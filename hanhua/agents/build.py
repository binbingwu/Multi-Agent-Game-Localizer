"""Build & Validation: 重新生成游戏资源、校验并测试汉化补丁。"""
import os
import subprocess
import time

from ..common import TOOLS


class BuildAgent:
    name = 'Build'

    def __init__(self, ws, db, engine):
        self.ws, self.db, self.engine = ws, db, engine

    def collect(self, include_unreviewed=True):
        statuses = ("'qa_ok','translated','review','qa_fail'" if include_unreviewed else "'qa_ok'")
        rows = self.db.q("SELECT key, target FROM units WHERE skip=0 AND target IS NOT NULL AND target != '' "
                         "AND status IN (%s)" % statuses)
        return {r['key']: r['target'] for r in rows}

    def build(self):
        trans = self.collect()
        self.ws.log(self.name, '打包 %d 条译文' % len(trans))
        self.engine.build(trans, log=self.ws.log)
        problems = self.engine.validate(trans, log=self.ws.log)
        for p in problems[:20]:
            self.ws.log('Validate', p)
        self.db.set('last_build', time.strftime('%Y-%m-%d %H:%M:%S'))
        return problems

    def install(self):
        self.engine.install(self.ws.meta['game_dir'], log=self.ws.log)

    def uninstall(self):
        self.engine.uninstall(self.ws.meta['game_dir'], log=self.ws.log)

    def smoke_test(self, seconds=15):
        """Launch the game, wait, take a screenshot of its window, then close it."""
        meta = self.ws.meta
        exe = os.path.join(meta['game_dir'], os.path.splitext(meta['files']['ain'])[0] + '.exe')
        if self.engine.game_running():
            raise RuntimeError('游戏已在运行，跳过冒烟测试')
        proc = subprocess.Popen([exe], cwd=meta['game_dir'])
        time.sleep(seconds)
        shot = self.ws.p('logs', 'smoke_%s.png' % time.strftime('%Y%m%d_%H%M%S'))
        r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                            os.path.join(TOOLS, 'gui.ps1'), 'shot', '-out', shot], capture_output=True, text=True)
        alive = proc.poll() is None
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/F'], capture_output=True)
        self.ws.log(self.name, '冒烟测试：游戏%s，截图 %s' % ('正常运行' if alive else '已退出（可能崩溃）', shot if os.path.exists(shot) else '失败'))
        return alive, shot
