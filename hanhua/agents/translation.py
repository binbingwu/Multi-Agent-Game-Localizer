"""Translation Agents: 并行批量翻译游戏文本。

每个批次是同一场景里连续的若干行，提示词里带上：翻译策略、场景概要、相关术语、前文译文，
以及 QA 退回时的问题说明。多个 worker 同时向本地模型服务发请求（llama.cpp 多槽并行）。
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..common import read_json

SYSTEM_TMPL = """你是资深的游戏汉化译者，正在把游戏《{title}》从{src}翻译成{dst}。
这是已正式发行的商业游戏，请忠实、完整、自然地翻译所有内容（包括面向成年玩家的情节），不删减、不弱化、不说教、不加任何解释。

【翻译策略】
{strategy}

【格式规则（必须遵守）】
1. 每行输入格式为「编号|[说话人] 原文」，你只输出「编号|译文」，行数与编号必须一一对应，不要输出说话人标签。
2. 原文的英文双引号 " 和半角括号 ( ) 原样保留在相同位置（引号包住台词，括号表示内心独白）。
3. 原文以一个空格开头的行（续行），译文开头也保留一个半角空格。
4. %s %d $ 等格式符号、⏎（换行）原样保留。
5. 使用中文全角标点：，。！？：；……（省略号用……），破折号用――。
6. 人名、地名等专有名词严格使用术语表中的译名。
7. 拟声词、呻吟、语气词要译成自然的中文拟声（如 啊、嗯、呜、哈啊）。"""


class TranslationAgents:
    name = 'Translation'

    def __init__(self, ws, db, llm, cfg, glossary, strategy):
        self.ws, self.db, self.llm, self.cfg = ws, db, llm, cfg
        self.glossary = glossary
        self.tcfg = cfg['translation']
        self.system = SYSTEM_TMPL.format(title=ws.meta.get('title', ws.name), src=self.tcfg['source_lang'],
                                         dst=self.tcfg['target_lang'], strategy=strategy.strip() or '（无）')
        # longest keys first so "Riche Eden" beats "Riche"
        self.gkeys = sorted(glossary.keys(), key=len, reverse=True)
        self.speakers = read_json(ws.p('speakers.json'), {}) or {}
        self.done = 0
        self.lock = threading.Lock()

    def spk(self, s):
        if not s:
            return None
        return self.speakers.get(s) or self.glossary.get(s) or s

    # ------------------------------------------------------------ batching
    def make_batches(self, kinds=('message', 'string')):
        rows = self.db.q("SELECT * FROM units WHERE status IN ('new','qa_fail') AND skip=0 AND locked=0 "
                         "AND kind IN (%s) ORDER BY seq" % ','.join('?' * len(kinds)), kinds)
        size = self.tcfg['batch_size']
        batches = []
        # dialogue: consecutive lines of one scene; UI strings: any order, bigger batches
        for kind, group_by_scene, bsize in (('message', True, size), ('string', False, size * 2)):
            cur = []
            for r in rows:
                if r['kind'] != kind:
                    continue
                if cur and (len(cur) >= bsize or (group_by_scene and r['scene'] != cur[-1]['scene'])):
                    batches.append(cur)
                    cur = []
                cur.append(r)
            if cur:
                batches.append(cur)
        return batches

    def glossary_for(self, text):
        hits = {}
        for k in self.gkeys:
            if len(k) >= 2 and re.search(r'\b%s\b' % re.escape(k), text):
                hits[k] = self.glossary[k]
        return hits

    def build_prompt(self, batch):
        first = batch[0]
        parts = []
        sc = self.db.one('SELECT summary FROM scenes WHERE scene=?', (first['scene'],))
        if sc and sc['summary']:
            parts.append('【场景概要】\n' + sc['summary'])
        text = ' '.join(r['source'] for r in batch)
        prev = self.db.q("SELECT speaker, source, target FROM units WHERE scene=? AND seq<? AND target IS NOT NULL "
                         "AND kind='message' ORDER BY seq DESC LIMIT ?", (first['scene'], first['seq'], self.tcfg['context_lines']))
        text += ' ' + ' '.join(p['source'] for p in prev)
        g = self.glossary_for(text)
        if g:
            parts.append('【术语表】\n' + '\n'.join('%s = %s' % kv for kv in g.items()))
        if prev:
            parts.append('【前文（仅供参考，不要翻译）】\n' + '\n'.join(
                '%s%s\n=> %s' % ('[%s] ' % self.spk(p['speaker']) if p['speaker'] else '', p['source'], p['target'])
                for p in reversed(prev)))
        notes = [(i + 1, r['qa_notes']) for i, r in enumerate(batch) if r['qa_notes']]
        if notes:
            parts.append('【上次译文的问题，这次必须改正】\n' + '\n'.join('%d: %s' % n for n in notes))
        lines = []
        for i, r in enumerate(batch):
            spk = '[%s] ' % self.spk(r['speaker']) if r['speaker'] else ''
            if r['kind'] == 'string':
                spk = '[界面文字] '
            lines.append('%d|%s%s' % (i + 1, spk, r['source'].replace('\n', '⏎')))
        parts.append('【待翻译】（共 %d 行）\n%s' % (len(batch), '\n'.join(lines)))
        return '\n\n'.join(parts)

    @staticmethod
    def parse(reply, n):
        out = {}
        for line in reply.split('\n'):
            m = re.match(r'\s*(\d+)\s*[|｜]\s?(.*)$', line)
            if m:
                i = int(m.group(1))
                if 1 <= i <= n and i not in out:
                    out[i] = m.group(2).rstrip('\r')
        return out

    @staticmethod
    def fix_format(src, dst):
        dst = dst.replace('⏎', '\n')
        # model often drops the leading continuation space or turns quotes into curly ones
        dst = dst.replace('“', '"').replace('”', '"').replace('（', '(').replace('）', ')')
        dst = dst.replace('「', '"').replace('」', '"').replace('『', '"').replace('』', '"')
        dst = re.sub(r'^\s*\[[^\]]{1,30}\]\s*', '', dst)
        dst = dst.replace('...', '……').replace('—', '―')
        # copy the source's leading/trailing whitespace (continuation space, trailing \n, padding) exactly
        core = dst.strip(' \n\t　')
        head = src[:len(src) - len(src.lstrip(' \n\t'))]
        tail = src[len(src.rstrip(' \n\t')):]
        return head + core + tail

    def translate_batch(self, batch):
        prompt = self.build_prompt(batch)
        res = {}
        for attempt in range(2):
            reply = self.llm.chat(self.system, prompt, max_tokens=min(4096, 200 + 120 * len(batch)))
            res = self.parse(reply, len(batch))
            if len(res) == len(batch):
                break
        rows = []
        for i, r in enumerate(batch):
            if i + 1 in res:
                rows.append((self.fix_format(r['source'], res[i + 1]), r['key']))
        self.db.execmany("UPDATE units SET target=?, status='translated', origin='llm', attempts=attempts+1 WHERE key=?", rows)
        missing = [r['key'] for i, r in enumerate(batch) if i + 1 not in res]
        if missing:
            self.db.execmany("UPDATE units SET attempts=attempts+1, qa_notes='上次漏译了这一行' WHERE key=?",
                             [(k,) for k in missing])
        return [r['key'] for r in batch]

    def run(self, qa=None, limit=None, kinds=('message', 'string')):
        batches = self.make_batches(kinds)
        if limit:
            batches = batches[:limit]
        total = sum(len(b) for b in batches)
        if not batches:
            return 0
        self.ws.log(self.name, '本轮待翻译 %d 条，分 %d 批，%d 个并行 worker' % (total, len(batches), self.tcfg['workers']))
        done = 0
        with ThreadPoolExecutor(self.tcfg['workers']) as ex:
            futs = [ex.submit(self.translate_batch, b) for b in batches]
            for f in as_completed(futs):
                try:
                    keys = f.result()
                except Exception as e:
                    self.ws.log(self.name, '批次失败：%s' % e)
                    continue
                if qa:
                    qa.run(keys)
                done += len(keys)
                if done % 400 < len(keys):
                    self.ws.log(self.name, '进度 %d/%d' % (done, total))
        return total
