"""Extraction Agent: 定位游戏文本、解析资源文件，写入任务数据库。"""
import json
import os

from ..common import read_json


class ExtractionAgent:
    name = 'Extraction'

    def __init__(self, ws, db, engine):
        self.ws, self.db, self.engine = ws, db, engine

    def run(self, force=False):
        if self.db.get('extracted') and not force:
            self.ws.log(self.name, '文本已提取，跳过（使用 --force 重新提取）')
            return
        game_dir = self.ws.meta['game_dir']
        self.engine.backup(game_dir)
        units, scenes = self.engine.extract()
        existing = {r['key']: r for r in self.db.q('SELECT key, target, status, origin, locked FROM units')}
        rows = []
        n_auto = 0
        for u in units:
            old = existing.get(u['key'])
            target, status, origin, locked = None, 'new', None, 0
            if old and old['target']:
                target, status, origin, locked = old['target'], old['status'], old['origin'], old['locked']
            elif not u['skip']:
                auto = self.engine.auto_translate(u['source'])
                if auto is not None:
                    target, status, origin, locked = auto, 'qa_ok', 'auto', 1
                    n_auto += 1
            rows.append((u['key'], u['kind'], u['seq'], u['scene'], u['speaker'], u['source'],
                         target, status, origin, locked, u['skip']))
        self.db.execmany('INSERT OR REPLACE INTO units(key,kind,seq,scene,speaker,source,target,status,origin,locked,skip)'
                         ' VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
        # drop units that the (possibly updated) extractor no longer produces
        keep = {u['key'] for u in units}
        stale = [(k,) for k in existing if k not in keep]
        if stale:
            self.db.execmany('DELETE FROM units WHERE key=?', stale)
        for sc in scenes:
            self.db.exec('INSERT OR IGNORE INTO scenes(scene, first_seq, n_units) VALUES(?,?,?)',
                         (sc['scene'], sc['first_seq'], sc['n_units']))
        self.db.set('extracted', '1')
        kinds = {}
        for u in units:
            kinds[u['kind']] = kinds.get(u['kind'], 0) + 1
        self.ws.log(self.name, '提取完成：%s；场景 %d 个；自动处理纯标点 %d 条；跳过 %d 条' % (
            '，'.join('%s %d' % (k, v) for k, v in kinds.items()), len(scenes), n_auto,
            sum(u['skip'] for u in units)))
        self.import_existing()

    def import_existing(self):
        """Import finished translations from projects/<name>/import/*.json ({key: text}); they are locked."""
        folder = self.ws.p('import')
        total = 0
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith('.json') or fn == 'glossary.json':
                continue
            data = read_json(os.path.join(folder, fn), {})
            rows = [(v, k) for k, v in data.items() if isinstance(v, str)]
            self.db.execmany("UPDATE units SET target=?, status='qa_ok', origin='import', locked=1 WHERE key=?", rows)
            total += len(rows)
        if total:
            self.ws.log(self.name, '导入已有译文 %d 条（锁定，不会被覆盖）' % total)
