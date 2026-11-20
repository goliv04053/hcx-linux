"""Translations of the HCX interface.

They live in standard gettext .po files in hcx/locale/ (pt_BR.po, es.po, …),
read directly at start-up, so no compilation step is needed. To add a
language, see TRANSLATING.md.

The language is, in order: the one picked in the app menu (saved in
~/.config/hcx/language), $HCX_LANG, then the usual LANGUAGE / LC_ALL /
LC_MESSAGES / LANG variables. English is the source language."""

import ast
import os

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")
CONFIG_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                           "hcx", "language")

# Names shown in the Language menu, in the language itself.
LANGUAGE_NAMES = {
    "en": "English", "pt_BR": "Português (Brasil)", "pt": "Português", "pt_PT": "Português (Portugal)",
    "es": "Español", "fr": "Français", "de": "Deutsch", "it": "Italiano", "ja": "日本語",
    "nl": "Nederlands", "pl": "Polski", "ru": "Русский", "uk": "Українська", "cs": "Čeština",
    "sv": "Svenska", "ca": "Català", "gl": "Galego", "eu": "Euskara", "tr": "Türkçe",
    "zh_CN": "中文（简体）", "zh_TW": "中文（繁體）", "ko": "한국어", "el": "Ελληνικά", "hu": "Magyar",
}


# -- .po files ------------------------------------------------------------------------------------
def po_quote(s):
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return '"%s"' % s


def _unquote(s):
    try:
        return ast.literal_eval(s.strip())
    except (ValueError, SyntaxError):
        return s.strip().strip('"')


def parse_po(text):
    """{msgid: msgstr} of a .po file. Fuzzy and obsolete entries are skipped."""
    out = {}
    msgid = msgstr = None
    field = None
    fuzzy = False

    def flush():
        if msgid and msgstr and not fuzzy:
            out[msgid] = msgstr

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#~"):
            if not line:
                flush()
                msgid = msgstr = field = None
                fuzzy = False
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                fuzzy = True
            continue
        if line.startswith("msgid "):
            if msgid is not None:
                flush()
                msgstr = None
                fuzzy = False
            msgid, field = _unquote(line[6:]), "id"
        elif line.startswith("msgstr "):
            msgstr, field = _unquote(line[7:]), "str"
        elif line.startswith('"'):
            if field == "id":
                msgid += _unquote(line)
            elif field == "str":
                msgstr += _unquote(line)
    flush()
    return out


# -- language choice ------------------------------------------------------------------------------
def available_languages():
    """Language codes that have a .po file, plus English."""
    langs = ["en"]
    if os.path.isdir(LOCALE_DIR):
        langs += sorted(f[:-3] for f in os.listdir(LOCALE_DIR) if f.endswith(".po"))
    return langs


def language_name(code):
    return LANGUAGE_NAMES.get(code) or LANGUAGE_NAMES.get(code.split("_")[0]) or code


def saved_language():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_language(code):
    """Remember the language picked in the menu ("" = follow the system)."""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(code + "\n")
    except OSError:
        pass


def _wanted():
    """Requested languages, most wanted first, as codes like "pt_BR"."""
    out = [v for v in (saved_language(), os.environ.get("HCX_LANG")) if v]
    return [x.split(".")[0].split("@")[0] for x in out] + _wanted_system()


def _wanted_system():
    out = []
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v:
            out += v.split(":")
            break
    return [x.split(".")[0].split("@")[0] for x in out if x and x not in ("C", "POSIX")]


def _pick(wanted, have):
    for w in wanted:
        if w == "en" or w.startswith("en_"):
            return "en"
        if w in have:
            return w
        base = w.split("_")[0]
        if base in have:
            return base
        for h in have:          # pt_PT asks, only pt_BR exists: still better than English
            if h.split("_")[0] == base:
                return h
    return "en"


def _load():
    have = [x for x in available_languages() if x != "en"]
    lang = _pick(_wanted(), have)
    if lang == "en":
        return lang, {}
    try:
        with open(os.path.join(LOCALE_DIR, lang + ".po"), encoding="utf-8") as f:
            return lang, parse_po(f.read())
    except OSError:
        return "en", {}


LANG, _TABLE = _load()


def translate_to(code, s):
    """s in another language (used to confirm a language change in that language)."""
    if code in ("", "en"):
        code = _pick(_wanted_system(), [x for x in available_languages() if x != "en"]) if code == "" else "en"
    if code == "en":
        return s
    try:
        with open(os.path.join(LOCALE_DIR, code + ".po"), encoding="utf-8") as f:
            return parse_po(f.read()).get(s, s)
    except OSError:
        return s


def _(s):
    """Translate an interface string."""
    return _TABLE.get(s, s)


def N_(s):
    """Mark a string for translation without translating it yet."""
    return s
