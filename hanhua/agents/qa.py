"""QA Agent: 检查翻译质量和文件结构。

规则检查（不依赖模型）：
  缺失/空译文、拒答或解释性废话、残留英文、引号/括号/续行空格结构、格式符(%s/%d/$/换行)、
  长度异常、术语表一致性。
不合格的条目会带着问题说明退回翻译 Agent 重译，超过次数后标记为 review 供人工处理。
"""
import re

REFUSAL = re.compile(r'(抱歉|对不起，我|我无法|无法协助|作为(一个)?AI|I can\'?t|I cannot|as an AI|不能提供|不适当|敏感内容|译文[:：]|原文[:：])')
FMT = re.compile(r'%[-0-9.]*[sdfxc]|\$|\\n|\n')
LATIN_WORD = re.compile(r'[A-Za-z]{2,}')


def structure_issues(src, dst):
    issues = []
    if src.startswith('"') and not dst.lstrip().startswith('"'):
        issues.append('原文以英文双引号"开头，译文也必须以"开头')
    if src.rstrip().endswith('"') and not dst.rstrip().endswith('"'):
        issues.append('原文以"结尾，译文也必须以"结尾')
    if not src.startswith('"') and dst.startswith('"') and not src.startswith(' '):
        issues.append('原文没有引号，译文不要加"')
    if src.startswith('(') and not dst.lstrip().startswith('('):
        issues.append('原文以半角(开头，译文也要以(开头')
    if src.rstrip().endswith(')') and not dst.rstrip().endswith(')') and src.startswith('('):
        issues.append('原文以)结尾，译文也要以)结尾')
    if src.startswith(' ') and not dst.startswith(' '):
        issues.append('原文开头有一个空格（续行），译文开头也必须保留一个半角空格')
    if sorted(FMT.findall(src)) != sorted(FMT.findall(dst)):
        issues.append('格式符号不一致（%%s、%%d、$、换行必须原样保留）：原文 %s' % ' '.join(FMT.findall(src)))
    return issues


class QAAgent:
    name = 'QA'

    def __init__(self, ws, db, cfg, glossary):
        self.ws, self.db, self.cfg, self.glossary = ws, db, cfg, glossary
        q = cfg['qa']
        self.allowed = set(q['allowed_latin'])
        self.max_ratio, self.min_ratio = q['max_len_ratio'], q['min_len_ratio']
        self.max_attempts = cfg['translation']['max_attempts']
        # glossary terms that appear as whole words
        self.gloss_re = [(re.compile(r'\b%s\b' % re.escape(k)), k, v) for k, v in glossary.items() if len(k) >= 3]

    def check(self, row):
        src, dst = row['source'], row['target']
        if dst is None or not dst.strip():
            return ['译文为空']
        issues = []
        if REFUSAL.search(dst):
            issues.append('译文包含拒答或说明文字，只输出译文本身')
        issues += structure_issues(src, dst)
        leftovers = [w for w in LATIN_WORD.findall(dst) if w not in self.allowed and w.upper() != w]
        if leftovers and row['kind'] != 'name':
            issues.append('残留英文未翻译：%s' % ' '.join(leftovers[:5]))
        s_len = len(re.sub(r'\s', '', src))
        d_len = len(re.sub(r'\s', '', dst))
        if s_len >= 20:
            ratio = d_len / max(1, s_len)
            if ratio > self.max_ratio:
                issues.append('译文过长，可能加入了原文没有的内容')
            elif ratio < self.min_ratio:
                issues.append('译文过短，可能漏译')
        for rx, k, v in self.gloss_re:
            if rx.search(src) and v not in dst:
                # names embedded in possessives etc. are still expected to use the glossary form
                issues.append('术语不一致：%s 应译为“%s”' % (k, v))
                break
        return issues

    def run(self, keys=None):
        if keys is None:
            rows = self.db.q("SELECT * FROM units WHERE status='translated' AND skip=0")
        else:
            rows = [self.db.one('SELECT * FROM units WHERE key=?', (k,)) for k in keys]
        ok, fail, review = [], [], []
        for r in rows:
            if r is None or r['locked']:
                continue
            issues = self.check(r)
            if not issues:
                ok.append((r['key'],))
            elif r['attempts'] >= self.max_attempts:
                review.append(('；'.join(issues), r['key']))
            else:
                fail.append(('；'.join(issues), r['key']))
        self.db.execmany("UPDATE units SET status='qa_ok', qa_notes=NULL WHERE key=?", ok)
        self.db.execmany("UPDATE units SET status='qa_fail', qa_notes=? WHERE key=?", fail)
        self.db.execmany("UPDATE units SET status='review', qa_notes=? WHERE key=?", review)
        if rows:
            self.ws.log(self.name, '检查 %d 条：通过 %d，退回重译 %d，需人工复核 %d' % (len(rows), len(ok), len(fail), len(review)))
        return len(ok), len(fail), len(review)

    def audit_locked(self):
        """Structure check for imported/locked translations (report only)."""
        bad = []
        for r in self.db.q("SELECT * FROM units WHERE locked=1 AND skip=0 AND kind='message'"):
            iss = structure_issues(r['source'], r['target'] or '')
            if iss:
                bad.append((r['key'], iss))
        return bad
