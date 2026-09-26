"""Orchestrator: 扫描项目、分析任务、制定翻译策略，并统一调度各 Agent。"""
import os
import random

from .common import Workspace, load_config, read_json
from .db import DB
from .engines import detect_engine, get_engine
from .llm import LocalLLM
from .agents.extraction import ExtractionAgent
from .agents.context import ContextAgent
from .agents.translation import TranslationAgents
from .agents.qa import QAAgent
from .agents.build import BuildAgent

STRATEGY_SYSTEM = """你是游戏汉化项目的总负责人。根据给出的项目扫描结果、主要角色译名和文本样本，制定一份简明的翻译策略（简体中文），
供译者遵守。包括：作品类型与整体文风、叙述文和对白的语气、各主要角色的说话风格、称呼习惯、拟声词原则、UI文字风格。
这是面向成年玩家的作品：成人内容必须忠实、直白地翻译，不得要求删减、弱化或“含蓄处理”。
提到角色时必须使用给出的中文译名。用要点列出，不超过400字。直接输出策略正文。"""

DEFAULT_STRATEGY = """- 忠实传达原作的剧情、语气和笑点，严肃剧情处保持庄重。
- 叙述文用流畅书面语；对白口语化，符合各角色性格。
- 保留原文的双关、吐槽和语气，不要添油加醋；H场景用词直白自然。
- UI与道具说明简洁明了。"""


class Orchestrator:
    def __init__(self, name):
        self.cfg = load_config()
        self.ws = Workspace(name)
        self.db = DB(self.ws.db_path)
        meta = self.ws.meta
        self.engine = get_engine(meta['engine'])(self.ws, self.cfg) if meta.get('engine') else None
        self.llm = LocalLLM(self.cfg, log=self.ws.log)

    # ----------------------------------------------------------------- init
    @classmethod
    def init_project(cls, name, game_dir, title=None):
        eng = detect_engine(game_dir)
        if not eng:
            raise RuntimeError('无法识别游戏引擎：%s（目前支持：AliceSoft System 4）' % game_dir)
        ws = Workspace(name)
        meta = ws.meta
        meta.update({'name': name, 'title': title or name, 'game_dir': os.path.abspath(game_dir), 'engine': eng.name})
        ws.save_meta(meta)
        o = cls(name)
        o.engine.backup(game_dir)
        info = o.engine.scan(game_dir)
        meta = o.ws.meta
        meta['scan'] = info
        o.ws.save_meta(meta)
        o.ws.log('Orchestrator', '项目已创建：%s，引擎 %s，%s' % (name, eng.description, info))
        return o

    def scan_report(self):
        st = self.db.stats()
        lines = ['项目：%s  游戏目录：%s' % (self.ws.name, self.ws.meta.get('game_dir')),
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
            s = self.llm.chat(STRATEGY_SYSTEM, '%s\n\n【主要译名】\n%s\n\n【文本样本】\n%s' % (report, names, text),
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
            ExtractionAgent(self.ws, self.db, self.engine).run()
        log('Orchestrator', '\n' + self.scan_report())
        need_llm = any(s in stages for s in ('context', 'translate'))
        try:
            if need_llm:
                self.llm.start()
            strategy = self.make_strategy() if need_llm else ''
            ctx = ContextAgent(self.ws, self.db, self.llm, self.cfg)
            if 'context' in stages:
                ctx.run()
            glossary = ctx.load_glossary()
            if 'translate' in stages:
                qa = QAAgent(self.ws, self.db, self.cfg, glossary)
                qa.run()  # pick up anything translated but not yet checked
                ta = TranslationAgents(self.ws, self.db, self.llm, self.cfg, glossary, strategy)
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
