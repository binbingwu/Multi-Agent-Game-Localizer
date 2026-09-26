"""Context Agent: 分析剧情、角色和术语。

- 术语表: 角色名/NPC 称呼/地名/道具等，统一译名 (glossary.json，可手工编辑)
- 场景概要: 每个剧情场景一段目标语言概要，供翻译时参考
"""
import collections
import re
from concurrent.futures import ThreadPoolExecutor

from ..common import read_json, write_json
from ..llm import extract_json

from ..langs import get_lang

TERM_SYSTEM = """You are a game localization terminology expert. Translate the proper nouns of a {src} game into {dst}.
Rules: transliterate personal and place names using common, readable conventions of {dst};
translate descriptive NPC titles (e.g. "Worried Man") naturally by meaning; monsters, items and skills mostly by meaning.
Entries in the existing glossary must be reused as-is.
Output only a JSON object {{"source term": "translation", ...}} with no explanations."""

SCENE_SYSTEM = """You are a game story analyst. Read an excerpt of a game script (in {src}) and write, in {dst}:
1) a 2-3 sentence plot summary; 2) the characters present and how each of them talks (one short line each).
At most about 120 words or 150 CJK characters. Output the text directly, no headings."""

SPEAKER_SYSTEM = """You are a game localization expert. The items below are character names or NPC titles taken from the
game's internal sprite keys (they may be in the game's original language, e.g. Japanese). Give each one its {dst} name.
If an item is just another spelling of a name in the reference list (e.g. a katakana transliteration of an English name),
you must use the reference translation. Translate descriptive titles naturally by meaning.
Output only a JSON object {{"item": "translation"}}."""


