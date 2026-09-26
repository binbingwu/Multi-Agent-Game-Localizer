"""Language profiles. Any language the LLM knows can be a source or target:
known languages get tuned rules, anything else falls back to a generic profile.

script:  cjk | hangul | latin | cyrillic | greek | other
ratio:   acceptable (target chars / source chars) range for lines >= 20 chars, relative to an
         English-like source; QA flags anything outside it.
"""
import re

PROFILES = {
    'zh-CN': dict(name='Simplified Chinese', native='简体中文', script='cjk', ellipsis='……', ratio=(0.12, 1.6),
                  aliases=['简体中文', '中文', '汉语', 'chinese', 'zh', 'zh-cn', 'zh-hans', 'simplified chinese', 'schinese'],
                  rules=['Use full-width Chinese punctuation: ，。！？：；……  (ellipsis is ……, dash is ――).',
                         'Render interjections, moans and sound effects as natural Chinese onomatopoeia (啊、嗯、呜、哈啊).']),
    'zh-TW': dict(name='Traditional Chinese', native='繁體中文', script='cjk', ellipsis='……', ratio=(0.12, 1.6),
                  aliases=['繁體中文', '繁体中文', 'zh-tw', 'zh-hant', 'traditional chinese', 'tchinese'],
                  rules=['Use Traditional Chinese characters and Taiwan usage, full-width punctuation ，。！？：；…… .',
                         'Render interjections and sound effects as natural Chinese onomatopoeia.']),
    'ja': dict(name='Japanese', native='日本語', script='cjk', ellipsis='……', ratio=(0.15, 1.8),
               aliases=['日本語', '日语', 'japanese', 'ja', 'jp'],
               rules=['Use natural Japanese with Japanese punctuation 、。！？……, but keep the ASCII quote marks of the source.',
                      'Match each character\'s speech style (keigo, casual, first-person pronoun).']),
    'ko': dict(name='Korean', native='한국어', script='hangul', ellipsis='...', ratio=(0.2, 1.8),
               aliases=['한국어', '韩语', 'korean', 'ko', 'kr'],
               rules=['Use natural Korean with appropriate speech levels for each character.']),
    'en': dict(name='English', native='English', script='latin', ellipsis='...', ratio=(0.5, 2.2),
               aliases=['english', 'en', '英语', '英文'], rules=[]),
    'es': dict(name='Spanish', native='Español', script='latin', ellipsis='...', ratio=(0.6, 2.2),
               aliases=['spanish', 'español', 'es', '西班牙语'], rules=['Use ¿ ¡ where Spanish requires them.']),
    'fr': dict(name='French', native='Français', script='latin', ellipsis='...', ratio=(0.6, 2.2),
               aliases=['french', 'français', 'fr', '法语'], rules=[]),
    'de': dict(name='German', native='Deutsch', script='latin', ellipsis='...', ratio=(0.6, 2.3),
               aliases=['german', 'deutsch', 'de', '德语'], rules=[]),
    'pt': dict(name='Portuguese (Brazil)', native='Português', script='latin', ellipsis='...', ratio=(0.6, 2.2),
               aliases=['portuguese', 'português', 'pt', 'pt-br', '葡萄牙语'], rules=[]),
    'it': dict(name='Italian', native='Italiano', script='latin', ellipsis='...', ratio=(0.6, 2.2),
               aliases=['italian', 'italiano', 'it', '意大利语'], rules=[]),
    'ru': dict(name='Russian', native='Русский', script='cyrillic', ellipsis='...', ratio=(0.5, 2.2),
               aliases=['russian', 'русский', 'ru', '俄语'], rules=[]),
    'pl': dict(name='Polish', native='Polski', script='latin', ellipsis='...', ratio=(0.6, 2.3),
               aliases=['polish', 'polski', 'pl', '波兰语'], rules=[]),
    'vi': dict(name='Vietnamese', native='Tiếng Việt', script='latin', ellipsis='...', ratio=(0.6, 2.2),
               aliases=['vietnamese', 'tiếng việt', 'vi', '越南语'], rules=[]),
    'id': dict(name='Indonesian', native='Bahasa Indonesia', script='latin', ellipsis='...', ratio=(0.6, 2.3),
               aliases=['indonesian', 'bahasa indonesia', 'id', '印尼语'], rules=[]),
    'tr': dict(name='Turkish', native='Türkçe', script='latin', ellipsis='...', ratio=(0.6, 2.2),
               aliases=['turkish', 'türkçe', 'tr', '土耳其语'], rules=[]),
    'uk': dict(name='Ukrainian', native='Українська', script='cyrillic', ellipsis='...', ratio=(0.5, 2.2),
               aliases=['ukrainian', 'українська', 'uk', '乌克兰语'], rules=[]),
}


class Lang:
    def __init__(self, code, prof):
        self.code = code
        self.__dict__.update(prof)

    @property
    def label(self):
        return self.name if self.native == self.name else '%s (%s)' % (self.name, self.native)

    @property
    def non_latin(self):
        return self.script in ('cjk', 'hangul', 'cyrillic', 'greek', 'other')

    @property
    def dense(self):
        return self.script == 'cjk'

    def __repr__(self):
        return '<Lang %s>' % self.code


def get_lang(spec):
    """Resolve a language code or name ("zh-CN", "简体中文", "Polish", "Swahili"...)."""
    if isinstance(spec, Lang):
        return spec
    s = (spec or 'en').strip()
    if s in PROFILES:
        return Lang(s, PROFILES[s])
    low = s.lower()
    for code, p in PROFILES.items():
        if low == code.lower() or low in [a.lower() for a in p['aliases']] or low == p['name'].lower():
            return Lang(code, p)
    # unknown language: generic profile, the LLM still knows it
    return Lang(s, dict(name=s, native=s, script='unknown', ellipsis='...', ratio=(0.2, 3.0), aliases=[],
                        rules=['Use the standard punctuation and typography of %s.' % s]))


def source_is_latin(lang):
    return get_lang(lang).script == 'latin'
