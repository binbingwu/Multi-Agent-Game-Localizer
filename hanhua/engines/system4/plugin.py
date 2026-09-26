"""AliceSoft System 4 engine plugin (Evenicle, Rance, ...).

Text lives in <game>.ain (messages m[] and strings s[]), glyphs in <game>Font.fnl.
Chinese is shown by mapping every Chinese character onto a Shift-JIS kanji code point
and drawing that glyph into the bitmap font. Verified on Evenicle (MangaGamer, Steam v1.04).
"""
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import zlib

from ..base import Engine
from ...common import TOOLS, read_json, write_json
from . import fnl as fnllib

ALICE = os.path.join(TOOLS, 'alice-tools', 'alice.exe')
JP_RE = re.compile(r'[\u3040-\u30ff\u4e00-\u9fff\uff66-\uff9f]')
NORMALIZE = {'—': '―', '“': '"', '”': '"', '·': '・', '•': '・', '‘': "'", '’': "'", '～': '～'}

# functions whose strings are engine/editor/debug internals
DENY_FN_PREFIX = ('CAE', 'CAS', 'CMapEditer', 'CMapPlacement', 'CDrawMovie', 'CADVEngine', 'CADVCallFunction',
                  'SYS_Init_Joystick', 'T_Battle@SetCameraSetting', 'Dbg', 'PG', 'CASGameNet')
