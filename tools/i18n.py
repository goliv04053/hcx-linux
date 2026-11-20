#!/usr/bin/env python3
"""Translation helper for HCX (needs only Python, no gettext tools).

  python3 tools/i18n.py extract        rebuild hcx/locale/hcx.pot from the code
  python3 tools/i18n.py update         extract, then add new strings to every .po
                                       (and mark strings no longer used as obsolete)
  python3 tools/i18n.py new xx         start hcx/locale/xx.po for a new language
  python3 tools/i18n.py stats          show how much of each language is translated

Translatable strings are the string literals passed to _() or N_() in hcx/*.py.
The .po files are standard gettext files: Poedit, Gtranslator, Lokalize or
Weblate open them directly.
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "hcx")
LOCALE = os.path.join(SRC, "locale")
sys.path.insert(0, ROOT)
from hcx.i18n import parse_po, po_quote  # noqa: E402

HEADER = ('msgid ""\nmsgstr ""\n'
          '"Project-Id-Version: HCX\\n"\n'
          '"Language: %s\\n"\n'
          '"MIME-Version: 1.0\\n"\n'
          '"Content-Type: text/plain; charset=UTF-8\\n"\n'
          '"Content-Transfer-Encoding: 8bit\\n"\n')


def extract():
    """[(msgid, [file:line, ...])] in source order."""
    found = {}
    for name in sorted(os.listdir(SRC)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(SRC, name)
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            fname = node.func.attr if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                else getattr(getattr(node, "func", None), "id", None)
            if isinstance(node, ast.Call) and fname in ("_", "N_") and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    found.setdefault(a.value, []).append("hcx/%s:%d" % (name, a.lineno))
    return sorted(found.items(), key=lambda kv: (kv[1][0].split(":")[0], int(kv[1][0].split(":")[1])))


def write_po(path, lang, entries, old=None, obsolete=()):
    old = old or {}
    out = [HEADER % lang]
    for msgid, refs in entries:
        out.append("")
        out.append("#: " + " ".join(refs))
        out.append("msgid " + po_quote(msgid))
        out.append("msgstr " + po_quote(old.get(msgid, "")))
    for msgid in obsolete:
        out.append("")
        out.append("#~ msgid " + po_quote(msgid))
        out.append("#~ msgstr " + po_quote(old[msgid]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def po_files():
    return sorted(f for f in os.listdir(LOCALE) if f.endswith(".po"))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "update"
    entries = extract()
    if cmd in ("extract", "update"):
        write_po(os.path.join(LOCALE, "hcx.pot"), "", entries)
        print("hcx.pot: %d strings" % len(entries))
    if cmd == "update":
        ids = {m for m, _ in entries}
        for name in po_files():
            path = os.path.join(LOCALE, name)
            old = parse_po(open(path, encoding="utf-8").read())
            gone = [m for m in old if m not in ids and old[m]]
            write_po(path, name[:-3], entries, old, gone)
            print("%s: updated" % name)
    if cmd == "new":
        lang = argv[2]
        path = os.path.join(LOCALE, lang + ".po")
        if os.path.exists(path):
            sys.exit("%s already exists" % path)
        write_po(path, lang, entries)
        print("created %s: translate the msgstr lines" % path)
    if cmd in ("stats", "update", "new"):
        for name in po_files():
            tr = parse_po(open(os.path.join(LOCALE, name), encoding="utf-8").read())
            done = sum(1 for m, _ in entries if tr.get(m))
            print("  %-8s %3d/%d translated" % (name[:-3], done, len(entries)))


if __name__ == "__main__":
    main(sys.argv)
