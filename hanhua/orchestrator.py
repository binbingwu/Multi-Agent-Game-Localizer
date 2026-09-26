"""Orchestrator: 扫描项目、分析任务、制定翻译策略，并统一调度各 Agent。"""
import os
import random

from .common import Workspace, load_config, read_json
from .langs import get_lang
from .db import DB
from .engines import detect_engine, get_engine
from .llm import LocalLLM
from .agents.extraction import ExtractionAgent
from .agents.context import ContextAgent
from .agents.translation import TranslationAgents
from .agents.qa import QAAgent
from .agents.build import BuildAgent

STRATEGY_SYSTEM = """You lead a game localization project from {src} into {dst}. From the project scan, the main
name translations and the text sample, write a concise translation strategy for the translators, in {dst}.
Cover: genre and overall style, narration vs. dialogue tone, how each main character speaks, forms of address,
onomatopoeia, UI text style. If the game contains adult content it must be translated faithfully and explicitly;
never ask for it to be cut, softened or "handled tastefully". Use the given name translations.
Bullet points, at most about 300 words. Output only the strategy."""

DEFAULT_STRATEGY = """- Convey the plot, tone and jokes faithfully; keep serious scenes serious.
- Narration in fluent written style; dialogue colloquial and true to each character.
- Keep puns and running gags where possible, without adding content.
- UI and item descriptions short and consistent."""


class Orchestrator:
    def __init__(self, name):
        self.cfg = load_config()
        self.ws = Workspace(name)
        self.db = DB(self.ws.db_path)
        meta = self.ws.meta
        self.engine = get_engine(meta['engine'])(self.ws, self.cfg) if meta.get('engine') else None
        self.llm = LocalLLM(self.cfg, log=self.ws.log)
        tc = self.cfg['translation']
        self.src = get_lang(meta.get('source_lang') or tc['source_lang'])
        self.tgt = get_lang(meta.get('target_lang') or tc['target_lang'])

    # ----------------------------------------------------------------- init
    @classmethod
    def init_project(cls, name, game_dir, title=None, src=None, tgt=None):
        eng = detect_engine(game_dir)
        if not eng:
            raise RuntimeError('无法识别游戏引擎：%s（目前支持：AliceSoft System 4）' % game_dir)
        ws = Workspace(name)
        meta = ws.meta
        cfg = load_config()['translation']
        meta.update({'name': name, 'title': title or name, 'game_dir': os.path.abspath(game_dir), 'engine': eng.name,
                     'source_lang': get_lang(src or cfg['source_lang']).code,
                     'target_lang': get_lang(tgt or cfg['target_lang']).code})
        ws.save_meta(meta)
        o = cls(name)
        o.engine.backup(game_dir)
        info = o.engine.scan(game_dir)
        meta = o.ws.meta
        meta['scan'] = info
        o.ws.save_meta(meta)
        o.ws.log('Orchestrator', '项目已创建：%s，引擎 %s，%s → %s，%s' % (name, eng.description, o.src.label, o.tgt.label, info))
        return o

    def scan_report(self):
        st = self.db.stats()
        lines = ['项目：%s  游戏目录：%s  语言：%s → %s' % (self.ws.name, self.ws.meta.get('game_dir'), self.src.label, self.tgt.label),
                 '引擎：%s' % self.ws.meta.get('scan')]
        for kind, d in st.items():
            total = sum(d.values())
            lines.append('  %-8s 共 %6d：%s' % (kind, total, '，'.join('%s %d' % kv for kv in sorted(d.items()))))
        return '\n'.join(lines)

    # ------------------------------------------------------------- strategy
    def make_strategy(self):
        if os.path.exists(self.ws.strategy_file):
            return open(self.ws.strategy_file, encoding='utf-8').read()
        rows = self.db.q("SELECT speaker, source FROM units WHERE kind='message' AND skip=0")
        sample = random.Random(7).sample(list(rows), min(60, len(rows)))
        text = '\n'.join(('[%s] ' % r['speaker'] if r['speaker'] else '') + r['source'] for r in sample)
        report = self.scan_report()
        g = read_json(self.ws.glossary_file, None) or read_json(self.ws.p('import', 'glossary.json'), {}) or {}
        names = '\n'.join('%s=%s' % kv for kv in list(g.items())[:60])
        try:
            s = self.llm.chat(STRATEGY_SYSTEM.format(src=self.src.label, dst=self.tgt.label),
                              '%s\n\n[Main name translations]\n%s\n\n[Text sample]\n%s' % (report, names, text),
                              temperature=0.3, max_tokens=1200)
        except Exception as e:
            self.ws.log('Orchestrator', '策略生成失败，使用默认策略：%s' % e)
            s = ''
        s = s.strip() or DEFAULT_STRATEGY
        with open(self.ws.strategy_file, 'w', encoding='utf-8') as f:
            f.write(s + '\n')
        self.ws.log('Orchestrator', '翻译策略已写入 %s（可手工修改）' % self.ws.strategy_file)
        return s

    # ------------------------------------------------------------- pipeline
    def run(self, stages=('extract', 'context', 'translate', 'build'), limit=None, install=False):
        log = self.ws.log
        if 'extract' in stages:
            ExtractionAgent(self.ws, self.db, self.engine, self.tgt).run()
        log('Orchestrator', '\n' + self.scan_report())
        need_llm = any(s in stages for s in ('context', 'translate'))
        try:
            if need_llm:
                self.llm.start()
            strategy = self.make_strategy() if need_llm else ''
            ctx = ContextAgent(self.ws, self.db, self.llm, self.cfg, self.src, self.tgt)
            if 'context' in stages:
                ctx.run()
            glossary = ctx.load_glossary()
            if 'translate' in stages:
                qa = QAAgent(self.ws, self.db, self.cfg, glossary, self.src, self.tgt)
                qa.run()  # pick up anything translated but not yet checked
                ta = TranslationAgents(self.ws, self.db, self.llm, self.cfg, glossary, strategy, self.src, self.tgt)
                for rnd in range(self.cfg['translation']['max_attempts'] + 1):
                    n = ta.run(qa=qa, limit=limit)
                    if not n or limit:
                        break
                    log('Orchestrator', '第 %d 轮完成，继续处理被 QA 退回的条目' % (rnd + 1))
        finally:
            if need_llm:
                self.llm.stop()
        if 'build' in stages:
            ba = BuildAgent(self.ws, self.db, self.engine)
            problems = ba.build()
            if install:
                if problems:
                    log('Orchestrator', '校验存在问题，仍按要求安装；请查看日志')
                ba.install()
        log('Orchestrator', '完成。\n' + self.scan_report())