class ContextAgent:
    name = 'Context'

    def __init__(self, ws, db, llm, cfg, src='en', tgt='zh-CN'):
        self.ws, self.db, self.llm, self.cfg = ws, db, llm, cfg
        self.src, self.tgt = get_lang(src), get_lang(tgt)
        fmt = dict(src=self.src.label, dst=self.tgt.label)
        self.term_system = TERM_SYSTEM.format(**fmt)
        self.scene_system = SCENE_SYSTEM.format(**fmt)
        self.speaker_system = SPEAKER_SYSTEM.format(**fmt)

    # ------------------------------------------------------------ glossary
    def load_glossary(self):
        g = read_json(self.ws.glossary_file, None)
        if g is None:
            g = read_json(self.ws.p('import', 'glossary.json'), {}) or {}
            write_json(self.ws.glossary_file, g)
        return g

    def save_glossary(self, g):
        write_json(self.ws.glossary_file, dict(sorted(g.items(), key=lambda kv: kv[0].lower())))

    def mine_terms(self):
        """Proper nouns: capitalised words/phrases that occur mid-sentence (after a lowercase word)
        and whose single words never occur in lowercase anywhere in the script."""
        texts = [r['source'] for r in self.db.q("SELECT source FROM units WHERE kind IN ('message','string') AND skip=0")]
        lower_words = set()
        for t in texts:
            lower_words.update(re.findall(r"\b[a-z][a-z']+\b", t))
        cnt = collections.Counter()
        phrase = r"[A-Z][a-z]+(?:'s)?(?: (?:of |the |de |la )?[A-Z][a-z]+)*"
        for t in texts:
            for m in re.finditer(r"\b[a-z][a-z,]* (%s)" % phrase, t):
                p = re.sub(r"'s$", '', m.group(1))
                cnt[p] += 1
        out = []
        for p, n in cnt.most_common():
            if n < 4:
                break
            words = [w for w in p.split() if w[0].isupper()]
            if len(words) == 1 and words[0].lower() in lower_words:
                continue  # ordinary word capitalised for emphasis  # ordinary word (e.g. "Actually", "Knight" is added via seed glossary instead)
            if len(p) <= 2:
                continue
            out.append(p)
        return out[:600]

    def build_glossary(self):
        g = self.load_glossary()
        names = [r['source'] for r in self.db.q("SELECT DISTINCT source FROM units WHERE kind='name'")]
        # capitalisation-based mining only works for Latin-script sources
        terms = self.mine_terms() if self.src.script == 'latin' else []
        ignore = set(read_json(self.ws.p('glossary_ignore.json'), []) or [])
        todo = [t for t in dict.fromkeys(names + terms) if t not in g and t not in ignore and len(t) < 60]
        self.ws.log(self.name, '术语表已有 %d 条，待翻译候选 %d 条' % (len(g), len(todo)))
        batches = [todo[i:i + 40] for i in range(0, len(todo), 40)]

        def work(batch):
            ref = '\n'.join('%s=%s' % (k, v) for k, v in list(g.items())[:150])
            user = 'Existing glossary (reuse these):\n%s\n\nTranslate these entries:\n%s' % (ref, '\n'.join(batch))
            for _ in range(3):
                try:
                    res = extract_json(self.llm.chat(self.term_system, user, temperature=0.2, json_mode=True))
                    return {k: v for k, v in res.items() if k in batch and isinstance(v, str) and v.strip()}
                except Exception:
                    continue
            return {}

        with ThreadPoolExecutor(self.cfg['translation']['workers']) as ex:
            for i, res in enumerate(ex.map(work, batches)):
                g.update({k: v for k, v in res.items() if k not in g})
                if (i + 1) % 5 == 0:
                    self.save_glossary(g)
                    self.ws.log(self.name, '术语翻译进度 %d/%d' % (i + 1, len(batches)))
        self.save_glossary(g)
        # name units take their translation straight from the glossary
        rows = [(g[r['source']], r['key']) for r in self.db.q("SELECT key, source FROM units WHERE kind='name' AND locked=0")
                if r['source'] in g]
        self.db.execmany("UPDATE units SET target=?, status='translated', origin='glossary' WHERE key=?", rows)
        self.ws.log(self.name, '术语表完成，共 %d 条（可编辑 %s）' % (len(g), self.ws.glossary_file))
        return g

    # -------------------------------------------------------------- scenes
    def summarize_scenes(self):
        scenes = self.db.q("SELECT scene FROM scenes WHERE summary IS NULL AND n_units >= 6 ORDER BY first_seq")
        self.ws.log(self.name, '待分析场景 %d 个' % len(scenes))

        def work(scene):
            rows = self.db.q("SELECT speaker, source FROM units WHERE scene=? AND kind='message' AND skip=0 ORDER BY seq LIMIT 80",
                             (scene,))
            text = '\n'.join(('%s: %s' % (r['speaker'], r['source'])) if r['speaker'] else r['source'] for r in rows)
            try:
                s = self.llm.chat(self.scene_system, text[:6000], temperature=0.2, max_tokens=400)
            except Exception as e:
                s = ''
            self.db.exec('UPDATE scenes SET summary=? WHERE scene=?', (s, scene))
            return scene

        done = 0
        with ThreadPoolExecutor(self.cfg['translation']['workers']) as ex:
            for _ in ex.map(work, [r['scene'] for r in scenes]):
                done += 1
                if done % 25 == 0:
                    self.ws.log(self.name, '场景分析进度 %d/%d' % (done, len(scenes)))
        self.ws.log(self.name, '场景分析完成')

    # ------------------------------------------------------------ speakers
    def map_speakers(self, glossary):
        """Speaker keys come from sprite names (often in the original language). Map every speaker to its target-language name."""
        path = self.ws.p('speakers.json')
        spk = read_json(path, {}) or {}
        rows = self.db.q("SELECT speaker, COUNT(*) n FROM units WHERE speaker IS NOT NULL GROUP BY speaker ORDER BY n DESC")
        todo = []
        for r in rows:
            s = r['speaker']
            if s in spk:
                continue
            if s in glossary:
                spk[s] = glossary[s]
            else:
                todo.append(s)
        names = [(k, v) for k, v in glossary.items() if k[:1].isupper()]
        ref = '\n'.join('%s=%s' % kv for kv in names[:400])
        batches = [todo[i:i + 40] for i in range(0, len(todo), 40)]

        def work(b):
            for _ in range(3):
                try:
                    res = extract_json(self.llm.chat(self.speaker_system,
                                                     'Reference (source = translation):\n%s\n\nTranslate:\n%s' % (ref, '\n'.join(b)),
                                                     temperature=0.1, json_mode=True))
                    return {k: v for k, v in res.items() if k in b and isinstance(v, str)}
                except Exception:
                    continue
            return {}

        with ThreadPoolExecutor(self.cfg['translation']['workers']) as ex:
            for res in ex.map(work, batches):
                spk.update(res)
        write_json(path, spk)
        self.ws.log(self.name, '说话人映射 %d 个（%s）' % (len(spk), path))
        return spk

    def run(self):
        g = self.build_glossary()
        self.map_speakers(g)
        self.summarize_scenes()
