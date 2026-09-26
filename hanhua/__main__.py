"""命令行入口：python -m hanhua <命令>

  setup                         下载/检查本地推理引擎和模型
  init <名称> <游戏目录> [标题]  创建汉化项目（识别引擎、备份原文件、扫描）
  run <名称> [--limit N] [--install] [--stages extract,context,translate,build]
  status <名称>                 查看进度
  build <名称> [--install]      只打包（不调用模型）
  install / uninstall <名称>    安装补丁 / 恢复原版
  test <名称>                   冒烟测试：启动游戏截图后关闭
  export <名称> <文件.tsv>      导出需复核的条目（key、原文、译文、问题）
  import <名称> <文件.tsv>      导入人工修改后的条目（锁定）
  llm-test                      测试本地模型是否正常工作
"""
import csv
import os
import sys
import time

from .common import list_projects, ROOT


def main(argv):
    if not argv or argv[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    flags = {a for a in args if a.startswith('--')}

    def opt(name, default=None):
        if name in args:
            i = args.index(name)
            return args[i + 1]
        return default

    pos = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in ('--limit', '--stages'):
            skip_next = True
            continue
        if not a.startswith('--'):
            pos.append(a)

    if cmd == 'setup':
        from .setup import setup
        return setup()
    if cmd == 'llm-test':
        from .llm import LocalLLM
        llm = LocalLLM()
        llm.start()
        t = time.time()
        print(llm.chat('你是翻译。', '把这句翻译成中文，只输出译文：“Aster finally caught up with Ramius at the entrance to town.”'))
        print('耗时 %.1fs' % (time.time() - t))
        llm.stop()
        return 0

    from .orchestrator import Orchestrator
    if cmd == 'init':
        if len(pos) < 2:
            print('用法: init <名称> <游戏目录> [标题]')
            return 1
        Orchestrator.init_project(pos[0], pos[1], pos[2] if len(pos) > 2 else None)
        return 0
    if cmd == 'list':
        print('\n'.join(list_projects()) or '（还没有项目）')
        return 0
    if not pos:
        print('请指定项目名称。已有项目：%s' % ', '.join(list_projects()))
        return 1
    o = Orchestrator(pos[0])
    if cmd == 'run':
        stages = tuple((opt('--stages') or 'extract,context,translate,build').split(','))
        limit = int(opt('--limit')) if opt('--limit') else None
        if '--force' in flags:
            o.db.set('extracted', '')
        o.run(stages=stages, limit=limit, install='--install' in flags)
    elif cmd == 'status':
        print(o.scan_report())
        print('最近打包：%s' % o.db.get('last_build', '无'))
    elif cmd == 'build':
        o.run(stages=('build',), install='--install' in flags)
    elif cmd == 'install':
        from .agents.build import BuildAgent
        BuildAgent(o.ws, o.db, o.engine).install()
    elif cmd == 'uninstall':
        from .agents.build import BuildAgent
        BuildAgent(o.ws, o.db, o.engine).uninstall()
    elif cmd == 'test':
        from .agents.build import BuildAgent
        BuildAgent(o.ws, o.db, o.engine).smoke_test()
    elif cmd == 'export':
        path = pos[1] if len(pos) > 1 else o.ws.p('review.tsv')
        rows = o.db.q("SELECT key, speaker, source, target, qa_notes FROM units WHERE status IN ('review','qa_fail') ORDER BY seq")
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f, delimiter='\t')
            w.writerow(['key', 'speaker', 'source', 'target', 'problem'])
            for r in rows:
                w.writerow([r['key'], r['speaker'] or '', r['source'], r['target'] or '', r['qa_notes'] or ''])
        print('已导出 %d 条到 %s' % (len(rows), path))
    elif cmd == 'import':
        path = pos[1]
        n = 0
        with open(path, encoding='utf-8-sig', newline='') as f:
            for r in csv.DictReader(f, delimiter='\t'):
                if r.get('key') and r.get('target'):
                    o.db.exec("UPDATE units SET target=?, status='qa_ok', origin='manual', locked=1 WHERE key=?",
                              (r['target'], r['key']))
                    n += 1
        print('已导入 %d 条人工译文' % n)
    else:
        print('未知命令：%s' % cmd)
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]) or 0)
