"""Native Linux rewrites of HyperCard externals (XCMD/XFCN).

Original externals are 68000 machine code for the classic Mac OS and can't
run here, so the ones that stacks commonly need are rewritten in Python
with the same names and parameters. Each takes (interp, args) and returns
a value (functions) or sets `the result` (commands).

Files that externals read or write go to HCX's sandbox folder
(~/.local/share/hcx/files), with Mac paths ("Disk:Folder:File") mapped
to folders inside it."""

import os

from . import hypertalk as ht

_COMMANDS = {}
_FUNCTIONS = {}


def command(name):
    return _COMMANDS.get(name.lower())


def function(name):
    return _FUNCTIONS.get(name.lower())


def known():
    return sorted(set(_COMMANDS) | set(_FUNCTIONS))


def xcmd(*names):
    def deco(f):
        for n in names:
            _COMMANDS[n.lower()] = f
        return f
    return deco


def xfcn(*names):
    def deco(f):
        for n in names:
            _FUNCTIONS[n.lower()] = f
        return f
    return deco


def _arg(a, i, default=""):
    return a[i] if i < len(a) else default


def _local(path):
    from .hthost import mac_to_local
    return mac_to_local(path)


# -- resources ---------------------------------------------------------------------------

def _find_resource(it, rtypes, key, only_current=False):
    """Search the resource chain (this stack, stacks in use) for a resource
    by name or id. Returns (type, id, name, data) or None."""
    key = str(key).strip()
    low = key.lower()
    num = int(ht.to_num(key)) if ht.is_number(key) else None
    for res in it.host.resource_chain(only_current):
        for r in res:
            if r[0] in rtypes and ((num is not None and r[1] == num) or r[2].lower() == low):
                return r
    return None


def _cstr(data):
    if data.endswith(b"\0"):
        data = data[:-1]
    k = data.find(b"\0")
    if k >= 0:
        data = data[:k]
    return data.decode("mac_roman", "replace").replace("\r\n", "\n").replace("\r", "\n")


@xfcn("GetStr", "Get1Str", "GetCSTR", "GetString")
def get_str(it, a):
    key = _arg(a, 0)
    over = it.host.str_overrides.get(str(key).lower())
    if over is not None:
        return over
    r = _find_resource(it, ("CSTR", "STR ", "TEXT"), key)
    if r is None:
        return ""
    if r[0] == "STR ":
        n = r[3][0] if r[3] else 0
        return r[3][1:1 + n].decode("mac_roman", "replace").replace("\r", "\n")
    return _cstr(r[3])


def _str_list(data):
    n = int.from_bytes(data[:2], "big") if len(data) >= 2 else 0
    out, p = [], 2
    for _ in range(n):
        if p >= len(data):
            break
        k = data[p]
        out.append(data[p + 1:p + 1 + k].decode("mac_roman", "replace").replace("\r", "\n"))
        p += 1 + k
    return out


@xfcn("GetIndStr", "GetIndString")
def get_ind_str(it, a):
    """GetIndStr(STR# name or id, index): one string of a string list."""
    r = _find_resource(it, ("STR#",), _arg(a, 0))
    if r is None or not ht.is_number(_arg(a, 1)):
        return ""
    items = _str_list(r[3])
    i = int(ht.to_num(_arg(a, 1)))
    return items[i - 1] if 1 <= i <= len(items) else ""


@xfcn("GetRegionCode")
def get_region_code(it, a):
    """The region code of the HyperCard application's 'vers' resource. HCX
    answers 0 (U.S.), so Voyager's Library shows its English strings."""
    return "0"


@xcmd("DeleteResID")
def delete_res_id(it, a):
    """DeleteResID type, name or id. Resources are read only in HCX, so
    this only forgets a string set with SetStr in this session."""
    rtype = str(_arg(a, 0)).ljust(4)[:4]
    r = _find_resource(it, (rtype,), _arg(a, 1))
    over = it.host.str_overrides.pop(str(_arg(a, 1)).lower(), None) if rtype == "CSTR" else None
    if r is None and over is None:
        it.result = "Resource not found."


@xcmd("SetStr", "SetCSTR", "PutStr")
def set_str(it, a):
    if len(a) < 2:
        it.result = "Invalid number of parameters."
        return
    # kept for this session; the original wrote into the stack's resource fork
    it.host.str_overrides[str(_arg(a, 0)).lower()] = str(_arg(a, 1))


@xfcn("ResID")
def res_id(it, a):
    r = _find_resource(it, (str(_arg(a, 0)).ljust(4)[:4],), _arg(a, 1))
    return str(r[1]) if r else ""


@xfcn("listOfResources", "ListResources", "ResList")
def list_resources(it, a):
    rtype = str(_arg(a, 0)).ljust(4)[:4]
    out = []
    for res in it.host.resource_chain(False):
        out += [r[2] or str(r[1]) for r in res if r[0] == rtype]
    return "".join(n + "\n" for n in out)      # Voyager's version ends every line


def twtx_text(data):
    """'twtx' resources hold a length word followed by Mac text."""
    if len(data) >= 2:
        n = int.from_bytes(data[:2], "big")
        if 0 < n <= len(data) - 2:
            data = data[2:2 + n]
        else:
            data = data[2:]
    return data.decode("mac_roman", "replace").replace("\r", "\n")


# -- text --------------------------------------------------------------------------

@xfcn("StripEm", "Strip")
def strip_em(it, a):
    """Replace each char of arg 2 with the matching char of arg 3 (Mac menu
    metacharacters by default), like `tr`."""
    text = str(_arg(a, 0))
    src = str(_arg(a, 1, ";^!</("))
    dst = str(_arg(a, 2, ":'.≤\\{"))
    table = {}
    for k, ch in enumerate(src):
        table[ord(ch)] = dst[k] if k < len(dst) else None
    return text.translate(table)


@xfcn("CharInStr")
def char_in_str(it, a):
    c = str(_arg(a, 0))
    return ht.b(bool(c) and c in str(_arg(a, 1)))


@xfcn("GetCharWidth", "CharWidth", "TextWidth", "StringWidth")
def char_width(it, a):
    """GetCharWidth(font, size, ASCII value) in Voyager's Library; the other
    names take (text, font, size)."""
    if len(a) >= 3 and not ht.is_number(_arg(a, 0)) and ht.is_number(_arg(a, 1)) and ht.is_number(_arg(a, 2)):
        ch = bytes([int(ht.to_num(_arg(a, 2))) & 0xFF]).decode("mac_roman")
        return str(it.host.text_width(ch, str(_arg(a, 0)), int(ht.to_num(_arg(a, 1)))))
    return str(it.host.text_width(str(_arg(a, 0)), str(_arg(a, 1, "Geneva")), int(ht.to_num(_arg(a, 2, "12") or 12))))


@xfcn("GetClickedWord")
def get_clicked_word(it, a):
    """(field number, field layer 'b'/'c', ignored, click location) → the word
    under the click."""
    return it.host.click_text() or it.host.word_at(_arg(a, 0), _arg(a, 1), _arg(a, 3))


@xfcn("FontExists")
def font_exists(it, a):
    """FontExists(name[, size]): whether the Mac knows the font, i.e. it is
    one of the classic system fonts or a FOND in the stack's resources.
    Any size counts: HCX scales outline replacements."""
    name = str(_arg(a, 0)).strip().lower()
    try:
        from .render import MAC_FONT_IDS
        known = {n.lower() for n in MAC_FONT_IDS.values()}
    except Exception:  # noqa: BLE001
        known = {"chicago", "geneva", "new york", "monaco", "times", "helvetica", "courier", "palatino"}
    if name in known:
        return ht.TRUE
    for res in it.host.resource_chain(False):
        if any(r[0] == "FOND" and r[2].lower() == name for r in res):
            return ht.TRUE
    return ht.FALSE


@xfcn("MainScreenRect", "ScreenRect")
def main_screen_rect(it, a):
    return it.host.screen_rect()


@xfcn("GetScreenRects", "ScreenRects")
def screen_rects(it, a):
    return it.host.screen_rect()


_EXT_OVERRIDE = {}


