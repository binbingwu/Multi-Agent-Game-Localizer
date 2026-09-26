"""Translation Agents: 并行批量翻译游戏文本。

每个批次是同一场景里连续的若干行，提示词里带上：翻译策略、场景概要、相关术语、前文译文，
以及 QA 退回时的问题说明。多个 worker 同时向本地模型服务发请求（llama.cpp 多槽并行）。
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..common import read_json

SYSTEM_TMPL = """You are a senior video game localizer translating the game "{title}" from {src} into {dst}.
It is a commercially released game. Translate everything faithfully, completely and naturally, including content
intended for adult players. Do not censor, soften, moralize, summarize or add explanations.

[Translation strategy]
{strategy}

[Output format - mandatory]
1. Each input line looks like "N|[speaker] source text". Output only "N|translation", one line per number, same count,
   without the speaker tag.
2. Keep the source's ASCII double quotes " and ASCII parentheses ( ) in the same positions
   (quotes wrap spoken lines, parentheses mark inner thoughts).
3. If a source line starts with one space (a continuation line), the translation must also start with one ASCII space.
4. Keep format codes such as %s %d $ and the line-break mark ⏎ exactly as they are.
5. Use the glossary translations for names and terms exactly.
{lang_rules}
Write the translations in {dst} only."""


class TranslationAgents:
    name = 'Translation'

    def __init__(self, ws, db, llm, cfg, glossary, strategy, src, tgt):
        self.ws, self.db, self.llm, self.cfg = ws, db, llm, cfg
        self.glossary = glossary
        self.tcfg = cfg['translation']
        self.src, self.tgt = src, tgt
        rules = ''.join('%d. %s\n' % (i + 6, r) for i, r in enumerate(tgt.rules))
        self.system = SYSTEM_TMPL.format(title=ws.meta.get('title', ws.name), src=src.label, dst=tgt.label,
                                         strategy=strategy.strip() or '(none)', lang_rules=rules)
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
            parts.append('[Scene summary]\n' + sc['summary'])
        text = ' '.join(r['source'] for r in batch)
        prev = self.db.q("SELECT speaker, source, target FROM units WHERE scene=? AND seq<? AND target IS NOT NULL "
                         "AND kind='message' ORDER BY seq DESC LIMIT ?", (first['scene'], first['seq'], self.tcfg['context_lines']))
        text += ' ' + ' '.join(p['source'] for p in prev)
        g = self.glossary_for(text)
        if g:
            parts.append('[Glossary]\n' + '\n'.join('%s = %s' % kv for kv in g.items()))
        if prev:
            parts.append('[Previous lines - context only, do not translate]\n' + '\n'.join(
                '%s%s\n=> %s' % ('[%s] ' % self.spk(p['speaker']) if p['speaker'] else '', p['source'], p['target'])
                for p in reversed(prev)))
        notes = [(i + 1, r['qa_notes']) for i, r in enumerate(batch) if r['qa_notes']]
        if notes:
            parts.append('[Problems found in your previous translation of these lines - fix them]\n' +
                         '\n'.join('%d: %s' % n for n in notes))
        lines = []
        for i, r in enumerate(batch):
            spk = '[%s] ' % self.spk(r['speaker']) if r['speaker'] else ''
            if r['kind'] == 'string':
                spk = '[UI text] '
            lines.append('%d|%s%s' % (i + 1, spk, r['source'].replace('\n', '⏎')))
        parts.append('[Translate] (%d lines)\n%s' % (len(batch), '\n'.join(lines)))
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
    def fix_format(src, dst, tgt=None):
        dst = dst.replace('⏎', '\n')
        # model often drops the leading continuation space or turns quotes into curly ones
        dst = dst.replace('“', '"').replace('”', '"').replace('（', '(').replace('）', ')')
        dst = dst.replace('「', '"').replace('」', '"').replace('『', '"').replace('』', '"')
        dst = re.sub(r'^\s*\[[^\]]{1,30}\]\s*', '', dst)
        if tgt is None or tgt.ellipsis == '……':
            dst = dst.replace('...', '……').replace('—', '―')
        # copy the source's leading/trailing whitespace (continuation space, trailing \n, padding) exactly
        core = dst.strip(' \n\t　')
        head = src[:len(src) - len(src.lstrip(' \n\t　'))]
        tail = src[len(src.rstrip(' \n\t　')):]
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
                rows.append((self.fix_format(r['source'], res[i + 1], self.tgt), r['key']))
        self.db.execmany("UPDATE units SET target=?, status='translated', origin='llm', attempts=attempts+1 WHERE key=?", rows)
        missing = [r['key'] for i, r in enumerate(batch) if i + 1 not in res]
        if missing:
            self.db.execmany("UPDATE units SET attempts=attempts+1, qa_notes='line was missing from your output' WHERE key=?",
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
