"""下载并检查本地运行环境：llama.cpp (CUDA) 推理引擎 + GGUF 模型。全部放在项目目录内。"""
import os
import shutil
import urllib.request
import zipfile

from .common import ROOT, load_config, rpath

LLAMA_TAG = 'b11192'
LLAMA_ZIPS = [
    'https://github.com/ggml-org/llama.cpp/releases/download/{tag}/llama-{tag}-bin-win-cuda-12.4-x64.zip',
    'https://github.com/ggml-org/llama.cpp/releases/download/{tag}/cudart-llama-bin-win-cuda-12.4-x64.zip',
]
ALICE_TAG = '0.13.0'
ALICE_URL = 'https://github.com/nunuhara/alice-tools/releases/download/{tag}/alice-tools-{tag}.zip'
MODEL_URLS = {
    'Qwen3.5-9B-Q6_K.gguf': ['https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q6_K.gguf',
                             'https://hf-mirror.com/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q6_K.gguf'],
}


def download(url, dst):
    print('下载 %s' % url)
    tmp = dst + '.part'
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, 'wb') as f:
        total = int(r.headers.get('Content-Length', 0))
        got = 0
        while True:
            chunk = r.read(1 << 22)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print('\r  %.1f / %.1f MB' % (got / 1e6, total / 1e6), end='', flush=True)
    print()
    os.replace(tmp, dst)


def setup():
    cfg = load_config()['llm']
    dl = os.path.join(ROOT, 'downloads')
    os.makedirs(dl, exist_ok=True)
    exe = rpath(cfg['server_exe'])
    rt = os.path.dirname(exe)
    if not os.path.exists(exe):
        os.makedirs(rt, exist_ok=True)
        for u in LLAMA_ZIPS:
            u = u.format(tag=LLAMA_TAG)
            z = os.path.join(dl, os.path.basename(u))
            if not os.path.exists(z):
                download(u, z)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(rt)
        print('推理引擎已就绪：%s' % exe)
    else:
        print('推理引擎已存在：%s' % exe)
    alice = os.path.join(ROOT, 'tools', 'alice-tools', 'alice.exe')
    if not os.path.exists(alice):
        z = os.path.join(dl, 'alice-tools-%s.zip' % ALICE_TAG)
        if not os.path.exists(z):
            download(ALICE_URL.format(tag=ALICE_TAG), z)
        tmp = os.path.join(dl, 'alice-tools-extract')
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp)
        src = os.path.join(tmp, 'alice-tools-%s' % ALICE_TAG)
        shutil.rmtree(os.path.dirname(alice), ignore_errors=True)
        shutil.move(src, os.path.dirname(alice))
        print('System 4 工具已就绪：%s' % alice)
    model = rpath(cfg['model'])
    if not os.path.exists(model):
        os.makedirs(os.path.dirname(model), exist_ok=True)
        urls = MODEL_URLS.get(os.path.basename(model))
        if not urls:
            print('请手动把模型放到 %s' % model)
            return 1
        for u in urls:
            try:
                download(u, model)
                break
            except Exception as e:
                print('失败：%s，尝试镜像' % e)
        print('模型已就绪：%s' % model)
    else:
        print('模型已存在：%s' % model)
    return 0