def _ext_code(it, name):
    """The 68k code of the stack's own XCMD or XFCN of that name, or b"".
    Natives use it only to tell versions of an external apart by the
    usage or copyright text inside (never to run it)."""
    low = name.lower()
    if low in _EXT_OVERRIDE:        # set by tools/xemu/compare.py
        return _EXT_OVERRIDE[low]
    try:
        chain = it.host.resource_chain()
    except Exception:  # noqa: BLE001
        return b""
    for res in chain:
        for r in res:
            if r[0] in ("XFCN", "XCMD") and r[2].lower() == low:
                return r[3]
    return b""


def _bool_param(v):
    v = str(v).strip().lower()
    return True if v == "true" else False if v == "false" else None


def _fold(text):
    """Case folding like the Mac's Script Manager for Mac Roman text."""
    return text.lower()


@xfcn("FullReplace")
def full_replace(it, a):
    """FullReplace(input, search[, replace[, all[, case sensitive][, offset]]])
    by Frédéric Rinaldi. Versions before 1.2 have no case parameter; a fifth
    parameter that is a number is the offset of those versions."""
    if len(a) < 2:
        return "Error : Missing parameter(s)"
    text, pat = str(a[0]), str(a[1])
    rep = str(_arg(a, 2))
    every, case, offset = True, True, 1
    if len(a) > 3 and str(a[3]) != "":
        every = _bool_param(a[3])
        if every is None:
            return "Error : Param #4 must be boolean"
    rest = list(a[4:6])
    code = _ext_code(it, "FullReplace")
    old = bool(code) and b"<case sens.>" not in code
    if old and len(a) > 4:
        if not ht.is_number(a[4]) or int(ht.to_num(a[4])) < 1:
            return "Error : Param #5 must be > 0"
        rest = [a[4]]
    elif code and not old and len(a) > 4 and _bool_param(a[4]) is None:
        return "Error : Param #5 must be boolean"
    if rest and _bool_param(rest[0]) is not None:
        case = _bool_param(rest.pop(0))
        n_off = 6
    else:
        n_off = 5
    if rest and str(rest[0]) != "":
        if not ht.is_number(rest[0]):
            return "Error : Param #%d must be boolean" % n_off if n_off == 5 and len(a) > 5 else \
                "Error : Param #%d must be > 0" % n_off
        offset = int(ht.to_num(rest[0]))
        if offset < 1:
            return "Error : Param #%d must be > 0" % n_off
    if not text or not pat:
        return ""
    hay = text if case else _fold(text)
    needle = pat if case else _fold(pat)
    out, i = [], offset - 1
    out.append(text[:i])
    while True:
        j = hay.find(needle, i)
        if j < 0:
            break
        out.append(text[i:j] + rep)
        i = j + len(pat)
        if not every:
            break
    out.append(text[i:])
    return "".join(out)


@xfcn("ReplaceCharSet")
def replace_char_set(it, a):
    """ReplaceCharSet(text, original set, substitution set) by Rinaldi."""
    text, src, dst = (str(_arg(a, i)) for i in range(3))
    if not text or not src or not dst:
        return "Error : Missing or empty parameter"
    if len(src) != len(dst):
        return "Error : Sets have different length"
    table = {}
    first_wins = b"ReplaceCharSet 1.0," in _ext_code(it, "ReplaceCharSet")
    for x, y in zip(src, dst):
        if not (first_wins and x in table):
            table[x] = y
    return "".join(table.get(ch, ch) for ch in text)


@xfcn("LeafName")
def leaf_name(it, a):
    """LeafName(path): what follows the last colon (Dartmouth XCMDs)."""
    return str(_arg(a, 0)).rsplit(":", 1)[-1]


def _ascii_upper(text):
    return "".join(c.upper() if "a" <= c <= "z" else c for c in text)


def _word_char(c):
    """A letter or a digit, as the Script Manager's CharType sees it."""
    return c.isalpha() or c.isdigit()


@xfcn("FullFind")
def full_find(it, a):
    """FullFind(container, pattern[, exactly[, all[, case sens.[, beginning[,
    start[, items]]]]]]) by Frédéric Rinaldi. Each find is "char in
    text,line in text,char in line,item in line", one per line. "exactly"
    (the default) takes whole words only; "beginning" (version 2) words
    that start with the pattern; start is how many characters to skip;
    items a list of item numbers the find must start in."""
    a = [str(x) for x in a]
    if len(a) < 2:
        return "Error : Missing parameter(s)"
    code = _ext_code(it, "FullFind")
    v2 = b"<beginning>" in code
    v11 = b"Version 1.1," in code
    flags = [True, False, False, False]
    nflags = 4 if v2 else 3
    for n in range(nflags):
        if len(a) > n + 2:
            v = _bool_param(a[n + 2])
            if v is None:
                return "Error : Param %d must be true|false" % (n + 3)
            flags[n] = v
    exactly, every, case, beginning = flags
    rest = a[2 + nflags:]
    skip = 0
    if rest:
        s = rest[0]
        if v2:
            if not (s.lstrip("-").isdigit() and s.count("-") <= 1 and s[:1] != "+"):
                return "Error : Param 7 must be an integer"
            skip = int(s)
            if skip < 0:
                return ""
        else:
            if not s.isdigit():
                return "Error : Param 6 must be a positive integer"
            skip = int(s)
    items = None
    if len(rest) > 1:
        def num(x):
            if v11:
                d = len(x) - len(x.lstrip("0123456789"))
                return int(x[:d]) if d else 0
            return int(x) if x.isdigit() else 0
        if not (v11 and rest[1] == ""):
            items = {num(x) for x in rest[1].split(",")}
    delim = it.props.get("itemdelimiter", ",") if not code or b"the itemDelimiter" in code else ","
    text, pat = a[0].replace("\r", "\n"), a[1].replace("\r", "\n")
    if exactly and pat[-1:] in ("\n", delim):
        return "Error : Separator char not allowed at the end of pattern"
    if not text or not pat:
        return ""
    if v11:
        def boundary(c):
            return c in (" ", "\n", delim)
    else:
        def boundary(c):
            return not _word_char(c)
    hay, needle = (text, pat) if case else (_ascii_upper(text), _ascii_upper(pat))
    found = []
    i = hay.find(needle, skip)
    while i >= 0:
        end = i + len(needle)
        ok = not (exactly or beginning) or i == 0 or boundary(text[i - 1])
        if ok and exactly:
            ok = end >= len(text) or boundary(text[end])
        line_start = text.rfind("\n", 0, i) + 1
        item = text.count(delim, line_start, i) + 1
        if ok and (items is None or item in items):
            found.append("%d,%d,%d,%d" % (i + 1, text.count("\n", 0, i) + 1, i - line_start + 1, item))
            if not every:
                break
        i = hay.find(needle, i + 1)
    return "\n".join(found)


@xfcn("Substitute")
def substitute(it, a):
    """Substitute(characters, replacement, text) by Eric Celeste: each of
    the characters in text becomes the first character of replacement
    (or goes away when it's empty)."""
    if len(a) != 3:
        return ""
    chars, rep, text = (str(x) for x in a)
    rep = rep[:1]
    return "".join(rep if ch in chars else ch for ch in text)


def _align_number(s):
    """Rinaldi's Align takes as a number anything made of digits, signs
    and points that ends with a digit or a point (but not "." alone)."""
    return bool(s) and s != "." and s[-1] in "0123456789." and all(c in "0123456789+-." for c in s)


def _align_frac_width(s):
    dot = s.rfind(".")
    return 0 if dot < 0 or dot == len(s) - 1 else len(s) - dot


