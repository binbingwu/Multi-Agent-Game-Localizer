"""QA Agent: 检查翻译质量和文件结构 / checks translation quality and file structure.

Rule-based (no model needed), language-aware:
  empty/missing, refusals or commentary, untranslated source words (when the target uses a different
  script), identical-to-source lines, quote/parenthesis/continuation structure, format codes
  (%s %d $ line breaks), length outliers, glossary consistency.
Failed lines go back to the Translation Agents with the problem description; after max_attempts
they are marked `review` for a human (or Claude) to fix.
Problem notes are written in English because they are fed back into the model prompt.
"""
import re

from ..langs import get_lang

REFUSAL = re.compile(r'(抱歉|对不起，我|我无法|无法协助|作为(一个)?AI|不能提供|不适当|敏感内容|译文[:：]|原文[:：]|'
                     r"I can'?t|I cannot|I'm sorry|as an AI|I apologi[sz]e|Translation:|Note:)", re.I)
# stricter pattern for targets where "I'm sorry" etc. can be a legitimate translation
REFUSAL_STRICT = re.compile(r"(as an AI|I (?:can(?:no|')t|am unable to) (?:assist|help|translate|provide|comply)|"
                            r"^(?:Translation|Note)\s*:)", re.I)
FMT = re.compile(r'%[-0-9.]*[sdfxc]|\$|\\n|\n')
LATIN_WORD = re.compile(r'[A-Za-z]{2,}')


def structure_issues(src, dst):
    issues = []
    if src.startswith('"') and not dst.lstrip().startswith('"'):
        issues.append('source starts with an ASCII double quote ", the translation must too')
    if src.rstrip().endswith('"') and not dst.rstrip().endswith('"'):
        issues.append('source ends with ", the translation must too')
    if not src.startswith('"') and dst.startswith('"') and not src.startswith(' '):
        issues.append('source has no leading quote, do not add one')
    if src.startswith('(') and not dst.lstrip().startswith('('):
        issues.append('source starts with an ASCII (, the translation must too')
    if src.rstrip().endswith(')') and not dst.rstrip().endswith(')') and src.startswith('('):
        issues.append('source ends with ), the translation must too')
    if src.startswith(' ') and not dst.startswith(' '):
        issues.append('source starts with one space (continuation line), keep one ASCII space at the start')
    if sorted(FMT.findall(src)) != sorted(FMT.findall(dst)):
        issues.append('format codes differ (%%s, %%d, $ and line breaks must be kept): source has %s'
                      % ' '.join(repr(x) for x in FMT.findall(src)))
    return issues


class QAAgent:
    name = 'QA'

    def __init__(self, ws, db, cfg, glossary, src='en', tgt='zh-CN'):
        self.ws, self.db, self.cfg, self.glossary = ws, db, cfg, glossary
        self.src, self.tgt = get_lang(src), get_lang(tgt)
        q = cfg['qa']
        self.allowed = set(q['allowed_latin'])
        self.min_ratio, self.max_ratio = self.tgt.ratio
        if self.tgt.code in ('zh-CN',):
            # config values tuned on Chinese take precedence for Chinese
            self.min_ratio, self.max_ratio = q['min_len_ratio'], q['max_len_ratio']
        self.max_attempts = cfg['translation']['max_attempts']
        self.check_leftover = self.tgt.non_latin and self.src.script == 'latin'
        self.refusal = REFUSAL if self.tgt.non_latin or self.tgt.dense else REFUSAL_STRICT
        self.gloss_re = [(re.compile(r'\b%s\b' % re.escape(k)), k, v) for k, v in glossary.items() if len(k) >= 3]

    def check(self, row):
        src, dst = row['source'], row['target']
        if dst is None or not dst.strip():
            return ['translation is empty']
        issues = []
        if self.refusal.search(dst) and not self.refusal.search(src):
            issues.append('output contains a refusal or commentary; output the translation only')
        issues += structure_issues(src, dst)
        if self.check_leftover and row['kind'] != 'name':
            leftovers = [w for w in LATIN_WORD.findall(dst) if w not in self.allowed and w.upper() != w]
            if leftovers:
                issues.append('untranslated source words left: %s' % ' '.join(leftovers[:5]))
        s_core, d_core = re.sub(r'\s', '', src), re.sub(r'\s', '', dst)
        if len(s_core) >= 12 and s_core == d_core and self.src.code != self.tgt.code:
            issues.append('line was left untranslated')
        if len(s_core) >= 20:
            ratio = len(d_core) / max(1, len(s_core))
            if ratio > self.max_ratio:
                issues.append('translation is much longer than the source; do not add content')
            elif ratio < self.min_ratio:
                issues.append('translation is much shorter than the source; something may be missing')
        for rx, k, v in self.gloss_re:
            if rx.search(src) and v not in dst:
                issues.append('glossary: "%s" must be translated as "%s"' % (k, v))
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
                review.append(('; '.join(issues), r['key']))
            else:
                fail.append(('; '.join(issues), r['key']))
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
