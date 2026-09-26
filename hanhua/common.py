"""Shared paths, config, logging and the workspace object."""
import json
import os
import sys
import time
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS = os.path.join(ROOT, 'projects')
TOOLS = os.path.join(ROOT, 'tools')

_log_lock = threading.Lock()


def load_config():
    with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as f:
        return json.load(f)


def rpath(p):
    """Resolve a path relative to the project root."""
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Workspace:
    """A per-game working directory under projects/<name>."""

    def __init__(self, name):
        self.name = name
        self.dir = os.path.join(PROJECTS, name)
        os.makedirs(self.dir, exist_ok=True)
        for sub in ('original', 'extract', 'build', 'logs', 'import'):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
        self.project_file = os.path.join(self.dir, 'project.json')
        self.db_path = os.path.join(self.dir, 'state.db')
        self.glossary_file = os.path.join(self.dir, 'glossary.json')
        self.strategy_file = os.path.join(self.dir, 'strategy.md')
        self.log_file = os.path.join(self.dir, 'logs', 'run.log')

    def p(self, *parts):
        return os.path.join(self.dir, *parts)

    @property
    def meta(self):
        return read_json(self.project_file, {})

    def save_meta(self, meta):
        write_json(self.project_file, meta)

    def log(self, agent, msg):
        line = '%s [%s] %s' % (time.strftime('%H:%M:%S'), agent, msg)
        with _log_lock:
            try:
                print(line, flush=True)
            except UnicodeEncodeError:
                print(line.encode(sys.stdout.encoding or 'utf-8', 'replace').decode(sys.stdout.encoding or 'utf-8'), flush=True)
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(line + '\n')


def list_projects():
    if not os.path.isdir(PROJECTS):
        return []
    return sorted(d for d in os.listdir(PROJECTS) if os.path.exists(os.path.join(PROJECTS, d, 'project.json')))