@xfcn("Align")
def align(it, a):
    """Align(text[, F1[, ... Fn]][, "s=<separator>"][, "f=<filler>"]) by
    Frédéric Rinaldi, with F = "width[,l|r|c|d[,separator]]": lines up the
    items of each line in columns. The last format serves the remaining
    columns; with none, each column is as wide as its widest item plus a
    space. Text that doesn't fit ends in "…"; "d" lines up decimal points."""
    a = [str(x) for x in a]
    if not a or not a[0]:
        return "Error : Missing parameter"
    code = _ext_code(it, "Align")
    sep = it.props.get("itemdelimiter", ",") if b"the itemDelimiter" in code else ","
    fill = " "
    fmts = []
    buf1 = ""   # the original reads each parameter into one buffer, and
    for p in a[1:]:   # tells options by its second byte even when stale
        if len(p) >= 2:
            buf1 = p[1]
        if buf1 == "=":
            if len(p) < 3:
                return 'Error : Missing char following "%s"' % p
            if p[0] in "sS":
                sep = p[2]
            elif p[0] in "fF":
                fill = p[2]
            else:
                return 'Error : Param must be "s=…" or "f=…"'
            continue
        n = len(fmts) + 1
        parts = p.split(",", 2)
        if len(parts) == 2 and parts[1] == "":
            parts = parts[:1]
        w = parts[0][1:] if parts[0][:1] == "+" else parts[0]
        if not w.isdigit() or int(w) < 1:
            return "Error : First item of Format %d must be an integer > 0" % n
        how = parts[1][:1].lower() if len(parts) > 1 else "l"
        if how not in ("l", "r", "c", "d"):
            return "Error : Second item of Format %d must be [l,r,c,d]" % n
        after = parts[2] if len(parts) > 2 else ""
        if "," in after:   # the separator stops after its first comma
            after = after[:after.index(",") + 1]
        fmts.append((int(w), how, after))
    text = a[0].replace("\r", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    rows = [line.split(sep) for line in text.split("\n")]
    ncol = max(len(r) for r in rows)
    for r in rows:
        r.extend([""] * (ncol - len(r)))
    cols = []
    for i in range(ncol):
        if fmts:
            cols.append(fmts[min(i, len(fmts) - 1)])
        else:
            cols.append((max(len(r[i]) for r in rows), "l", " "))
    fracs = [max([_align_frac_width(r[i]) for r in rows if _align_number(r[i])] or [0])
             if cols[i][1] == "d" else 0 for i in range(ncol)]

    def cell(s, w, how, fw):
        if how == "d" and _align_number(s):
            dot = s.find(".")
            whole, frac = (s, "") if dot < 0 else (s[:dot], s[dot:])
            iw = w - fw
            whole = "" if iw <= 0 else whole[-iw:].rjust(iw, fill)
            out = whole + frac[:fw].ljust(fw, fill)
            return out[-w:] if len(out) > w else out
        if len(s) > w:
            return s[:w - 1] + "…"
        if how == "r":
            return s.rjust(w, fill)
        if how == "c":
            left = (w - len(s)) // 2
            return fill * left + s + fill * (w - len(s) - left)
        return s.ljust(w, fill)

    return "\n".join("".join(cell(r[i], w, how, fracs[i]) + after
                             for i, (w, how, after) in enumerate(cols))
                     for r in rows)


# -- dialogs and menus ---------------------------------------------------------------------

def _menu_lines(text):
    lines = str(text).split("\n")
    return lines


@xfcn("PopItUp", "PopUpMenu", "PopUp")
def pop_it_up(it, a):
    """PopItUp(items, checkedItem, h, v[, flags]) shows a pop-up menu at
    h,v (card coordinates) and returns "line,subitem", or 0 if cancelled.
    Lines starting with "<X" carry a style and are shown as a title; "("
    disables an item; "-" is a separator. Items of a line separated by
    commas become a submenu unless flags contain "nosubmenus"."""
    lines = _menu_lines(_arg(a, 0))
    checked = int(ht.to_num(_arg(a, 1, "0") or 0)) if ht.is_number(_arg(a, 1, "0")) else 0
    try:
        h = int(ht.to_num(_arg(a, 2, "0") or 0))
        v = int(ht.to_num(_arg(a, 3, "0") or 0))
    except ht.HTError:
        h = v = 0
    flags = str(_arg(a, 4)).lower()
    subs = "nosubmenus" not in flags
    items = []
    for k, ln in enumerate(lines):
        style = None
        if ln.startswith("<") and len(ln) > 1:
            style, ln = ln[1], ln[2:]
        enabled = not ln.startswith("(") and style != "U"
        if ln.startswith("("):
            ln = ln[1:]
        parts = ln.split(",") if subs and "," in ln else [ln]
        items.append({"text": parts[0], "sub": parts[1:] if len(parts) > 1 else [],
                      "enabled": enabled, "sep": ln.startswith("-"), "checked": k + 1 == checked,
                      "title": style is not None})
    r = it.host.popup_menu(items, h, v)
    if not r:
        return "0"
    line, sub = r
    return "%d,%d" % (line, sub)


@xfcn("HPopUpMenu", "FullHPop")
def h_pop_up_menu(it, a):
    """HPopUpMenu(items, checkedItem, top, left) pops up a menu with the given
    items (lines, or one line of comma-separated items) with its top-left
    corner at left,top and returns "item text,,item number", or empty if
    nothing was chosen. A menu resource id as items isn't supported."""
    src = str(_arg(a, 0))
    if ht.is_number(src):
        return ""
    lines = [x for x in src.split("\n")]
    if len(lines) == 1 and "," in lines[0]:
        lines = lines[0].split(",")
    while lines and lines[-1] == "":
        lines.pop()
    checked = int(ht.to_num(_arg(a, 1, "0"))) if ht.is_number(_arg(a, 1, "0")) else 0
    try:
        v = int(ht.to_num(_arg(a, 2, "0") or 0))
        h = int(ht.to_num(_arg(a, 3, "0") or 0))
    except ht.HTError:
        h = v = 0
    items = []
    for k, ln in enumerate(lines):
        enabled = not ln.startswith("(")
        if ln.startswith("("):
            ln = ln[1:]
        items.append({"text": ln, "sub": [], "enabled": enabled, "sep": ln.startswith("-"),
                      "checked": k + 1 == checked, "title": False})
    r = it.host.popup_menu(items, h, v)
    if not r:
        return ""
    line = r[0]
    return "%s,,%d" % (items[line - 1]["text"] if 0 < line <= len(items) else "", line)


@xfcn("ListSelect", "SelectList")
def list_select(it, a):
    """ListSelect(width, "h,v", prompt, list) → the chosen lines (several
    can be picked), or empty."""
    if len(a) >= 4:
        prompt, items = _arg(a, 2), _arg(a, 3)
    elif len(a) == 2:
        prompt, items = _arg(a, 0), _arg(a, 1)
    else:
        prompt, items = "", _arg(a, 0)
    lines = [x for x in str(items).split("\n") if x != ""]
    if not lines:
        return ""
    r = it.host.list_select(str(prompt), lines, multiple=True)
    return "\n".join(r or [])


# -- files ------------------------------------------------------------------------------

@xfcn("GetFilesInDir", "FileList", "ListFiles", "Files")
def files_in_dir(it, a):
    path = _local(_arg(a, 0))
    kind = str(_arg(a, 1, "")).strip().lower()
    delim = str(_arg(a, 2, "\n")) or "\n"
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except OSError:
        return ""
    out = []
    for n in names:
        if n.startswith("."):
            continue
        full = os.path.join(path, n)
        isdir = os.path.isdir(full)
        if kind == "folder" and not isdir:
            continue
        if kind not in ("folder", "") and isdir:
            continue
        out.append(n)
    return delim.join(out)


@xfcn("GetFileSize", "FileSize")
def file_size(it, a):
    try:
        return str(os.path.getsize(_local(_arg(a, 0))))
    except OSError:
        return "0"


_DIR_IDS = {}


def _dir_id(folder):
    """A folder's number, like an HFS dirID: 2 for a volume (a folder at the
    top of the sandbox), others numbered from 100 in the order they are
    asked for, and the same for the rest of the session."""
    from .hthost import files_root
    folder = os.path.realpath(folder)
    if os.path.dirname(folder) == os.path.realpath(files_root()):
        return 2
    return _DIR_IDS.setdefault(folder, 100 + len(_DIR_IDS))


@xfcn("GetPathInfo", "PathInfo")
def path_info(it, a):
    """GetPathInfo(path) in Voyager's Library: "vRefNum,dirID,name" for the
    folder that holds the last part of the path (which need not exist).
    With no parameter it reads the path from the global xyzTempGlobal, as
    Voyager's other externals call it. The vRefNum is HyperCard's -1 printed
    unsigned, like the original."""
    path = str(_arg(a, 0)) if a else str(it.globals.get("xyztempglobal", ""))
    path = path.rstrip(":") if path.count(":") > 1 or not path.endswith(":") else path
    vol, _, rest = path.partition(":")
    leaf = rest.rsplit(":", 1)[-1] if rest else ""
    parent = _local(vol + ":" + rest.rsplit(":", 1)[0]) if ":" in rest else _local(vol)
    if not os.path.isdir(parent):
        return "0,0,"
    return "4294967295,%d,%s" % (_dir_id(parent), leaf)


@xcmd("DeleteFile", "FileDelete")
def delete_file(it, a):
    p = _local(_arg(a, 0))
    root = _local("")
    if not os.path.abspath(p).startswith(os.path.dirname(root)):
        it.result = "Can't delete files outside HCX's folder."
        return
    try:
        if os.path.isdir(p):
            os.rmdir(p)         # like the File Manager: only an empty folder
        else:
            os.remove(p)
    except OSError:
        it.result = "File not deleted"


@xcmd("CreateFolder", "NewFolder", "MakeFolder")
def create_folder(it, a):
    """CreateFolder folder path, name (Voyager's Library) or CreateFolder
    full path (other stacks)."""
    if len(a) >= 2:
        parent = _local(_arg(a, 0).rstrip(":") or _arg(a, 0))
        if not os.path.isdir(parent):
            it.result = "Could not get directory id."
            return
        p = os.path.join(parent, str(_arg(a, 1)).replace("/", "_").replace(":", "_"))
        if os.path.exists(p):
            it.result = "Could not create new folder."
            return
        try:
            os.mkdir(p)
        except OSError:
            it.result = "Could not create new folder."
        return
    try:
        os.makedirs(_local(_arg(a, 0)), exist_ok=True)
    except OSError as e:
        it.result = str(e)


# -- Voyager's indexed data files ------------------------------------------------------------
# The Library of the Expanded Books saves the reader's marks ("Export
# markings") as a data file of entries, each a 4-byte length and the bytes,
# plus an index file of 4-byte offsets (one per entry and one for the end of
# the data). Style entries hold a field's TextEdit style record: the runs
# (startChar, styleIndex, ending with teLength + 1) and the style table
# (stCount, stHeight, stAscent, stFont, stFace, stSize, stColor). The layout
# was checked against the original externals in tools/xemu.

_FACE_BITS = {"bold": 1, "italic": 2, "underline": 4, "outline": 8, "shadow": 16,
              "condensed": 32, "extend": 64, "group": 128}


def _append_entry(path, data):
    p = _local(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "ab") as fh:
        fh.write(len(data).to_bytes(4, "big") + data)


def _field_ref(it, layer, num):
    kind = "card" if str(layer).strip().lower().startswith("c") else "bkgnd"
    if not ht.is_number(num):      # the originals take only a number
        return None
    try:
        ref = ht.parse_ref(it, "%s field %d" % (kind, int(ht.to_num(num))))
    except ht.HTError:
        return None
    return ref if isinstance(ref, ht.Obj) and ref.t == "part" else None


def _style_record(it, ref):
    """A field's styles as the bytes of a TextEdit style record."""
    host = it.host
    p = ref.b
    c = host.content_of(ref.a, p, ref.c, card=ref.d)
    text = c.text if c is not None else ""
    n = len(text)
    ids = host._style_list(c, n, p) if c is not None and n else []
    table, index, runs = [], {}, []
    prev = object()
    for i, sid in enumerate(ids or [None]):
        if sid == prev:
            continue
        prev = sid
        d = host.stack.styles.get(sid, {}) if sid is not None else {}
        font = d.get("font") if d.get("font") is not None else p.text_font_id
        size = d.get("size") if d.get("size") is not None else p.text_size
        style = d.get("style") if d.get("style") is not None else set(p.text_style)
        key = (font, size, frozenset(style))
        if key not in index:
            index[key] = len(table)
            table.append([0, key])
        table[index[key]][0] += 1
        runs.append((i, index[key]))
    from .render import MAC_FONT_IDS
    by_name = {v.lower(): k for k, v in MAC_FONT_IDS.items()}
    out = bytearray((len(runs) + 1).to_bytes(2, "big"))
    for start, k in runs + [(n + 1, 0xFFFF)]:
        out += start.to_bytes(2, "big") + k.to_bytes(2, "big")
    out += len(table).to_bytes(2, "big")
    for count, (font, size, style) in table:
        mac_font = by_name.get(host.font_name(font).lower(), font)
        face = sum(_FACE_BITS.get(x, 0) for x in style)
        ascent = int(size)
        height = int(size) + max(2, int(size) // 4) + 1
        out += (count.to_bytes(2, "big") + height.to_bytes(2, "big") + ascent.to_bytes(2, "big") +
                (mac_font & 0xFFFF).to_bytes(2, "big") + bytes([face & 0xFF, 0]) +
                int(size).to_bytes(2, "big") + bytes(6))
    return bytes(out)


def _apply_style_record(it, ref, data):
    host = it.host
    p = ref.b
    c = host.content_of(ref.a, p, ref.c, card=ref.d)
    if c is None or len(data) < 4:
        return
    nruns = int.from_bytes(data[:2], "big")
    runs = [(int.from_bytes(data[2 + 4 * i:4 + 4 * i], "big"), int.from_bytes(data[4 + 4 * i:6 + 4 * i], "big"))
            for i in range(nruns)]
    o = 2 + 4 * nruns
    nstyles = int.from_bytes(data[o:o + 2], "big")
    from .render import MAC_FONT_IDS
    sids = []
    for k in range(nstyles):
        e = data[o + 2 + 18 * k:o + 20 + 18 * k]
        if len(e) < 18:
            break
        mac_font = int.from_bytes(e[6:8], "big")
        face = e[8]
        size = int.from_bytes(e[10:12], "big")
        name = MAC_FONT_IDS.get(mac_font)
        font = host.font_id(name) if name else mac_font
        style = {x for x, b in _FACE_BITS.items() if face & b}
        sids.append(host._style_id(font, size, style))
    n = len(c.text or "")
    c.runs = [(start, sids[k]) for start, k in runs if k < len(sids) and start < max(n, 1)]
    host.changed(ref)


@xcmd("AppendDataToFile", "AppendToFile")
def append_data(it, a):
    """AppendDataToFile data, length, file: adds an entry."""
    data = str(_arg(a, 0)).replace("\n", "\r").encode("mac_roman", "replace")
    if ht.is_number(_arg(a, 1)):
        data = data[:max(0, int(ht.to_num(_arg(a, 1))))]
    _append_entry(_arg(a, 2), data)


@xcmd("AppendStyleDataToFile")
def append_style_data(it, a):
    """AppendStyleDataToFile "b"|"c", field number, file: adds an entry with
    the field's text styles."""
    ref = _field_ref(it, _arg(a, 0), _arg(a, 1))
    if ref is None:
        it.result = "Could not get the TE handle."
        return
    _append_entry(_arg(a, 2), _style_record(it, ref))


@xcmd("BuildIndex")
def build_index(it, a):
    """BuildIndex data file, index file: writes the offsets of the entries."""
    try:
        with open(_local(_arg(a, 0)), "rb") as fh:
            data = fh.read()
    except OSError:
        it.result = "Could not open the data file."
        return
    offs, o = [], 0
    while o + 4 <= len(data):
        offs.append(o)
        o += 4 + int.from_bytes(data[o:o + 4], "big")
    offs.append(len(data))
    p = _local(_arg(a, 1))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(b"".join(x.to_bytes(4, "big") for x in offs))


def _indexed_entry(n, data_file, index_file):
    try:
        with open(_local(index_file), "rb") as fh:
            idx = fh.read()
        with open(_local(data_file), "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    count = len(idx) // 4 - 1
    if not ht.is_number(n) or not 1 <= int(ht.to_num(n)) <= count:
        return None
    o = int.from_bytes(idx[4 * (int(ht.to_num(n)) - 1):4 * int(ht.to_num(n))], "big")
    size = int.from_bytes(data[o:o + 4], "big")
    return data[o + 4:o + 4 + size]


@xfcn("GetFileIndexedEntry")
def get_file_indexed_entry(it, a):
    """GetFileIndexedEntry(n, data file, index file): the text of entry n."""
    e = _indexed_entry(_arg(a, 0), _arg(a, 1), _arg(a, 2))
    if e is None:
        return "Error -- Could not find the entry."
    return e.decode("mac_roman", "replace").replace("\r", "\n")


@xcmd("ApplyFileIndxEntryToTE")
def apply_file_entry(it, a):
    """ApplyFileIndxEntryToTE n, data file, "b"|"c", field number, index
    file: gives the field the styles saved in entry n."""
    ref = _field_ref(it, _arg(a, 2), _arg(a, 3))
    if ref is None:
        it.result = "Could not get the TEHandle!"
        return
    e = _indexed_entry(_arg(a, 0), _arg(a, 1), _arg(a, 4))
    if e is None:
        it.result = "Error -- Could not find the entry."
        return
    _apply_style_record(it, ref, e)


# -- clipboard -----------------------------------------------------------------------------

@xcmd("SelectionToClip", "CopyToClip", "ToClip")
def selection_to_clip(it, a):
    """SelectionToClip field number, card field?, first char, last char
    (Voyager's Library: copies that part of the field), or text, or nothing
    for the current selection."""
    if len(a) >= 4 and all(ht.is_number(_arg(a, i)) for i in (0, 2, 3)):
        ref = _field(it, _arg(a, 0), "c" if ht.to_bool(_arg(a, 1, "false") or "false") else "b")
        if ref is None:
            it.result = "Incorrect number of parameters"
            return
        c = it.host.content_of(ref.a, ref.b, ref.c, card=ref.d)
        text = c.text if c is not None else ""
        s0, s1 = int(ht.to_num(_arg(a, 2))), int(ht.to_num(_arg(a, 3)))
        it.host.set_clipboard(text[max(0, s0 - 1):max(0, s1)])
        return
    text = str(a[0]) if a else it.host.selection_text()
    it.host.set_clipboard(text)


@xcmd("AppendToClip")
def append_to_clip(it, a):
    text = str(a[0]) if a else it.host.selection_text()
    it.host.set_clipboard(it.host.clipboard_text() + text)


# -- fields and styles -----------------------------------------------------------------------

def _field(it, num, layer="b"):
    word = "card field" if str(layer).lower().startswith("c") else "field"
    return ht.parse_ref(it, "%s %s" % (word, num))


@xcmd("ChangeFontSizes")
def change_font_sizes(it, a):
    """(field number, card field?, table) where table lines are "old,new":
    change every style run of that size."""
    ref = _field(it, _arg(a, 0), "c" if ht.to_bool(_arg(a, 1, "false") or "false") else "b")
    pairs = {}
    for ln in str(_arg(a, 2)).split("\n"):
        p = ln.split(",")
        if len(p) >= 2 and ht.is_number(p[0]) and ht.is_number(p[1]):
            pairs[int(ht.to_num(p[0]))] = int(ht.to_num(p[1]))
    if ref is not None and pairs:
        it.host.map_field_sizes(ref, pairs)


@xcmd("RemoveAttribute")
def remove_attribute(it, a):
    """(field number, card field?, style) removes one text style from a field."""
    ref = _field(it, _arg(a, 0), "c" if ht.to_bool(_arg(a, 1, "false") or "false") else "b")
    if ref is not None:
        it.host.remove_field_style(ref, str(_arg(a, 2)).lower(), None, None)


@xcmd("RemoveUB")
def remove_ub(it, a):
    """(field number, card field?, first char, last char) removes underline
    and bold from a range ("Plain" in the tool palette)."""
    ref = _field(it, _arg(a, 0), "c" if ht.to_bool(_arg(a, 1, "false") or "false") else "b")
    try:
        s0 = int(ht.to_num(_arg(a, 2, "1")))
        s1 = int(ht.to_num(_arg(a, 3, "0")))
    except ht.HTError:
        return
    if ref is not None:
        for st in ("underline", "bold"):
            it.host.remove_field_style(ref, st, s0, s1)


# -- windows -------------------------------------------------------------------------------

@xcmd("TextXWindow", "TextWindow")
def text_x_window(it, a):
    """TextXWindow id, name[, visible, style]: a floating text window. Its
    properties (text, rect, textfont, …) are set by the script, and clicks
    on it send textWindowClick char, line, name to the current card."""
    name = str(_arg(a, 1, "Text")) or "Text"
    visible = ht.to_bool(_arg(a, 2, "false") or "false") if len(a) > 2 else False
    it.host.window_create(name, "text", {"visible": visible})


@xcmd("Picture")
def picture_cmd(it, a):
    """picture name[, source[, windowStyle[, visible[, depth[, floating]]]]] —
    HyperCard's own picture window. `name` is a PICT resource (source
    "resource") or a file. Clicks send mouseDownInPicture name, where."""
    name = str(_arg(a, 0, "Picture"))
    source = str(_arg(a, 1, "file")).lower() or "file"
    style = str(_arg(a, 2, "zoom")).lower()
    visible = ht.to_bool(_arg(a, 3, "true") or "true") if len(a) > 3 else True
    png = it.host.picture_png(source, name)
    if png is None:
        it.result = "Couldn't find picture “%s”." % name
        return
    import os as _os
    title = _os.path.basename(name) if "/" in name else name.split(":")[-1]
    it.host.window_create(title, "picture", {"png": png, "visible": visible, "style": style})


# -- color (HyperCard 2.x Color Tools) ----------------------------------------------------------

_ADDCOLOR_EFFECTS = {
    "dissolve": ("dissolve", ""), "barndooropen": ("barn door", "open"), "barndoorclose": ("barn door", "close"),
    "irisopen": ("iris", "open"), "irisclose": ("iris", "close"), "fromleft": ("wipe", "right"),
    "fromright": ("wipe", "left"), "fromtop": ("wipe", "down"), "frombottom": ("wipe", "up"),
    "fromtopleft": ("wipe", "down"), "fromtopright": ("wipe", "down"), "frombottomleft": ("wipe", "up"),
    "frombottomright": ("wipe", "up"), "venetianblinds": ("venetian blinds", ""),
    "checkerboard": ("checkerboard", ""),
}


def _addcolor_effect(name, ticks):
    low = str(name).strip().lower().replace(" ", "")
    if not low or low in ("stamp", "plain", "none"):
        return None
    name, direction = _ADDCOLOR_EFFECTS.get(low, ("dissolve", ""))
    t = ht.to_num(ticks) if ht.is_number(ticks) else 30
    speed = "fast" if t < 20 else ("normal" if t < 60 else "slow")
    return (name, direction, speed)


def _addcolor_rect(text):
    try:
        v = [int(ht.to_num(x)) for x in str(text).split(",")]
    except ht.HTError:
        return None
    return v if len(v) in (2, 4) else None


def _addcolor_items(it, layer):
    from . import addcolor
    h = it.host
    t = addcolor.load(h.stack) if h.stack is not None else None
    if t is None or h.card is None:
        return []
    if layer == "bg":
        bg = h.bg_of(h.card)
        return t["backgrounds"].setdefault(addcolor.key(bg.id), []) if bg else []
    return t["cards"].setdefault(addcolor.key(h.card.id), [])


@xcmd("AddColor")
def add_color(it, a):
    """AddColor colorCard|colorBg [,effect, ticks] · install · remove ·
    colorButton|colorField, cd|bg, id, "r,g,b", bevel · colorRect, cd|bg, rect, "r,g,b", bevel ·
    colorPict|colorPictFile, cd|bg, name, loc or rect, o|t, effect, ticks ·
    changeObjectColor|changeObjectBounds, cd|bg, number, value · getObjectColor|getObjectBounds, cd|bg, number.
    The colors stored with the stack are drawn whenever a card is shown; objects drawn by
    command last until the card changes."""
    from . import addcolor
    h = it.host
    state = h.addcolor_state
    cmd = str(_arg(a, 0)).strip().lower()
    layer = "bg" if str(_arg(a, 1)).strip().lower() in ("bg", "bkgnd", "background") else "cd"
    if cmd == "install":
        state["enabled"] = True
        h.color_refresh(None)
    elif cmd == "remove":
        state["enabled"] = False
        h.color_refresh(None)
    elif cmd in ("colorcard", "colorbg", "colorbkgnd", "colorbackground", "colorstack"):
        state["enabled"] = True
        h.color_refresh(_addcolor_effect(_arg(a, 1), _arg(a, 2)))
    elif cmd in ("colorbutton", "colorfield"):
        rgb = addcolor.parse_rgb(_arg(a, 3))
        if rgb is None or not ht.is_number(_arg(a, 2)):
            return
        bevel = int(ht.to_num(_arg(a, 4))) if ht.is_number(_arg(a, 4)) else 0
        state["extra"].append({"type": "button" if cmd == "colorbutton" else "field", "layer": layer,
                               "id": int(ht.to_num(_arg(a, 2))), "rgb": rgb, "bevel": bevel, "card": h.card})
        h.color_refresh(None)
    elif cmd == "colorrect":
        r = _addcolor_rect(_arg(a, 2))
        rgb = addcolor.parse_rgb(_arg(a, 3))
        if not r or len(r) != 4 or rgb is None:
            return
        bevel = int(ht.to_num(_arg(a, 4))) if ht.is_number(_arg(a, 4)) else 0
        state["extra"].append({"type": "rect", "layer": layer, "rect": r, "rgb": rgb, "bevel": bevel,
                               "card": h.card})
        h.color_refresh(None)
    elif cmd in ("colorpict", "colorpictfile", "colorpicture"):
        transparent = str(_arg(a, 4)).strip().lower().startswith("t")
        f = addcolor.picture_for_script(h, str(_arg(a, 2)), cmd != "colorpictfile", transparent)
        if f is None:
            it.result = "Couldn't find picture “%s”." % _arg(a, 2)
            return
        path, w, hgt = f
        r = _addcolor_rect(_arg(a, 3)) or [0, 0]
        if len(r) == 2:
            r = [r[0], r[1], r[0] + w, r[1] + hgt]
        state["extra"].append({"type": "pict", "layer": layer, "rect": r, "file": path, "card": h.card})
        h.color_refresh(_addcolor_effect(_arg(a, 5), _arg(a, 6)))
    elif cmd in ("changeobjectcolor", "changeobjectbounds", "getobjectcolor", "getobjectbounds",
                 "deleteobject", "hideobject", "showobject"):
        items = _addcolor_items(it, layer)
        n = int(ht.to_num(_arg(a, 2))) if ht.is_number(_arg(a, 2)) else 0
        if not 1 <= n <= len(items):
            it.result = "No such object"
            return
        obj = items[n - 1]
        if cmd == "changeobjectcolor":
            rgb = addcolor.parse_rgb(_arg(a, 3))
            if rgb is not None:
                obj["rgb"] = rgb
        elif cmd == "changeobjectbounds":
            r = _addcolor_rect(_arg(a, 3))
            if r and len(r) == 4 and "rect" in obj:
                obj["rect"] = r
        elif cmd == "getobjectcolor":
            it.result = ",".join(str(x) for x in obj.get("rgb", []))
            return
        elif cmd == "getobjectbounds":
            it.result = ",".join(str(x) for x in obj.get("rect", []))
            return
        elif cmd == "deleteobject":
            del items[n - 1]
        else:
            obj["hidden"] = cmd == "hideobject"
        h.color_refresh(None)
    # anything else (getBitsCall, …) has nothing to do here


@xcmd("colorizeHC", "ColorizeHC")
def colorize_hc(it, a):
    """colorizeHC: an older color XCMD; HCX draws the colors stored with the stack anyway."""
    it.host.color_refresh(None)


@xfcn("GetDepth", "MaxDepth", "ScreenDepth", "GetScreenDepth")
def get_depth(it, a):
    """Screen color depth in bits. HCX draws in full color, but stacks of the time
    only know 1 to 16 bits ("thousands of colors") and some refuse larger values."""
    return "16"


@xcmd("SetDepth", "HideDesktop", "ShowDesktop", "MaxMem", "Compact", "PurgeMem", "FlushMem")
def max_mem(it, a):
    return None


@xcmd("SetCustomIconBit", "SetFileInfo", "SetCreator", "SetType")
def set_custom_icon(it, a):
    return None


# -- sound and video ------------------------------------------------------------------------------

@xcmd("PlayAIFFFile", "PlaySound", "PlayAIFF", "PlaySnd", "SoundPlay")
def play_aiff(it, a):
    name = str(_arg(a, 0))
    p = _local(name)
    if os.path.exists(p):
        it.host.play_file(p)
    else:
        it.host.play_sound(name)


@xcmd("Movie", "QTMovie", "PlayMovie")
def movie(it, a):
    """Movie fileName[, windowStyle, loc, visible, floating]: shows a video
    window when the file can be found and GStreamer can play it."""
    name = str(_arg(a, 0))
    p = _local(name)
    if os.path.exists(p):
        it.host.window_create(os.path.basename(p), "movie", {"file": p, "visible": True})
    else:
        it.result = "Couldn't open the movie “%s”." % name


# -- file dialogs ------------------------------------------------------------------------

_TYPE_PATTERNS = {
    "TEXT": ("Text", ["*.txt", "*.text", "*.TXT"]),
    "PICT": ("PICT", ["*.pict", "*.pct", "*.pic", "*.PICT"]),
    "STAK": ("HyperCard", ["*.stak", "*.stack", "*.hcxs", "*.img", "*.sit", "*.hqx", "*.bin"]),
    "SND ": ("Sound", ["*.snd", "*.aiff", "*.aif", "*.wav"]),
}


def _patterns(types):
    for t in types:
        t = str(t).strip().upper()
        if t in _TYPE_PATTERNS:
            return _TYPE_PATTERNS[t]
        if t[:3] == "SND":
            return _TYPE_PATTERNS["SND "]
    return None


@xfcn("FileName", "GetFileName", "SFGetFile", "GetFile", "FilePath", "FileOpen")
def file_name(it, a):
    """FileName(["TYPE"...]): the full path of a file the user picks, or empty."""
    return it.host.choose_file("file", "", "", _patterns(a)) or ""


@xfcn("NewFileName", "SFPutFile", "PutFile", "SaveFileName")
def new_file_name(it, a):
    """NewFileName(prompt, default name): the path for a new file, or empty."""
    return it.host.choose_file("save", _arg(a, 0), _arg(a, 1)) or ""


@xfcn("StandardFile")
def standard_file(it, a):
    """StandardFile("get|put|dir|apd|add[:button names]", ...) by Nigel Perry."""
    fn = str(_arg(a, 0)).split(":", 1)[0].strip().lower()
    if fn in ("put", "apd", "add"):
        return it.host.choose_file("save", _arg(a, 2), _arg(a, 1)) or ""
    if fn == "dir":
        return it.host.choose_file("folder", _arg(a, 1)) or ""
    return it.host.choose_file("file", "", "", _patterns(a[1:])) or ""


# -- dialogs from DLOG/DITL resources (ShowDialog, DialogDisplay, Dialoger) and ShowList ----

def _truth(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def _first_user(spec):
    from .macdialog import items_of
    users = items_of(spec, "user")
    return users[0] if users else None


def _edits(spec, values):
    from .macdialog import items_of
    return [values.get(i["n"], "") for i in items_of(spec, "edit")]


def _menu_items(text):
    """ShowDialog type 4 menus: comma-separated items, "<B" style prefixes,
    "(" for a disabled item, "(-" for a separator line."""
    out = []
    for x in str(text).split(","):
        while x[:1] == "<" and len(x) >= 2:
            x = x[2:]
        if x.startswith("(-"):
            out.append("—")
            continue
        out.append(x.lstrip("("))
    return out


@xcmd("ShowDialog")
def show_dialog(it, a):
    """ShowDialog type, DLOG id, … by Jay Hodgdon (types 1 to 7)."""
    from . import macdialog as md
    kind = int(ht.to_num(_arg(a, 0, "1"))) if ht.is_number(_arg(a, 0, "1")) else 1
    if kind == 1:
        spec = md.load(it, _arg(a, 1), [_arg(a, 2)])
        if spec is None:
            return ""
        spec["outline_default"] = _truth(_arg(a, 3, "true"))
        n, _v = it.host.ditl_dialog(spec)
        return "" if n is None else str(n)
    if kind in (2, 3, 4):
        spec = md.load(it, _arg(a, 1), [_arg(a, 2), _arg(a, 3)])
        if spec is None:
            return ""
        spec["cancel"] = 2
        ed = md.item(spec, 3)
        if ed is not None and ed["kind"] == "edit":
            ed["text"] = str(_arg(a, 4))
        checks = md.items_of(spec, "check")
        radios = md.items_of(spec, "radio")
        if kind == 2:
            states = [_truth(x) for x in str(_arg(a, 5)).split(",")] if _arg(a, 5) != "" else []
            for i, c in enumerate(checks):
                c["checked"] = states[i] if i < len(states) else False
        elif kind == 3:
            want = int(ht.to_num(_arg(a, 5))) if ht.is_number(_arg(a, 5)) else (radios[0]["n"] if radios else 0)
            for r in radios:
                r["checked"] = r["n"] == want
        else:
            u = _first_user(spec)
            if u is not None:
                u["kind"] = "popup"
                u["menu"] = _menu_items(_arg(a, 5))
                u["selected_item"] = int(ht.to_num(_arg(a, 6))) if ht.is_number(_arg(a, 6)) else 1
        n, values = it.host.ditl_dialog(spec)
        if n is None or n == 2:
            return ""
        lines = []
        if kind == 2 and checks:
            lines.append(",".join("true" if values.get(c["n"]) else "false" for c in checks))
        elif kind == 3:
            on = [r["n"] for r in radios if values.get(r["n"])]
            lines.append(str(on[0]) if on else "")
        elif kind == 4:
            u = md.items_of(spec, "popup")
            lines.append(str(values.get(u[0]["n"], 1)) if u else "")
        lines += _edits(spec, values)
        return "\n".join(lines)
    if kind == 7:
        spec = md.load(it, _arg(a, 1), [_arg(a, 2)])
        if spec is None:
            return ""
        spec["cancel"] = 2
        spec["font"], spec["size"] = _arg(a, 3) or None, (int(ht.to_num(_arg(a, 4))) if ht.is_number(_arg(a, 4)) else 12) or 12
        u = _first_user(spec)
        if u is not None:
            u["kind"] = "list"
            u["lines"] = [x for x in str(_arg(a, 5)).split("\n")]
            if u["lines"] and u["lines"][-1] == "":
                u["lines"].pop()
            sel = int(ht.to_num(_arg(a, 6))) if ht.is_number(_arg(a, 6)) else 0
            u["selected"] = [sel] if sel > 0 else []
            u["multiple"] = str(_arg(a, 7)).strip().lower() == "many"
        n, values = it.host.ditl_dialog(spec)
        if n is None:
            n = 2
        sel = values.get(u["n"], []) if u is not None else []
        return "%d\n%s" % (n, ",".join(str(x) for x in sel))
    if kind in (5, 6):
        return _picture_dialog(it, a, kind)
    return ""


def _picture_dialog(it, a, kind):
    """ShowDialog types 5 and 6: help screens made of PICTs shown in item 2."""
    from . import macdialog as md
    spec = md.load(it, _arg(a, 1))
    if spec is None:
        return ""
    area = md.item(spec, 2)
    if area is None:
        return ""
    area["kind"] = "user"
    area["picture_area"] = True
    spec["cancel"] = 1
    if kind == 5:
        first = int(ht.to_num(_arg(a, 2))) if ht.is_number(_arg(a, 2)) else 3
        state = {"btn": first}

        def show(ui):
            ui.set_png(2, md.pict_png(it, 1000 + state["btn"]))
            for b in md.items_of(spec, "button"):
                if b["n"] >= 3:
                    ui.set_sensitive(b["n"], b["n"] != state["btn"])

        def click(n, ui):
            if n >= 3:
                state["btn"] = n
                show(ui)
                return "keep"
            return None
        spec["on_show"] = show
        it.host.ditl_dialog(spec, click)
        return ""
    base = int(ht.to_num(_arg(a, 2))) if ht.is_number(_arg(a, 2)) else 0
    total = max(1, int(ht.to_num(_arg(a, 3))) if ht.is_number(_arg(a, 3)) else 1)
    state = {"k": max(1, min(total, int(ht.to_num(_arg(a, 4))) if ht.is_number(_arg(a, 4)) else 1))}
    cycle = _truth(_arg(a, 5))

    def show(ui):
        k = state["k"]
        r = md.find(it, ("PICT",), base + k - 1)
        ui.set_png(2, md.pict_png(it, base + k - 1))
        ui.set_text(5, r[2] if r is not None else "")
        ui.set_text(6, "Page %d of %d" % (k, total))
        if not cycle:
            ui.set_sensitive(3, k > 1)
            ui.set_sensitive(4, k < total)

    def click(n, ui):
        if n in (3, 4):
            k = state["k"] + (1 if n == 4 else -1)
            state["k"] = (k - 1) % total + 1 if cycle else max(1, min(total, k))
            show(ui)
            return "keep"
        return None
    spec["on_show"] = show
    n, _v = it.host.ditl_dialog(spec, click)
    return "true" if n == 7 else "false"


_DD_STATE = {}


@xfcn("DialogDisplay")
def dialog_display(it, a):
    """DialogDisplay(command, …) by Software Perspectives: setText,
    setButtonName, setButtonDefault, setTextFont/Size/Face, showDialog, getVersion."""
    from . import macdialog as md
    st = _DD_STATE.setdefault(id(it.host), {"texts": [], "buttons": [], "default": True,
                                            "font": None, "size": None})
    cmd = str(_arg(a, 0)).strip()
    low = cmd.lower()
    if low == "settext":
        st["texts"] = list(a[1:5])
    elif low == "setbuttonname":
        st["buttons"] = list(a[1:4])
    elif low == "setbuttondefault":
        st["default"] = _truth(_arg(a, 1))
    elif low == "settextfont":
        st["font"] = str(_arg(a, 1)) or None
    elif low == "settextsize":
        st["size"] = int(ht.to_num(_arg(a, 1))) if ht.is_number(_arg(a, 1)) else None
    elif low == "settextface":
        pass
    elif low == "getversion":
        return "DialogDisplay 2.0 (HCX)"
    elif low == "showdialog" or ht.is_number(cmd):
        rid = _arg(a, 1) if low == "showdialog" else cmd
        texts = st["texts"] if low == "showdialog" else list(a[1:5])
        spec = md.load(it, rid, texts)
        if spec is None:
            return "Error: dialog %s not found" % rid
        for k, name in enumerate(st["buttons"]):
            b = md.item(spec, k + 1)
            if b is not None and b["kind"] == "button" and str(name) != "":
                b["text"] = str(name)
        spec["outline_default"] = st["default"]
        spec["font"], spec["size"] = st["font"], st["size"]
        n, _v = it.host.ditl_dialog(spec)
        return "" if n is None else str(n)
    return ""


@xfcn("Dialoger")
def dialoger(it, a):
    """Dialoger("!DLOG name" & return & options, params…) by Leonard Buck.
    Only the use found in stacks is supported: text parameters fill ^0-^3
    and a "userList font,size,1|AnySelection" & return & lines parameter puts
    a list in the first user item. The result has one line per DITL item:
    1 for the button clicked, the selected line numbers of a list, the text
    of edit fields, 1 or 0 for check boxes and radio buttons."""
    from . import macdialog as md
    head = str(_arg(a, 0)).split("\n")
    name = head[0].strip()
    params, lists = [], []
    for p in a[1:]:
        s = str(p)
        if s.lower().startswith("userlist"):
            lines = s.split("\n")
            opts = lines[0].split(None, 1)[1] if " " in lines[0] else ""
            o = [x.strip() for x in opts.split(",")]
            lists.append({"lines": [x for x in lines[1:]], "font": o[0] if o and o[0] else None,
                          "size": int(o[1]) if len(o) > 1 and o[1].isdigit() else None,
                          "multiple": len(o) > 2 and o[2].lower() != "1"})
        else:
            params.append(s)
    spec = md.load(it, name, params)
    if spec is None and name.startswith("!"):
        spec = md.load(it, name[1:], params)
    if spec is None:
        return "Error: no dialog “%s”" % name
    spec["cancel"] = 2
    users = md.items_of(spec, "user")
    for u, l in zip(users, lists):
        u.update(kind="list", lines=l["lines"], multiple=l["multiple"], selected=[])
        if l["font"]:
            u["font"] = l["font"]
        if l["size"]:
            u["size"] = l["size"]
    n, values = it.host.ditl_dialog(spec)
    out = []
    for i in spec["items"]:
        k = i["kind"]
        if k == "button":
            out.append("1" if i["n"] == n else "")
        elif k == "list":
            out.append(",".join(str(x) for x in values.get(i["n"], [])))
        elif k == "edit":
            out.append(str(values.get(i["n"], "")).replace("\n", " "))
        elif k in ("check", "radio"):
            out.append("1" if values.get(i["n"]) else "0")
        else:
            out.append("")
    return "\n".join(out)


@xfcn("ShowList")
def show_list(it, a):
    """ShowList(list, prompt, multiple, ?, "OK,Cancel^", selected line [, h, v])
    from Apple's externals. Answers the button name on line 1 and the chosen
    line numbers on line 2 (empty when the button ends with ^, the cancel button)."""
    items = str(_arg(a, 0))
    prompt = _arg(a, 1)
    delim = "\n"
    rest = list(a[2:])
    if ht.is_number(prompt) and len(a) >= 3:
        delim = chr(int(ht.to_num(prompt)))
        prompt, rest = _arg(a, 2), list(a[3:])
    multiple = _truth(rest[0]) if rest else False
    buttons = str(rest[2]) if len(rest) > 2 and str(rest[2]) else "OK,Cancel^"
    sel = int(ht.to_num(rest[3])) if len(rest) > 3 and ht.is_number(rest[3]) else 0
    lines = items.split(delim)
    if lines and lines[-1] == "":
        lines.pop()
    names = [b for b in buttons.split(",") if b != ""][:4]
    width = 300
    list_h = min(16 * max(4, len(lines)), 200)
    prompt_h = 16 * max(1, min(6, len(str(prompt)) // 40 + 1 + str(prompt).count("\n")))
    top = 8 + prompt_h + 6
    bottom = top + list_h + 10
    spec = {"title": "", "proc": 1, "width": width, "height": bottom + 30, "default": 1, "items": [], "cancel": None}
    x = width - 8
    for k, b in reversed(list(enumerate(names))):
        bw = max(60, 8 * len(b.rstrip("^")) + 16)
        spec["items"].append({"n": k + 1, "kind": "button", "text": b.rstrip("^"), "enabled": True,
                              "rect": (x - bw, bottom, x, bottom + 20)})
        if b.endswith("^"):
            spec["cancel"] = k + 1
        x -= bw + 10
    spec["items"].sort(key=lambda i: i["n"])
    nb = len(names)
    spec["items"].append({"n": nb + 1, "kind": "static", "text": str(prompt), "enabled": False,
                          "rect": (8, 8, width - 8, 8 + prompt_h)})
    spec["items"].append({"n": nb + 2, "kind": "list", "lines": lines, "multiple": multiple,
                          "selected": [sel] if sel > 0 else [], "enabled": True,
                          "rect": (8, top, width - 8, top + list_h)})
    n, values = it.host.ditl_dialog(spec)
    if n is None:
        n = spec["cancel"] or len(names)
    name = names[n - 1].rstrip("^") if 0 < n <= len(names) else ""
    if spec["cancel"] == n:
        return name + "\n"
    return name + "\n" + ",".join(str(x) for x in values.get(nb + 2, []))


# -- speech (MacinTalk and PlainTalk externals) --------------------------------------------

def _speech():
    from . import speech
    return speech.get()


@xcmd("Say", "MacinTalkSay", "Speak", "SpeakString", "Talk", "SayIt", "SpeakIt")
def say(it, a):
    """Say text — HyperMacinTalk, MacinTalkSay, SpeakString, Talk text[, rate, pitch]."""
    if len(a) >= 3 and ht.is_number(a[1]):
        _speech().set_rate_wpm(ht.to_num(a[1]))
        if ht.is_number(a[2]):
            _speech().set_pitch_hz(ht.to_num(a[2]))
    _speech().speak(_arg(a, 0))


@xcmd("SayPhonetic")
def say_phonetic(it, a):
    """MacinTalk phonetic text has no Linux equivalent: it is not spoken."""
    return


@xfcn("Phonemes", "Reader")
def phonemes(it, a):
    return str(_arg(a, 0))


@xcmd("TurnSpeechOn", "TurnSpeechOff", "SpeechOn", "SpeechOff")
def speech_on_off(it, a):
    return


@xcmd("SetRate", "SetSpeechRate")
def set_rate(it, a):
    if ht.is_number(_arg(a, 0)):
        _speech().set_rate_wpm(ht.to_num(_arg(a, 0)))


@xcmd("SetPitch", "SetSpeechPitch")
def set_pitch(it, a):
    if ht.is_number(_arg(a, 0)):
        v = ht.to_num(_arg(a, 0))
        _speech().set_pitch_hz(v if v > 0 else 110)


@xcmd("StopSpeech", "StopSpeaking", "ShutUp")
def stop_speech(it, a):
    _speech().stop()


@xcmd("Vocalise", "Vocalize")
def vocalise(it, a):
    """Vocalise "command", … by Alex Metcalf: quick speak, prepare to speak,
    speak, stop speaking, speak faster/slower/higher/lower, are you speaking,
    list available voices, check for voice, no more to speak."""
    from . import speech
    sp = _speech()
    cmd = str(_arg(a, 0)).strip().lower().rstrip("?")
    if cmd == "quick speak":
        sp.speak(_arg(a, 1), "")
    elif cmd == "prepare to speak":
        v = str(_arg(a, 1))
        sp.voice = "" if v.lower() in ("default", "") else v
    elif cmd == "speak":
        sp.speak(_arg(a, 1))
    elif cmd in ("stop speaking", "no more to speak"):
        sp.stop()
    elif cmd == "speak faster":
        sp.set_rate_wpm(sp.rate + 30)
    elif cmd == "speak slower":
        sp.set_rate_wpm(sp.rate - 30)
    elif cmd == "speak higher":
        sp.pitch = min(100, sp.pitch + 15)
    elif cmd == "speak lower":
        sp.pitch = max(-100, sp.pitch - 15)
    elif cmd == "are you speaking":
        return "yes" if sp.busy() else "no"
    elif cmd == "list available voices":
        return "\n".join(speech.MAC_VOICES) if sp.available() else ""
    elif cmd == "check for voice":
        return "" if sp.available() else "Speech isn't available."
    return ""


@xfcn("SpeakText")
def speak_text(it, a):
    """SpeakText(command, …) by Software Perspectives."""
    from . import speech
    sp = _speech()
    cmd = str(_arg(a, 0)).strip().lower()
    names = list(speech.MAC_VOICES)
    if cmd == "checkforspeech":
        return "true" if sp.available() else "false"
    if cmd == "speakstring":
        sp.speak(_arg(a, 1), "")
    elif cmd == "speaktext":
        v = _arg(a, 1)
        voice = names[int(ht.to_num(v)) - 1] if ht.is_number(v) and 0 < int(ht.to_num(v)) <= len(names) else ""
        sp.speak(_arg(a, 3), voice)
    elif cmd in ("stopspeech", "stopspeechat"):
        sp.stop()
    elif cmd == "isspeaking":
        return "true" if sp.busy() else "false"
    elif cmd == "countvoices":
        return str(len(names)) if sp.available() else "0"
    elif cmd == "getvoicelist":
        return "\n".join(names) if sp.available() else ""
    elif cmd == "getvoiceinfo":
        v = _arg(a, 1)
        k = int(ht.to_num(v)) if ht.is_number(v) else 0
        return names[k - 1] if 0 < k <= len(names) else ""
    elif cmd == "setspeechrate":
        if ht.is_number(_arg(a, 1)):
            sp.set_rate_wpm(ht.to_num(_arg(a, 1)))
    elif cmd == "getspeechrate":
        return str(sp.rate)
    elif cmd == "setspeechpitch":
        if ht.is_number(_arg(a, 1)):
            sp.pitch = max(-100, min(100, int((ht.to_num(_arg(a, 1)) - 45) * 3)))
    elif cmd == "getspeechpitch":
        return str(int(45 + sp.pitch / 3))
    elif cmd == "getversion":
        return "SpeakText 2.0 (HCX)"
    return ""
