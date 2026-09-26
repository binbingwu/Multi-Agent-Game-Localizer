"""SQLite store for translation units, scenes and run state.

unit.status: new -> translated -> (qa_ok | qa_fail) ; locked units are never overwritten.
unit.origin: llm | import | manual | auto
"""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    key TEXT PRIMARY KEY,          -- e.g. m1234 / s567
    kind TEXT NOT NULL,            -- message | string | name
    seq INTEGER NOT NULL,          -- order in the game script
    scene TEXT,                    -- function / scene name
    speaker TEXT,                  -- speaker (source language) if known
    source TEXT NOT NULL,          -- unescaped source text
    target TEXT,                   -- translation
    status TEXT NOT NULL DEFAULT 'new',
    origin TEXT,
    locked INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    qa_notes TEXT,
    skip INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_units_status ON units(status);
CREATE INDEX IF NOT EXISTS idx_units_seq ON units(seq);
CREATE TABLE IF NOT EXISTS scenes (
    scene TEXT PRIMARY KEY,
    first_seq INTEGER,
    n_units INTEGER,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class DB:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self):
        c = getattr(self._local, 'c', None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=60)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA journal_mode=WAL')
            self._local.c = c
        return c

    def q(self, sql, args=()):
        return self.conn().execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.conn().execute(sql, args).fetchone()
        return r

    def exec(self, sql, args=()):
        with self._write_lock:
            c = self.conn()
            c.execute(sql, args)
            c.commit()

    def execmany(self, sql, rows):
        with self._write_lock:
            c = self.conn()
            c.executemany(sql, rows)
            c.commit()

    def get(self, k, default=None):
        r = self.one('SELECT v FROM kv WHERE k=?', (k,))
        return r['v'] if r else default

    def set(self, k, v):
        self.exec('INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)', (k, v))

    def stats(self):
        rows = self.q("SELECT kind, status, skip, COUNT(*) n FROM units GROUP BY kind, status, skip")
        out = {}
        for r in rows:
            kind = r['kind']
            d = out.setdefault(kind, {})
            st = 'skip' if r['skip'] else r['status']
            d[st] = d.get(st, 0) + r['n']
        return out