DEBUG_WORDS = re.compile(r'(Failed to|[Ee]rror|plugin|Plugin|Internal|\.php|http|\.png|\.ain|\.dll|\.qnt|\.ogg|'
                         r'Tried to|[Ii]nvalid|[Uu]ndefined|out of range|[Ff]unction|void|Base Z|Area Number|range:|'
                         r'\[%[ds]\]|[Dd]ebug|[Ss]cenario|[Ss]ystem message|window %d)')


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            n = s[i + 1]
            out.append({'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(n, '\\' + n))
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def escape(s):
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t')


def is_display_string(text, fn):
    if not re.search(r'[A-Za-z]{2,}', text):
        return False
    if JP_RE.search(text):
        return False
    if fn and fn.startswith(DENY_FN_PREFIX):
        return False
    if DEBUG_WORDS.search(text):
        return False
    if '_' in text or text.startswith('#'):
        return False
    stripped = re.sub(r'%[-0-9.]*[sdfxc]', '', text).strip()
    if ' ' not in stripped and '$' not in stripped and '\n' not in stripped:
        # single token: accept only capitalised words ("Attack", "Buy", "Riche") or known short ordinals
        if not re.fullmatch(r"[A-Z][a-z'\-]+[!?.]?|[a-z]{2,8}", stripped):
            return False
        if re.fullmatch(r'[a-z]+', stripped) and fn not in ('Nth',):
            return False
    return True


class System4Engine(Engine):
    name = 'system4'
    description = 'AliceSoft System 4 (.ain + .fnl)'

    @classmethod
    def detect(cls, game_dir):
        return bool(glob.glob(os.path.join(game_dir, '*.ain'))) and os.path.exists(os.path.join(game_dir, 'AliceStart.ini'))

    # ------------------------------------------------------------------ files
    def _files(self, game_dir):
        ain = sorted(glob.glob(os.path.join(game_dir, '*.ain')))[0]
        base = os.path.splitext(os.path.basename(ain))[0]
        fnl = os.path.join(game_dir, base + 'Font.fnl')
        if not os.path.exists(fnl):
            cands = glob.glob(os.path.join(game_dir, '*.fnl'))
            fnl = cands[0] if cands else None
        return ain, fnl

    def scan(self, game_dir):
        ain, fnl = self._files(game_dir)
        info = {'engine': self.name, 'ain': os.path.basename(ain), 'fnl': os.path.basename(fnl) if fnl else None}
        r = subprocess.run([ALICE, 'ain', 'dump', '--ain-version', self.orig('ain') if os.path.exists(self.orig('ain')) else ain],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        info['ain_version'] = r.stdout.strip()
        ver = os.path.join(game_dir, 'Version.txt')
        if os.path.exists(ver):
            info['game_version'] = open(ver, encoding='utf-8', errors='replace').read().strip()
        return info

    def orig(self, which):
        meta = self.ws.meta
        return self.ws.p('original', meta['files'][which]) if 'files' in meta else self.ws.p('original', '?')

    def backup(self, game_dir):
        """Copy pristine game files into the workspace (once)."""
        ain, fnl = self._files(game_dir)
        meta = self.ws.meta
        files = {'ain': os.path.basename(ain), 'fnl': os.path.basename(fnl)}
        meta['files'] = files
        hashes = meta.get('orig_hash', {})
        for k, src in (('ain', ain), ('fnl', fnl)):
            dst = self.ws.p('original', files[k])
            if os.path.exists(dst):
                continue
            # prefer an earlier manual backup if the game dir already holds a patched file
            alt = os.path.join(game_dir, '_backup_original', files[k])
            use = alt if os.path.exists(alt) else src
            shutil.copy2(use, dst)
            hashes[k] = sha(dst)
        meta['orig_hash'] = hashes
        self.ws.save_meta(meta)

    # ---------------------------------------------------------------- extract
    def extract(self):
        dump = self.ws.p('extract', 'text_dump.txt')
        subprocess.run([ALICE, 'ain', 'dump', '-t', '-o', dump, self.orig('ain')], check=True,
                       capture_output=True)
        lines = open(dump, encoding='utf-8').read().split('\n')

        # pass 1: lookup tables (JP -> EN) used for speaker names
        jp2en = {}
        fn = None
        pending = None
        for line in lines:
            if line.startswith('; '):
                fn = line[2:]
                pending = None
                continue
            m = re.match(r';s\[(\d+)\] = "(.*)"$', line)
            if not m or fn != 'BuildHugeLookup':
                continue
            t = unescape(m.group(2))
            if JP_RE.search(t):
                pending = t
            elif pending is not None:
                jp2en[pending] = t
                pending = None

        units, scenes = [], {}
        seen_s = set()
        seq = 0
        fn = None
        speaker = None
        last_speaker = None
        name_fns = ('LookupName',)
        for line in lines:
            if line.startswith('; '):
                fn = line[2:]
                speaker = last_speaker = None
                continue
            m = re.match(r';([sm])\[(\d+)\] = "(.*)"$', line)
            if not m:
                continue
            kind, idx, raw = m.group(1), int(m.group(2)), m.group(3)
            text = unescape(raw)
            if kind == 's':
                # portrait keys: 顔／NAME／...  NPC keys: ROLE／NAME／／／FACE
                fm = re.match(r'顔／([^／]+)／', text) or re.match(r'[^／背立]{1,12}／([^／]+)／／／', text)
                if fm:
                    speaker = jp2en.get(fm.group(1), fm.group(1))
                if ('s%d' % idx) in seen_s:
                    continue
                seen_s.add('s%d' % idx)
                if fn in name_fns and is_display_string(text, fn):
                    units.append(dict(key='s%d' % idx, kind='name', seq=seq, scene=fn, speaker=None,
                                      source=text, skip=0))
                    seq += 1
                elif is_display_string(text, fn):
                    units.append(dict(key='s%d' % idx, kind='string', seq=seq, scene=fn, speaker=None,
                                      source=text, skip=0))
                    seq += 1
                continue
            key = 'm%d' % idx
            if any(u['key'] == key for u in units[-3:]):
                continue
            if text.startswith(' ') and last_speaker is not None:
                spk = last_speaker
            else:
                spk = speaker
            speaker = None
            last_speaker = spk
            skip = 1 if (not text.strip() or JP_RE.search(text)) else 0
            units.append(dict(key=key, kind='message', seq=seq, scene=fn, speaker=spk, source=text, skip=skip))
            sc = scenes.setdefault(fn, {'scene': fn, 'first_seq': seq, 'n_units': 0})
            sc['n_units'] += 1
            seq += 1
        # dedupe messages that appear in several functions (keep first)
        out, seen = [], set()
        for u in units:
            if u['key'] in seen:
                continue
            seen.add(u['key'])
            out.append(u)
        write_json(self.ws.p('extract', 'jp2en.json'), jp2en)
        return out, list(scenes.values())

    @staticmethod
    def auto_translate(source):
        """Rule-based translation for punctuation-only lines. Returns None if not applicable."""
        if re.search(r'[A-Za-z0-9]', source):
            return None
        t = source.replace('...', '……').replace('..', '…').replace('?', '？').replace('!', '！')
        return t

    # ------------------------------------------------------------------ build
    @staticmethod
    def normalize(text):
        for a, b in NORMALIZE.items():
            text = text.replace(a, b)
        return text

    def build(self, translations, log=print):
        trans = {k: self.normalize(v) for k, v in translations.items()}
        d, fonts = fnllib.load(self.orig('fnl'))
        present = {i for i, g in enumerate(fonts[0][0]['g']) if g[1]}
        chars = set()
        for v in trans.values():
            chars.update(v)
        mapping, need_glyph, used, pending = {}, {}, set(), []
        for ch in sorted(chars):
            if ord(ch) < 0x80:
                continue
            code = sjis(ch)
            if code is not None and fnllib.char2idx(code) in present:
                continue
            if code is not None and is_kanji_slot(code):
                mapping[ch] = code
                used.add(code)
                need_glyph[code] = ch
            elif code is not None and code < 0x889f and fnllib.char2idx(code):
                mapping[ch] = code
                need_glyph[code] = ch
            else:
                pending.append(ch)
        free = [c for c in slot_pool() if c not in used]
        if len(pending) > len(free):
            raise RuntimeError('字符太多，编码空位不足: %d > %d' % (len(pending), len(free)))
        for ch in pending:
            code = free.pop(0)
            mapping[ch] = code
            need_glyph[code] = ch
        log('Build', '用到 %d 个字符，需绘制字形 %d 个，重映射 %d 个，剩余空位 %d' %
            (len(chars), len(need_glyph), len(pending), len(free)))
        write_json(self.ws.p('build', 'charmap.json'), {ch: '%04x' % c for ch, c in mapping.items()})

        def conv(s):
            return ''.join(bytes([mapping[ch] >> 8, mapping[ch] & 0xff]).decode('cp932') if ch in mapping else ch
                           for ch in s)

        edit = self.ws.p('build', 'edit.txt')
        with open(edit, 'w', encoding='utf-8') as f:
            for k, v in trans.items():
                f.write('%s[%d] = "%s"\n' % (k[0], int(k[1:]), escape(conv(v))))
        files = self.ws.meta['files']
        out_ain = self.ws.p('build', files['ain'])
        r = subprocess.run([ALICE, 'ain', 'edit', '-t', edit, '-o', out_ain, self.orig('ain')],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode:
            raise RuntimeError('ain 写回失败: ' + (r.stderr or r.stdout)[-800:])
        log('Build', '已生成 %s' % files['ain'])
        build_font(d, fonts, need_glyph, self.ws.p('build', files['fnl']), self.cfg['font']['ttf'])
        log('Build', '已生成 %s' % files['fnl'])

    # --------------------------------------------------------------- validate
    def validate(self, translations, log=print):
        problems = []
        files = self.ws.meta['files']
        built_ain = self.ws.p('build', files['ain'])
        charmap = read_json(self.ws.p('build', 'charmap.json'), {})
        rev = {bytes.fromhex(v).decode('cp932'): k for k, v in charmap.items()}
        chk = self.ws.p('build', 'verify_messages.txt')
        subprocess.run([ALICE, 'ain', 'dump', '-m', '-o', chk, built_ain], check=True, capture_output=True)
        lines = open(chk, encoding='utf-8').read().split('\n')
        n_ok = 0
        for k, v in translations.items():
            if k[0] != 'm':
                continue
            i = int(k[1:])
            if i >= len(lines):
                problems.append('%s: 超出范围' % k)
                continue
            got = ''.join(rev.get(c, c) for c in lines[i])
            want = self.normalize(v).replace('\n', '\\n')
            if got.replace('\\"', '"') != want and got != want:
                problems.append('%s: 写回内容不一致' % k)
            else:
                n_ok += 1
        # font coverage
        d, fonts = fnllib.load(self.ws.p('build', files['fnl']))
        present = {i for i, g in enumerate(fonts[0][0]['g']) if g[1]}
        missing = set()
        for v in translations.values():
            for ch in self.normalize(v):
                if ord(ch) < 0x80:
                    continue
                code = int(charmap[ch], 16) if ch in charmap else sjis(ch)
                if code is None or fnllib.char2idx(code) not in present:
                    missing.add(ch)
        if missing:
            problems.append('字体缺字: %s' % ''.join(sorted(missing))[:200])
        log('Validate', '台词写回校验通过 %d 条，问题 %d 个' % (n_ok, len(problems)))
        return problems

    # ---------------------------------------------------------------- install
    def game_running(self):
        r = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, errors='replace')
        exe = os.path.splitext(self.ws.meta['files']['ain'])[0].lower() + '.exe'
        return exe in r.stdout.lower()

    def install(self, game_dir, log=print):
        if self.game_running():
            raise RuntimeError('游戏正在运行，请先关闭游戏再安装补丁')
        files = self.ws.meta['files']
        for k in ('ain', 'fnl'):
            shutil.copy2(self.ws.p('build', files[k]), os.path.join(game_dir, files[k]))
        log('Install', '补丁已安装到 %s' % game_dir)

    def uninstall(self, game_dir, log=print):
        if self.game_running():
            raise RuntimeError('游戏正在运行，请先关闭游戏')
        files = self.ws.meta['files']
        for k in ('ain', 'fnl'):
            shutil.copy2(self.ws.p('original', files[k]), os.path.join(game_dir, files[k]))
        log('Install', '已恢复原版文件')


# ---------------------------------------------------------------------- font
def sjis(ch):
    try:
        b = ch.encode('cp932')
    except UnicodeEncodeError:
        return None
    return (b[0] << 8) | b[1] if len(b) == 2 else None


def is_kanji_slot(code):
    lead, trail = code >> 8, code & 0xff
    return (0x88 <= lead <= 0x9f or 0xe0 <= lead <= 0xea) and trail != 0x5c and code >= 0x889f


def slot_pool():
    pool = []
    for lead in list(range(0x88, 0xa0)) + list(range(0xe0, 0xeb)):
        for trail in list(range(0x40, 0x7f)) + list(range(0x80, 0xfd)):
            code = (lead << 8) | trail
            if not is_kanji_slot(code):
                continue
            try:
                u = bytes([lead, trail]).decode('cp932')
            except UnicodeDecodeError:
                continue
            if len(u) == 1 and u.encode('cp932') == bytes([lead, trail]):
                pool.append(code)
    return pool


def build_font(d, fonts, need_glyph, outpath, ttfs):
    from PIL import Image, ImageDraw, ImageFont
    cache = {}

    def render(ch, face, ttf):
        h, adv, base = face['h'], face['adv'], face['base']
        size = int(round(adv * 0.96))
        if (ttf, size) not in cache:
            cache[(ttf, size)] = ImageFont.truetype(ttf, size)
        ft = cache[(ttf, size)]
        img = Image.new('L', (adv, h), 0)
        dr = ImageDraw.Draw(img)
        by = (h - 1 - base) + int(round(adv * 0.08))
        bb = dr.textbbox((0, 0), ch, font=ft, anchor='ls')
        x = (adv - (bb[2] - bb[0])) // 2 - bb[0]
        dr.text((x, by), ch, font=ft, fill=255, anchor='ls')
        return img

    def bits(img, h):
        gw = img.width
        stride = (gw + 31) // 32 * 4
        px = img.tobytes()
        buf = bytearray(stride * h)
        for r in range(h):
            row = px[(h - 1 - r) * gw:(h - r) * gw]
            for c in range(gw):
                if row[c] >= 128:
                    buf[r * stride + c // 8] |= 0x80 >> (c % 8)
        return bytes(buf)

    maxidx = max([fnllib.char2idx(c) for c in need_glyph] + [0]) + 1
    blobs = bytearray()
    entries = []
    for fi, faces in enumerate(fonts):
        ttf = ttfs[min(fi, len(ttfs) - 1)]
        fl = []
        for face in faces:
            h = face['h']
            w, st, raw = fnllib.glyph(d, h, face['g'][fnllib.char2idx(0x41)])
            face['base'] = min(r for r in range(h) if any(raw[r * st:(r + 1) * st]))
            face['adv'] = face['g'][fnllib.char2idx(0x8140)][0]
            gl = []
            for i in range(max(len(face['g']), maxidx)):
                code = fnllib.idx2char(i)
                if code in need_glyph:
                    img = render(need_glyph[code], face, ttf)
                    comp = zlib.compress(bits(img, h), 9)
                    gl.append((img.width, len(blobs), len(comp)))
                    blobs += comp
                elif i < len(face['g']) and face['g'][i][1]:
                    w, pos, cs = face['g'][i]
                    gl.append((w, len(blobs), cs))
                    blobs += d[pos:pos + cs]
                else:
                    gl.append((0, None, 0))
            fl.append((h, face['uk'], gl))
        entries.append(fl)
    index_len = 4 + sum(4 + sum(12 + 10 * len(gl) for _, _, gl in fl) for fl in entries)
    index_size = 16 + index_len
    out = bytearray(struct.pack('<I', len(entries)))
    for fl in entries:
        out += struct.pack('<I', len(fl))
        for h, uk, gl in fl:
            out += struct.pack('<III', h, uk, len(gl))
            for w, pos, cs in gl:
                out += struct.pack('<HII', w, 0 if pos is None else index_size + pos, cs)
    assert len(out) == index_len
    data = b'FNA\0' + struct.pack('<III', 0, index_size + len(blobs), index_size) + bytes(out) + bytes(blobs)
    with open(outpath, 'wb') as f:
        f.write(data)
