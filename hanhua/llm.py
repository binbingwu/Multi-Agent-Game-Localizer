"""Local LLM runtime: manages a llama.cpp server bundled in runtime/ and talks to it
through its OpenAI-compatible HTTP API (no external services)."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from .common import load_config, rpath, ROOT


class LocalLLM:
    def __init__(self, cfg=None, log=print):
        self.cfg = (cfg or load_config())['llm']
        self.base = 'http://%s:%d' % (self.cfg['host'], self.cfg['port'])
        self.proc = None
        self.log = log

    # ---------- server lifecycle ----------
    def healthy(self):
        try:
            with urllib.request.urlopen(self.base + '/health', timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def start(self):
        if self.healthy():
            return
        exe = rpath(self.cfg['server_exe'])
        model = rpath(self.cfg['model'])
        if not os.path.exists(exe):
            raise RuntimeError('找不到推理引擎: %s （请运行 setup 下载）' % exe)
        if not os.path.exists(model):
            raise RuntimeError('找不到模型文件: %s （请运行 setup 下载）' % model)
        args = [exe, '-m', model, '--host', self.cfg['host'], '--port', str(self.cfg['port']),
                '-c', str(self.cfg['ctx_size']), '-np', str(self.cfg['parallel']),
                '-ngl', str(self.cfg['gpu_layers']), '--jinja']
        args += self.cfg.get('extra_args', [])
        logf = open(os.path.join(ROOT, 'runtime', 'llama-server.log'), 'a', encoding='utf-8')
        flags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
        self.log('LLM', '启动本地模型服务: %s' % os.path.basename(model))
        self.proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                                     cwd=os.path.dirname(exe), creationflags=flags)
        t0 = time.time()
        while time.time() - t0 < 300:
            if self.proc.poll() is not None:
                raise RuntimeError('模型服务启动失败，请查看 runtime/llama-server.log')
            if self.healthy():
                self.log('LLM', '模型服务就绪 (%.0fs)' % (time.time() - t0))
                return
            time.sleep(1)
        raise RuntimeError('模型服务启动超时')

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except Exception:
                self.proc.kill()
        self.proc = None

    # ---------- chat ----------
    def chat(self, system, user, temperature=None, max_tokens=None, json_mode=False):
        body = {
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'temperature': self.cfg['temperature'] if temperature is None else temperature,
            'top_p': self.cfg.get('top_p', 0.9),
            'max_tokens': max_tokens or self.cfg['max_tokens'],
            'stream': False,
            # Qwen3.x: disable the thinking phase, we want direct answers
            'chat_template_kwargs': {'enable_thinking': False},
        }
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        data = json.dumps(body).encode('utf-8')
        last = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(self.base + '/v1/chat/completions', data=data,
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=self.cfg['request_timeout']) as r:
                    resp = json.loads(r.read().decode('utf-8'))
                text = resp['choices'][0]['message'].get('content') or ''
                return strip_think(text)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                time.sleep(2 + attempt * 3)
        raise RuntimeError('LLM 请求失败: %s' % last)


def strip_think(text):
    if '</think>' in text:
        text = text.split('</think>', 1)[1]
    return text.strip()


def extract_json(text):
    """Pull the first JSON object/array out of a model reply."""
    text = text.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.startswith('json'):
            text = text[4:]
    for opener, closer in (('{', '}'), ('[', ']')):
        i = text.find(opener)
        j = text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError('no json in reply')
