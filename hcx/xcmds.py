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


def _strip_marks(text):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def _intl_key(text):
    """Sort key like the International Utilities' IUMagString: letters
    without case or accents first, then the Mac Roman bytes break ties."""
    return (_strip_marks(text).upper(), text.encode("mac_roman", "replace"))


_SEP_NAMES = {"space": " ", "return": "\n", "tab": "\t", "quote": '"'}


def _number_35(s):
    """FullSort 3.5's number in a string: digits from the first digit (or
    point), then any one character followed by digits is the decimal
    point; a minus sign just before makes it negative."""
    i = next((k for k, c in enumerate(s) if c.isdigit() or c == "."), -1)
    if i < 0:
        return None
    neg = i > 0 and s[i - 1] == "-"
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    whole = s[i:j]
    frac = ""
    if j + 1 < len(s) and s[j + 1].isdigit():
        k = j + 1
        while k < len(s) and s[k].isdigit():
            k += 1
        frac = s[j + 1:k]
    v = float((whole or "0") + "." + (frac or "0"))
    return -v if neg else v


def _number_39(s):
    """FullSort 3.9's: the number that starts at the first digit (with a
    point and a sign just before it), read like SANE's str2num."""
    import re
    i = next((k for k, c in enumerate(s) if c.isdigit()), -1)
    if i < 0:
        return None
    if i > 0 and s[i - 1] == ".":
        i -= 1
    if i > 0 and s[i - 1] in "+-":
        i -= 1
    return float(re.match(r"[+-]?\d*\.?\d*(?:[eE][+-]?\d+)?", s[i:]).group(0))


def _date_key(s, time_only):
    import re
    if time_only:
        mt = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?", s)
        if not mt:
            return None
        h, mi, sec = int(mt.group(1)), int(mt.group(2)), int(mt.group(3) or 0)
        ampm = (mt.group(4) or "").upper()
        if ampm:
            h = h % 12 + (12 if ampm == "PM" else 0)
        return (h, mi, sec)
    mt = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", s)
    if not mt:
        return None
    mo, d, y = int(mt.group(1)), int(mt.group(2)), int(mt.group(3))
    if y < 100:
        y += 1900 if y >= 4 else 2000   # the Mac's window: 1904-2003
    return (y, mo, d)


def _sort_chunk(entry, chunk, item_delim):
    """The sort key of one entry for "c=char|word|item x[ to y]", or None
    when the entry doesn't have that chunk."""
    kind, first, last = chunk
    if kind == "char":
        if last == "*" or len(entry) < first:
            return None
        return entry[first - 1:(last or first)]
    if kind == "word":
        import re
        spans = [m.span() for m in re.finditer(r"[^ ]+", entry)]
    else:
        spans, start = [], 0
        for part in entry.split(item_delim):
            spans.append((start, start + len(part)))
            start += len(part) + len(item_delim)
        if last == "*":
            return None
    if len(spans) < first:
        return None
    end = spans[-1][1] if last == "*" else spans[min(last or first, len(spans)) - 1][1]
    return entry[spans[first - 1][0]:end]


@xfcn("FullSort")
def full_sort(it, a):
    """FullSort(text[, "l=<sep>"][, "i=<sep>"][, "t=a|n|d|i|t|u"][,
    "d=a|d|r|l"][, "c=char|word|item x[ to y|*]"][, sort list]) by
    Frédéric Rinaldi. Sorts the lines (or the items, or the words: the
    separator is found in the text) and puts the new order in the global
    FullSortList, so "d=l" can sort another list the same way. Entries
    without the sort key keep their order at the end. Follows versions
    2.7 to 4.0, which differ in small ways."""
    import random
    import re
    code = _ext_code(it, "FullSort")
    mt = re.search(rb"FullSort (\d+)\.(\d+)", code)
    ver = (int(mt.group(1)), int(mt.group(2))) if mt else (4, 0)
    new = ver >= (3, 6)
    a = [str(x) for x in a]

    def fail(msg):
        it.globals["fullsortlist"] = ""
        it.globals["fullsortsep"] = ""
        return "Error : " + msg

    if not a:
        return fail("Missing input parameter")
    text = a[0]
    if text in ("!", "?", "="):
        return {"!": "FullSort %d.%d, © Frederic RINALDI %s" % (ver + ({
                    (2, 7): "1990,1991", (3, 9): "1990…1993", (4, 0): "1990…1998"}.get(ver, "1990…1992"),)),
                "?": 'FullSort(<text>[,"%s"][,"t=a|n|d|i|t|u"][,"d=a|d|r|l"]'
                     '[,"c=char x|word x|item x[ to y|*]"][,<sort list>]' %
                     ('s=<sep>' if ver < (2, 8) else 'l=<sep>"][,"i=<sep>'),
                "=": "<sorted list>"}[text]
    if not text and ver >= (2, 8):
        return ""
    first_chars = "TDCS" if ver < (2, 8) else "TDCLIS"
    letters = "[t,d,c,s]" if ver < (2, 8) else "[t,d,c,l,i]"
    sort_type = direction = chunk = line_sep = item_sep = sort_list = None
    for p in a[1:]:
        if p[:1].isdigit() and sort_list is None:
            sort_list = p
            continue
        if p[:1].upper() not in first_chars or not p:
            return fail("First char of param 2..n must be " + letters)
        if p[1:2] != "=":
            return fail('Second char of param 2..n must be "="')
        key, val = p[0].upper(), p[2:]
        if key == "T":
            if sort_type:
                return fail("Sort type defined twice")
            if val[:1].upper() not in tuple("ADINTU"):
                return fail('Char following "T=" must be [a,d,i,n,t,u]')
            sort_type = val[0].upper()
        elif key == "D":
            if direction:
                return fail("Sort direction defined twice")
            if val[:1].upper() not in tuple("ADRL"):
                return fail('Char following "D=" must be [a,d,r,l]')
            direction = val[0].upper()
        elif key in ("L", "S", "I"):
            sep = val if len(val) == 1 else _SEP_NAMES.get(val.lower())
            if key == "I":
                if item_sep is not None:
                    return fail("Item separator defined twice")
            elif line_sep is not None:
                return fail("Line separator defined twice")
            if sep is None:
                return fail("Bad separator param" if new else "Bad line separator param")
            if key == "I":
                item_sep = sep
            else:
                line_sep = sep
        else:
            if chunk:
                return fail("Sort chunk defined twice")
            up = p.upper()
            kind = up[2:6]
            if kind not in ("CHAR", "WORD", "ITEM"):
                return fail('"C=" %smust be followed by [char,word,item]' % ("param " if ver < (2, 8) else ""))
            if up[6:7] != " ":
                return fail('"C=%s" must be followed by a space' % kind)
            m = re.match(r"\d+", up[7:])
            if not m or int(m.group(0)) < 1:
                return fail('"%s" must be followed by a positive integer' % up[:7])
            start, rest = int(m.group(0)), up[7 + m.end():]
            stop = None
            if rest:
                if not rest.startswith(" TO ") or len(rest) == 4:
                    return fail('"%s" must be followed by " to X"' % up[:7 + m.end()])
                tail = rest[4:]
                if tail[:1] == "*":
                    stop = "*"
                else:
                    m2 = re.match(r"\d+", tail)
                    if not m2 or int(m2.group(0)) < 1:
                        return fail('" to " must be followed by a positive integer or "*"')
                    stop = int(m2.group(0))
                    if stop < start:
                        return fail("Stop chunk must be greater that start chunk")
            chunk = (kind.lower(), start, stop)
    sort_type, direction = sort_type or "A", direction or "A"
    if not text:        # only 2.7 gets here with no text
        it.globals["fullsortlist"] = it.globals["fullsortsep"] = ""
        return ""
    if sort_type in ("D", "T") and not chunk and ver >= (2, 8):
        return fail("Need to define a chunk for date/time sorting" if new else
                    "Chunk param required for date/time sorting")
    if item_sep is None:
        item_sep = it.props.get("itemdelimiter", ",") if ver >= (2, 8) else ","
    auto = line_sep is None
    if auto:
        if new and chunk and chunk[0] == "item":
            line_sep = "\n"
        else:
            line_sep = next((c for c in ("\n", ",", " ") if c in text and (c != " " or ver >= (2, 8))),
                            "\n" if ver >= (2, 8) else ",")
    if chunk and chunk[0] == "word" and line_sep == " ":
        return fail('"c=word" cannot be used with word sorting')
    if chunk and chunk[0] == "item" and line_sep == item_sep and (auto or new) and ver >= (2, 8):
        return fail("Line and item delimiters must be different")
    if ver < (2, 8) and text.endswith("\n"):
        text = text[:-1]
    entries = text.split(line_sep)
    trailing = ""
    if entries[-1] == "" and len(entries) > 1 and ver >= (2, 8) and not new:
        entries.pop()
        trailing = line_sep if ver >= (3, 5) else ""
    if direction == "L":
        if sort_list is None:
            sort_list = it.globals.get("fullsortlist", "")
            if not sort_list:
                return fail("Missing Sort list parameter")
        order = []
        for n, x in enumerate(sort_list.split(","), 1):
            if not x.isdigit() or int(x) < 1:
                return fail("Bad item %d in Sort list" % n)
            order.append(int(x))
        out = [entries[k - 1] if k <= len(entries) else "" for k in order]
        out += entries[len(order):]
        return line_sep.join(out) + ("" if new else trailing)
    keyed, rest = [], []
    for n, e in enumerate(entries):
        k = _sort_chunk(e, chunk, item_sep) if chunk else e
        if k is not None and sort_type == "N":
            k = (_number_39 if new else _number_35)(k)
        elif k is not None and sort_type in ("D", "T"):
            k = _date_key(k, sort_type == "T")
        elif k is not None and sort_type == "U":
            k = _strip_marks(k).upper()
        elif k is not None and sort_type == "I":
            k = _intl_key(k)
        elif k is not None:
            k = k.encode("mac_roman", "replace")
        if k is None or (new and e == ""):
            rest.append(n)
        else:
            keyed.append((k, n))
    if direction == "R":
        random.shuffle(keyed)
        order = [n for _k, n in keyed]
    else:
        order = [n for _k, n in sorted(keyed, key=lambda t: t[0], reverse=direction == "D")]
    order += rest
    it.globals["fullsortlist"] = ",".join(str(n + 1) for n in order)
    it.globals["fullsortsep"] = line_sep
    out = line_sep.join(entries[n] for n in order)
    return out + trailing if not new else out


@xfcn("Search")
def search(it, a):
    """Search(container, string, searchWhole[, every]) from Apple's 1990
    sample externals: "line,item" of each place the string starts (case
    and accents count), one per line, each line ending in a return. Item
    numbers count the commas from the start of the text, not of the line.
    With searchWhole only the starts of words (after a space or a return)
    are tried; a fourth parameter, whatever its value, asks for every
    place instead of the first."""
    code = _ext_code(it, "Search")
    dts = b"Container,theString" in code
    v12 = b"Search XFCN 1.2" in code     # 1995: any case, and "line,item,char"
    a = [str(x) for x in a]
    usage12 = "Form: Search(<text>,<search string>,<search whole>[,<every occurrence>])"
    if v12:     # 1.2 reports its errors in the global gXCMDError
        it.globals["gxcmderror"] = usage12 if len(a) not in (3, 4) and a not in (["?"], ["!"]) else ""
    if a == ["!"] and not dts:
        return "Search XFCN 1.%d ©%d Apple Computer, Inc. All Rights Reserved." % ((2, 1995) if v12 else (1, 1990))
    if v12 and a == ["?"]:
        return usage12
    if len(a) not in (3, 4):
        return "" if v12 else \
            "Form: Search(Container,theString,searchWhole,Every Occurrence Flag)" if dts else \
            "Form: Search(container, theString, searchWhole, Every Occurrence Flag)"
    text, pat = a[0].replace("\r", "\n"), a[1].replace("\r", "\n")
    if dts and len(pat) < 2:
        return "String to match must be at least 2 characters long"
    if v12 and not pat:
        return ""
    whole = _bool_param(a[2])
    if whole is None:
        if not v12:
            return ""
        whole = False
    every = len(a) == 4
    hay = _ascii_upper(text) if v12 else text
    if v12:
        pat = _ascii_upper(pat)
    out, line, item, i, n = [], 1, 1, 0, len(text)
    while True:
        if hay.startswith(pat, i):
            out.append(("%d,%d,%d\n" % (line, item, i + 1)) if v12 else "%d,%d\n" % (line, item))
            if not every:
                break
        if i >= n:
            break
        if whole:       # on to the next word
            stop = False
            while True:
                c = text[i] if i < n else ""
                if c == "":
                    stop = True
                elif c == "\n":
                    line += 1
                    stop = True
                elif c == " ":
                    stop = True
                elif c == ",":
                    item += 1
                i += 1
                if stop:
                    break
            if i > n or (i == n and stop):
                break
        else:
            c = text[i]
            if c == "\n":
                line += 1
            elif c == ",":
                item += 1
            i += 1
    return "".join(out)


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


def _append_menu(items, text):
    """Adds menu items as the Menu Manager's AppendMenu does with a string:
    ";" and return separate them; "(" disables one; "^x", "!x", "<x" and
    "/x" set its icon, mark, style and key (and are not shown). An item
    with no text ends the menu, as its zero length marks the end of a
    menu record."""
    if items and items[-1] is None:
        return items
    for part in text.replace("\r", ";").replace("\n", ";").split(";"):
        name, enabled, mark, i = "", True, False, 0
        while i < len(part):
            c = part[i]
            if c in "^!</" and i + 1 < len(part):
                mark = mark or c == "!"
                i += 2
                continue
            if c == "(":
                enabled = False
            else:
                name += c
            i += 1
        if name == "":
            items.append(None)
            return items
        items.append({"text": name, "sub": [], "enabled": enabled, "sep": name.startswith("-"),
                      "checked": mark, "title": False})
    return items


def _menu_select(it, items, checked, h, v):
    """PopUpMenuSelect with a menu of _append_menu: the item chosen, or 0."""
    items = [i for i in items if i is not None]
    if 1 <= checked <= len(items):
        items[checked - 1]["checked"] = True
    r = it.host.popup_menu(items, h, v)
    return r[0] if r and 1 <= r[0] <= len(items) else 0


# The fonts a Mac menu of FONT resources lists besides the stack's own (HyperCard's Font menu).
_MENU_FONTS = ("Chicago", "Geneva", "Helvetica", "Monaco", "New York", "Palatino", "Times")


def res_menu_names(rtype, resources):
    """The items AddResMenu puts in a menu for a resource type: the names
    of those resources (resources: (type, id, name, data) tuples), without
    the ones starting with "." or "%", sorted; for fonts, also the fonts
    every Mac has. Also used by the xemu's AddResMenu."""
    names = {}
    for r in resources:
        if r[0] == rtype or (rtype == "FONT" and r[0] in ("FOND", "NFNT")):
            name = r[2]
            if name and name[0] not in ".%" and name.lower() not in names:
                names[name.lower()] = name
    if rtype == "FONT":
        for name in _MENU_FONTS:
            names.setdefault(name.lower(), name)
    return [names[k] for k in sorted(names)]


def _num_arg(a, i):
    return int(ht.to_num(a[i])) if len(a) > i and ht.is_number(a[i]) else 0


def _pstr255(text):
    """A HyperCard string made a Pascal string (its first 255 bytes)."""
    return text.encode("mac_roman", "replace")[:255].decode("mac_roman")


_POPUPMENU_CRC = {0xDD190DCD: "any", 0x2F9B759D: "four", 0x77BBE03A: "four quiet"}


def _pop_up_menu_versions(it, a, code):
    """The PopUpMenu XFCNs of the test stacks (see pop_it_up), or None."""
    import re
    import zlib
    a = [str(x) for x in a]
    if b"Brown University" in code:
        # Brown University's 2.0b: items on lines or between commas, the empty ones left out.
        if not a:
            m = re.search(rb"PopUpMenu 2\.0b(\d?)", code)
            v = m.group(1).decode() if m else ""
            if v == "4":
                return "PopUpMenu 2.0b4 by Andrew Gilmartin, Brown University"
            return "PopUpMenu 2.0b%s, \u00a9 Brown University 1989" % v
        if len(a) > 4:
            return ""
        items = []
        for ln in a[0].replace(",", "\n").split("\n"):
            if ln:
                _append_menu(items, ln)
        n = _menu_select(it, items, _num_arg(a, 1), _num_arg(a, 2), _num_arg(a, 3))
        return str(n) if n else ""
    if b"APPENDALLMENUITEMS" in code:
        # WWW.Where.com's: any length, in pieces of up to 250 characters cut at a comma.
        text, items = a[0] if a else "", []
        if not text:
            return "0"
        while text:
            piece = text
            if len(piece.encode("mac_roman", "replace")) > 250:
                cut = piece[:250].rfind(",")
                if cut <= 0:
                    break
                piece = piece[:cut]
            _append_menu(items, piece.replace(",", ";"))
            text = text[len(piece) + 1:] if len(piece) < len(text) else ""
        return str(_menu_select(it, items, _num_arg(a, 1), _num_arg(a, 2), _num_arg(a, 3)))
    if b"BungDabba" in code:
        return _pop_up_menu_bungdabba(it, a)
    if b"PopupMenu XFNC v32" in code:
        return _pop_up_menu_fernandez(it, a)
    kind = _POPUPMENU_CRC.get(zlib.crc32(code))
    if kind is None:
        return None
    if kind != "any" and len(a) != 4:
        return ""
    items = _append_menu([], _pstr255(a[0] if a else "").replace(",", ";"))
    n = _menu_select(it, items, _num_arg(a, 1), _num_arg(a, 2), _num_arg(a, 3))
    return "" if n == 0 and kind == "four quiet" else str(n)


_BUNGDABBA_HELP = ("popUpMenu( MenuText, MenuLoc [,DefaultItemNum] [,TextFont] [,TextSize] ) \n"
                   "Returns text of the menu item selected.  Errors reported in the HC global \u201cgXCMDError\u201d.")


def _pop_up_menu_bungdabba(it, a):
    """BungDabba's popUpMenu(MenuText, "h,v" [, DefaultItemNum, TextFont,
    TextSize]): the items are the lines and items of the text (the
    itemDelimiter), each up to 255 characters and shown as it is (an empty
    one ends the menu, as on the Mac); answers the item's text and number
    on two lines ("" and 0 when none was chosen). Errors go into the
    global gXCMDError, which is emptied otherwise."""
    def err(msg):
        it.globals["gxcmderror"] = msg

    if len(a) == 1 and a[0][:1] in ("!", "?"):
        err("")
        return "\u00a9 BungDabba Productions 1991,  ver 1.0." if a[0][:1] == "!" else _BUNGDABBA_HELP
    if not 2 <= len(a) <= 5:
        err("")
        return _BUNGDABBA_HELP
    if len(a[0].encode("mac_roman", "replace")) < 2:
        err("no text specified for the menu.")
        return ""
    pt = a[1].split(",")
    if len(pt) != 2 or not all(ht.is_number(x.strip()) for x in pt):
        err("invalid location specified for the menu.")
        return ""
    h, v = (int(ht.to_num(x.strip())) for x in pt)
    delim = str(it.props.get("itemdelimiter", ","))[:1] or "\u00e9"
    items, text = [], a[0].replace("\r", "\n")
    while text:
        k = min([i for i in (text.find("\n"), text.find(delim)) if i >= 0] or [len(text)])
        items.append(text[:k][:255])
        text = text[k + 1:]
    menu = []
    for k, name in enumerate(items, 1):     # AppendMenu(" ") then SetMenuItemText, item by item
        menu.append(" ")
        if k <= len(menu):
            menu[k - 1] = name
            if name == "":                  # no text: the menu record ends there
                del menu[k - 1:]
    shown = [{"text": name, "sub": [], "enabled": True, "sep": name.startswith("-"),
              "checked": False, "title": False} for name in menu]
    default = _num_arg(a, 2)
    if 1 <= default <= len(shown):
        shown[default - 1]["checked"] = True
    r = it.host.popup_menu(shown, h, v)
    err("")
    if not r or not 1 <= r[0] <= len(shown):
        return "\n0"
    return "%s\n%d" % (shown[r[0] - 1]["text"], r[0])


_FERNANDEZ_HELP = "PopupMenu XFNC v32,  Help follows -->\n\n© Bill Fernandez 1989, all rights reserved.\nNo warranty expressed or implied.\n_______________________________________________\nSYNTAX:\n\nget PopupMenu(menu items, current item, menu left, menu top)\n\n_______________________________________________\nDEFINITIONS:\n\nMENU ITEMS =  a list of the menu item names, in the form--\nFirstItem[*] SecondItem[*]  ...LastItem[*]\n\n* =  an optional list of item-modifiers, in the form--\nFirstModifier, SecondModifier, ...LastModifier\n\nCURRENT ITEM =  the number of the menu-item that will be positioned just above(!) the TOP parameter described below.\n\nMENU LEFT =  the number of pixels, from the left edge of the card, at which the left edge of the menu will be positioned.\n\nMENU TOP =  the number of pixels, from the top edge of the card, at which the top edge of the menu will be positioned.  Note that the card begins below(!) the titlebar.\n\n_______________________________________________\nITEM-MODIFIERS:\n\nDISABLED\n\nBOLD\n\nITALIC\n\nOUTLINE\n\nSHADOW\n\nUNDERLINE\n\nCONDENSE\n\nEXTEND\n\nAPPLE_MARK\n\nDIAMOND_MARK\n\nCHECKMARK\n\nMARK followed by a space and any character.\n\nCOMMAND followed by a space and any character.\n\nICON followed by a space and an ICON ID number from 257 to 511.\n\nSICN or SMALL_ICON followed by a space and a SICN ID number from 257 to 511.\n\nRICN (short for \"reduced\" icon) or SHRUNKEN_ICON followed by a space and an ICON ID number from 257 to 511.\n\n_______________________________________________\nNOTES:\n\n(1) The item list parameter may be a field or variable with each item on its own line, or it may be a quoted string typed directly into the script.\n\n(2) Item-modifiers may be typed in completely, or shortened to their first four letters.  They may be in upper or lowercase.\n\n(3) Try specifying only one, two, or three parameters.  The XFNC tries to default any unspecified parameters to safe and useful values.\n\n(4) An item with a small or shrunken icon cannot have a command character.\n\n(5) Modifiers may be specified in any order.\n\n(6) When there is a conflict, modifiers that appear later in a modifier-list will take precedence over modifiers that appear earlier in the list.\n"

_FERNANDEZ_MARKS = {"APPL": True, "DIAM": True, "CHEC": True, "MARK": True}


def _pop_up_menu_fernandez(it, a):
    """Bill Fernandez's PopupMenu(items, current item, left, top), v32:
    an item per line, with modifiers in brackets after its name
    ("Name[disabled, bold, checkmark]", the first four letters are enough;
    a later one wins). Answers "number,name", or 0. Without parameters,
    its help."""
    if not a:
        return _FERNANDEZ_HELP
    t = a[0].replace("\r", "\n")
    items, i = [], 0
    while i < len(t):
        j = i
        while j < len(t) and t[j] not in "[\n":
            j += 1
        name, enabled, marked = t[i:j], True, False
        i = j
        if i < len(t) and t[i] == "[":
            j = i + 1
            while j < len(t) and t[j] not in "]\n":
                j += 1
            for mod in t[i + 1:j].split(","):
                word = mod.strip().upper()[:4]
                if word == "DISA":
                    enabled = False
                elif word in _FERNANDEZ_MARKS:
                    marked = True
            i = j + 1 if j < len(t) and t[j] == "]" else j
        if i < len(t) and t[i] == "\n":
            i += 1
        items.append({"text": name or " ", "sub": [], "enabled": enabled, "sep": name.startswith("-"),
                      "checked": marked, "title": False})
    r = it.host.popup_menu(items, _num_arg(a, 2), _num_arg(a, 3))
    if not r or not 1 <= r[0] <= len(items):
        return "0"
    return "%d,%s" % (r[0], items[r[0] - 1]["text"])


@xfcn("PopItUp", "PopUpMenu", "PopUp")
def pop_it_up(it, a):
    """PopUpMenu(items, checkedItem, h, v): the versions of the test stacks
    (Brown University's 2.0b, WWW.Where.com's, Minkowski's, HyperScience's
    and Quel Giorno's) put the items in a Mac menu (with its ";", "(" and
    other marks) and answer the number of the item chosen (0, or empty in
    some, when none was). Otherwise:

    PopItUp(items, checkedItem, h, v[, flags]) shows a pop-up menu at
    h,v (card coordinates) and returns "line,subitem", or 0 if cancelled.
    Lines starting with "<X" carry a style and are shown as a title; "("
    disables an item; "-" is a separator. Items of a line separated by
    commas become a submenu unless flags contain "nosubmenus"."""
    for name in ("PopUpMenu", "PopItUp", "PopUp"):
        code = _ext_code(it, name)
        if code:
            r = _pop_up_menu_versions(it, a, code)
            if r is not None:
                return r
            break
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


# -- Mac menus, as the Menu Manager keeps them --------------------------------------------------

def parse_menu_meta(text):
    """AppendMenu's items for a string (bytes, Mac Roman): ";" or return
    between items, "^" icon, "!" mark, "<" style, "/" key, "(" disabled.
    [([name, icon, cmd, mark, style], enabled)]. Also the xemu's."""
    out = []
    for part in text.replace(b"\r", b";").split(b";"):
        name, icon, cmd, mark, style, enabled = bytearray(), 0, 0, 0, 0, True
        i = 0
        while i < len(part):
            c = part[i]
            if c in b"^!</" and i + 1 < len(part):
                v = part[i + 1]
                if c == ord("^"):
                    icon = v - 48
                elif c == ord("!"):
                    mark = v
                elif c == ord("/"):
                    cmd = v
                else:
                    style |= {ord("B"): 1, ord("I"): 2, ord("U"): 4, ord("O"): 8, ord("S"): 16}.get(v, 0)
                i += 2
                continue
            if c == ord("("):
                enabled = False
                i += 1
                continue
            name.append(c)
            i += 1
        out.append(([bytes(name), icon & 0xFF, cmd, mark, style], enabled))
    return out


class _MacMenu:
    """A menu record: its ID, enable flags and items ([name, icon, cmd,
    mark, style], the name in Mac Roman). An item with an empty name ends
    the menu, as its zero length byte does in the record."""

    def __init__(self, mid, items=(), flags=0xFFFFFFFF):
        self.id, self.flags, self.items = mid, flags, [list(e) for e in items]
        self._trim()

    @classmethod
    def from_resource(cls, mid, data):
        """A MENU resource (ID, width, height, proc, flags, title, items)."""
        import struct
        if len(data) < 15:
            return cls(mid)
        flags = struct.unpack(">I", data[10:14])[0]
        pos = 15 + data[14]
        items = []
        while pos < len(data) and data[pos]:
            n = data[pos]
            items.append([data[pos + 1:pos + 1 + n]] + list(data[pos + 1 + n:pos + 5 + n].ljust(4, b"\0")))
            pos += 5 + n
        return cls(mid, items, flags)

    def _trim(self):
        for k, e in enumerate(self.items):
            if not e[0]:
                del self.items[k:]
                return

    def count(self):
        return len(self.items)

    def item(self, n):
        return self.items[n - 1] if 1 <= n <= len(self.items) else None

    def insert(self, text, after=1 << 15):
        """AppendMenu (and InsMenuItem after an item) with metacharacters."""
        at = max(0, min(after, len(self.items)))
        for k, (e, enabled) in enumerate(parse_menu_meta(text)):
            n = at + k + 1
            if n < 32:      # the enable bits after it move up
                low = self.flags & ((1 << n) - 1)
                high = (self.flags >> n) << (n + 1)
                self.flags = (low | high | ((1 << n) if enabled else 0)) & 0xFFFFFFFF
            self.items.insert(at + k, e)
        self._trim()

    def insert_names(self, names, after=1 << 15):
        """AddResMenu and InsertResMenu: each name an item as it is."""
        for k, name in enumerate(names):
            at = min(after, self.count()) + k if after != 1 << 15 else self.count()
            self.insert(b"x", at)
            self.items[at][0] = name.encode("mac_roman", "replace")[:255]

    def delete(self, n):
        if 1 <= n <= len(self.items):
            del self.items[n - 1]
            if n < 32:
                f = self.flags
                self.flags = (f & ((1 << n) - 1)) | ((f >> (n + 1)) << n)

    def set(self, n, index, value):
        """Sets the name (0), icon, key, mark or style of item n."""
        e = self.item(n)
        if e:
            e[index] = value if index == 0 else value & 0xFF
            self._trim()

    def enabled(self, n):
        return bool(self.flags & 1) and (n >= 32 or bool(self.flags & (1 << n)))

    def host_items(self, menus, depth=0):
        """The items as host.popup_menu takes them, and for each the
        (menu, item number) of each row of its submenu; a submenu's own
        submenus are flattened into it ("child › grandchild"), as the
        xemu's PopUpMenuSelect shows them. menus: ID -> _MacMenu."""
        out, rows = [], []
        for k, (name, icon, cmd, mark, style) in enumerate(self.items, 1):
            text = name.decode("mac_roman", "replace").replace("\r", "\n")
            sub, srows = [], []
            sub_m = menus.get(mark) or menus.get(mark - 256) if cmd == 0x1B and depth < 4 else None
            if sub_m:
                kids, kid_rows = sub_m.host_items(menus, depth + 1)
                for j, kid in enumerate(kids, 1):
                    if kid["sub"]:
                        sub += ["%s › %s" % (kid["text"], g) for g in kid["sub"]]
                        srows += kid_rows[j - 1]
                    else:
                        sub.append(kid["text"])
                        srows.append((sub_m, j))
            out.append({"text": text, "sub": sub, "enabled": self.enabled(k), "sep": text.startswith("-"),
                        "checked": mark != 0 and cmd != 0x1B, "title": False, "style": style})
            rows.append(srows)
        return out, rows

    def select(self, it, menus, h, v):
        """PopUpMenuSelect: (menu, item) chosen (item 0: none), and whether
        it can be chosen (a disabled one, or one with a submenu, answers 0
        but is in MenuDisable)."""
        items, rows = self.host_items(menus)
        r = it.host.popup_menu(items, h, v)
        if not r or not 1 <= r[0] <= len(items):
            return self, 0, False
        line, sub = r
        if sub and 1 <= sub <= len(rows[line - 1]):
            m, n = rows[line - 1][sub - 1]
            return m, n, m.enabled(n)
        return self, line, self.enabled(line) and not rows[line - 1]


def _resource_data(it, rtype, rid):
    try:
        chain = it.host.resource_chain()
    except Exception:  # noqa: BLE001
        return None
    for res in chain:
        for r in res:
            if r[0] == rtype and r[1] == rid:
                return r[3]
    return None


def _all_resources(it):
    try:
        return [r for res in it.host.resource_chain() for r in res]
    except Exception:  # noqa: BLE001
        return []


# -- HPopUpMenu: Jon Pugh's ---------------------------------------------------------------------------

class _PughError(Exception):
    pass


class _Pugh:
    """Jon Pugh's HPopupMenu (3.1.2, 3.2.2, 3.3): its menus are built as
    the XFCN builds them, with the same Menu Manager calls."""
    STYLES = b"BIOSU"

    def __init__(self, it, version):
        self.it, self.version = it, version
        self.menus = []         # by number: 0 is the main menu, ID 128; None once disposed
        self.inserted = {}      # ID -> menu (InsertMenu)
        self.res = None

    def res_names(self, rtype):
        if self.res is None:
            self.res = _all_resources(self.it)
        return res_menu_names(rtype, self.res)

    def new_menu(self, mid):
        m = _MacMenu(mid)
        self.menus.append(m)
        return m

    def insert(self, m):
        self.inserted[m.id] = m

    @staticmethod
    def small_icon(t):
        """SMALLICON: "name†n" is item name with small icon n (a "/x" key
        at the end is kept): (icon, name)."""
        k = t.find(b"\xa0")
        if k < 0:
            return 0, t
        rest, t = t[k + 1:], t[:k]
        if len(rest) >= 2 and rest[-2] == 0x2F:
            t += rest[-2:]
        return (rest[0] - 48 if rest else 0), t

    # DEPENDMENU: one item, its metacharacters kept only where they mean something
    def depend(self, t, m):
        if len(t) == 5 and t[0] == 0xA8:      # "®FONT": the resources of a type
            m.insert_names(self.res_names(t[1:5].decode("mac_roman")))
            return
        icon, t = self.small_icon(t)
        if icon > 0:
            self.depend(t, m)
            last = self.menus[-1].count()   # (the XFCN counts the newest menu's items)
            m.set(last, 2, 0x1E)
            m.set(last, 1, icon)
            return
        self.append(t, m)

    def append(self, t, m, semicolon=True):
        """The item as AppendMenu sees it, then its name without the
        metacharacters that mean something."""
        n = len(t)
        s = bytearray(t)
        for i in range(n):
            c = s[i]
            nxt = s[i + 1] if i + 1 < n else None
            if c == 0x2F and i != n - 2:
                s[i] = 0x5C                     # "/" but the key's: "\"
            elif c in (0x5E, 0xA0):
                if not (nxt is not None and 0x30 < nxt < 0x7F):
                    s[i] = 0x76                 # "^" but an icon's: "v"
            elif c == 0x3C and not (nxt is not None and nxt in self.STYLES):
                s[i] = 0x3E                     # "<" but a style's: ">"
            elif c == 0x21 and i != n - 2:
                s[i] = 0x31                     # "!" but the mark's: "1"
            elif c == 0x3B and semicolon:
                s[i] = 0x3A                     # ";" -> ":"
            elif c == 0x28 and i != 0:
                s[i] = 0x29                     # "(" but the first: ")"
        m.insert(bytes(s))
        r, i = bytearray(), 0
        while i < n:
            c = t[i]
            nxt = t[i + 1] if i + 1 < n else None
            if (c == 0x2F and i == n - 2) or \
                    (c in (0x5E, 0xA0) and nxt is not None and 0x30 < nxt < 0x7F) or \
                    (c == 0x3C and nxt is not None and nxt in self.STYLES) or \
                    (c == 0x21 and (i == n - 2 or i == 0)):
                i += 2
            elif c == 0x28 and i == 0:
                i = 1
            else:
                r.append(c)
                i += 1
        m.set(m.count(), 0, bytes(r))

    # 3.1.2 and 3.2.2: one line an item, and the items after its first delimiter its submenu
    def add_item_v3(self, t, m):
        if len(t) == 5 and t[0] == 0xA8:
            m.insert_names(self.res_names(t[1:5].decode("mac_roman")))
            return
        icon, t = self.small_icon(t)
        if icon > 0:
            m.insert(t)
            m.set(m.count(), 2, 0x1E)
            m.set(m.count(), 1, icon)
        else:
            self.append(t, m, semicolon=False)

    def line_menu(self, text, d, always_subs=False):
        t = bytearray(text).replace(b"\r", b";")
        if b";" not in t:
            t = t.replace(d, b";")
        if not t or t[-1] != 0x3B:
            t.append(0x3B)
        dc, dset = d[0], {d[0], 0x3B}
        start = 1 if t[0] in dset else 0
        main = self.new_menu(128)
        first, has_sub, seg = True, False, start
        for i in range(start, len(t)):
            c = t[i]
            if first and c in dset:
                item = t[seg:i][:255]
                if c == dc:
                    first = False
                    has_sub = has_sub or always_subs    # 3.1.2
                    if not (i + 1 < len(t) and t[i + 1] in dset):
                        has_sub = True
                        item = (item + b"/\x1b")[:255]
                self.add_item_v3(bytes(item), main)
            if c == 0x3B:
                seg, first = i + 1, True
        self.insert(main)
        if has_sub:
            first, have, seg, k, sub = True, False, 0, 0, None
            for i in range(start, len(t)):
                c = t[i]
                if c not in dset:
                    continue
                if not first:
                    if i != seg:
                        self.add_item_v3(bytes(t[seg:i][:255]), sub)
                        have = True
                    if c == 0x3B:
                        if not have:
                            main.set(k, 3, 0)
                            self.inserted.pop(sub.id, None)
                            self.menus.pop()
                        else:
                            self.insert(sub)
                else:
                    k += 1
                    if c == dc:
                        if len(self.menus) > 300:
                            raise _PughError("Error: Too many menu items")
                        sub = self.new_menu(len(self.menus) + 128)
                        main.set(k, 3, sub.id)
                        have = False
                seg, first = i + 1, c == 0x3B
        return main

    # STRINGMENU and MAKEMENUS: items between the level characters
    def string_menu(self, text, levels, dset, own_delims):
        t = bytearray(text)
        if not own_delims:
            t = t.replace(b"\r", b";")
            if b";" not in t and len(levels) > 1:
                t = t.replace(levels[1:2], b";")
        if not t or t[-1] != 0x3B:
            t.append(0x3B)
        self.t, self.dset, self.levels = bytes(t), dset, levels
        self.pos = 1 if t[0] in dset else 0
        self.level = 0
        main = self.new_menu(128)
        self.make_menus(0)
        self.insert(main)
        return main

    def level_of(self, c):
        k = self.levels.find(bytes([c]))
        return k if k >= 0 else 0

    def make_menus(self, cur):
        t, end, k = self.t, len(self.t) - 1, 0
        while True:
            if self.pos >= end:
                return
            start = self.pos
            while self.pos < end and t[self.pos] not in self.dset:
                self.pos += 1
            item = t[start:self.pos][:255]
            if item:
                k += 1
            lvl = self.level_of(t[self.pos])
            m = self.menus[cur]
            if lvl == self.level:
                if item:
                    self.depend(item, m)
                self.pos += 1
                continue
            if lvl > self.level + 1:
                raise _PughError(self.version.get("skipped", "Error: Skipped a submenu level."))
            if lvl < self.level:
                if item:
                    self.depend(item, m)
                self.insert(m)
                self.level = lvl
                self.pos += 1
                return
            # one level down: the item has a submenu
            parent = item + b"/\x1b"
            self.depend(parent, m)
            if len(self.menus) > 300:
                raise _PughError("Error: Too many submenus")
            num = len(self.menus)
            mid = num + 128
            m.set(k, 3, mid)
            sub = self.new_menu(mid)
            self.pos += 1
            self.level += 1
            self.make_menus(num)
            if num == len(self.menus) - 1 and sub.count() == 0:
                m.set(k, 3, 0)
                self.inserted.pop(mid, None)
                self.menus.pop()
                m.delete(k)
                self.depend(parent[:-2], m)
            else:
                self.insert(sub)
            if self.level != lvl - 1:
                return

    # RESOURCEMENU and SCANMENUS: a MENU resource and its submenus
    def resource_menu(self, rid):
        data = _resource_data(self.it, "MENU", rid)
        if data is None:
            raise _PughError("Error: Invalid Menu Number")
        main = _MacMenu.from_resource(128, data)
        self.menus.append(main)
        self.insert(main)
        self.scan(main)
        return main

    def scan(self, m):
        i = 1
        while i <= m.count():
            name, icon, cmd, mark, style = m.item(i)
            if len(name) == 5 and name[0] == 0xA8:
                m.delete(i)
                m.insert_names(self.res_names(name[1:5].decode("mac_roman")), i - 1)
            elif cmd == 0x1B:
                data = _resource_data(self.it, "MENU", mark)
                if data is None:
                    raise _PughError("Error: Submenu resource not found")
                mid = len(self.menus) + 128
                m.set(i, 3, mid)
                sub = _MacMenu.from_resource(mid, data)
                self.menus.append(sub)
                self.scan(sub)
                self.insert(sub)
            i += 1


def _param_num(s):
    """PARAMTONUM: HyperCard's StrToNum of the parameter (0 if not a number)."""
    s = str(s).strip()
    if not ht.is_number(s):
        return 0
    try:
        return int(ht.to_num(s))
    except (ht.HTError, ValueError, OverflowError):
        return 0


def _is_numeric(raw):
    """NUMERIC: not empty, and only digits, "+", "-" and spaces."""
    return bool(raw) and all(c in b"0123456789+- " for c in raw)


def _h_pop_up_menu_pugh(it, a, code):
    """HPopupMenu({items | MENU id}, item, top, left [, font, size [, delimiters]])
    by Jon Pugh. Items are separated by return or ";" (or the
    itemDelimiter on one line); an item followed by the itemDelimiter
    gets a submenu of the items that follow, up to the next return or
    ";". With delimiters, its characters are the levels: the first ends
    an item of the same level, the second opens a submenu, and so on.
    A resource ID builds the menu from MENU resources (and their
    submenus); "®TYPE" lists the resources of a type. The answer is
    "text,subtext,...,item,subitem,..." (the second delimiter between
    them, "0" as the subitem of a main item), or empty when nothing was
    chosen (or the mouse was already up)."""
    def pstr(marker):
        k = code.find(marker)
        return code[k:k + code[k - 1]].decode("mac_roman") if k > 0 else ""

    about, usage = pstr(b"HPopupMenu 3"), pstr(b"Usage: HPopupMenu")
    has_delims = b"[, delimiter]" in code
    a = [str(x) for x in a]
    if len(a) == 1:
        return about if a[0] == "!" else usage
    if not 4 <= len(a) <= (7 if has_delims else 6):
        return usage
    if not it.host.mouse_is_down() or a[0] == "":
        return ""

    def mac(s):
        return s.encode("mac_roman", "replace").replace(b"\n", b"\r")

    levels_ok = b"Skipped a submenu level" in code     # 3.3: levels of delimiters
    own = has_delims and len(a) >= 7
    idel = mac(str(it.props.get("itemdelimiter", ",")))
    if not levels_ok:
        # 3.1.2, 3.2.2: one delimiter, the given one if it is one character
        d = mac(a[6]) if own and len(mac(a[6])) == 1 else idel if len(idel) == 1 else b","
    elif own:
        levels = mac(a[6]) or b","
        dset = set(levels)
        d = levels[1:2] or b"\0"
    else:
        levels = b";" + (idel or b",")
        dset = set(levels) | {0x3B}
        d = levels[1:2] or b"\0"
    item = _param_num(a[1])
    v, h = _param_num(a[2]), _param_num(a[3])
    p = _Pugh(it, {})
    raw = mac(a[0])
    pas = raw if len(raw) < 256 else b""
    try:
        if _is_numeric(pas):
            main = p.resource_menu(_param_num(a[0]))
        elif len(pas) == 5 and pas[0] == 0xA8:
            main = p.new_menu(128)
            main.insert_names(p.res_names(pas[1:5].decode("mac_roman")))
            p.insert(main)
        elif not levels_ok:
            main = p.line_menu(raw, d, not has_delims)
        else:
            main = p.string_menu(raw, levels, dset, own)
    except _PughError as e:
        return str(e)
    if item > 0:
        main.set(item, 3, 0x12)
    if not it.host.mouse_is_down():
        return ""
    m, n, enabled = main.select(it, p.inserted, h, v)
    if n == 0 or not (n > 31 or m.flags & (1 << n)) and (m is not main or has_delims):
        return ""       # (3.1.2 answers a disabled item of the main menu too)

    def name(menu, k):
        return menu.item(k)[0].replace(b"\0", b"") if menu.item(k) else b""

    def cat(*parts):
        return b"".join(parts)[:255]

    if m is main:
        out = cat(name(m, n), d, d, str(n).encode(), d, b"0")
    else:
        text, nums = name(m, n), str(n).encode()
        while m is not main:
            parent = None
            for pm in p.menus:
                for k, e in enumerate(pm.items, 1):
                    if e[2] == 0x1B and e[3] == m.id:
                        parent = (pm, k)
                        break
                if parent:
                    break
            if parent is None:
                break
            m, n = parent
            if len(text) + len(name(m, n)) + 1 < 256:
                text = cat(name(m, n), d, text)
            nums = cat(str(n).encode(), d, nums)
        out = cat(text, d, nums) if len(text) + len(nums) + 1 < 256 else nums
    return out.split(b"\0")[0].decode("mac_roman").replace("\r", "\n")


def _string_to_num(raw):
    """The Mac's StringToNum: a sign, then every character taken as a digit."""
    neg = raw[:1] == b"-"
    if raw[:1] in (b"-", b"+"):
        raw = raw[1:]
    n = 0
    for c in raw:
        n = (n * 10 + c - 48) & 0xFFFFFFFF
    n = n - (1 << 32) if n >= 1 << 31 else n
    return -n if neg else n


_HPOPUP_EARLY = {0xC7091989: (False, "none", False), 0xD1FFAEFC: (True, "skip", True),
                 0x6D1632BB: (False, "nonempty", False), 0x84FF1619: (False, "skip", True)}


def _h_pop_up_menu_choices(it, a, code, early=False):
    """The older HPopupMenu(choices, item, top, left [, font, size]): lines
    (or the items of one line) are the menu; the items after the first
    comma of a line are its submenu. The items go to AppendMenu as they
    are (its metacharacters work). Answers "text,subtext,item,subitem"
    (subitem 0 for a main item), or empty. The early ones ("Insufficient
    Parameters") split only at ";" (return separates items inside
    AppendMenu) and have no small icons."""
    k = code.find(b"Usage: HPopupMenu" if not early else b"Insufficient Parameters")
    usage = code[k:k + code[k - 1]].decode("mac_roman") if k > 0 else ""
    import zlib
    # the early ones: (return -> ";", "skip" a leading delimiter or "nonempty" items only, empty text answers empty)
    cr, lead, empty = _HPOPUP_EARLY.get(zlib.crc32(code), (False, "none", False)) if early else \
        (True, "skip", True)
    a = [str(x) for x in a]
    if len(a) < 4 or len(a) > 6 and b"[,font, size]" in code:
        return usage
    if not it.host.mouse_is_down() or a[0] == "" and empty:
        return ""
    t = bytearray(a[0].encode("mac_roman", "replace").replace(b"\n", b"\r"))
    if cr:
        t = t.replace(b"\r", b";")
    if b";" not in t:
        t = t.replace(b",", b";")
    if not t or t[-1] != 0x3B:
        t.append(0x3B)
    dset = b",;"
    start = 1 if lead == "skip" and t[0] in dset else 0
    item = _param_num(a[1])
    v, h = _param_num(a[2]), _param_num(a[3])

    def add(raw, m):
        k = raw.find(b"\xa0") if not early else -1    # "name†n": small icon n
        icon = 0
        if k >= 0:
            rest, raw = raw[k + 1:], raw[:k]
            icon = _string_to_num(rest) & 0xFFFF
            icon = icon - 0x10000 if icon >= 0x8000 else icon
            if len(rest) >= 2 and rest[-2] == 0x2F:
                raw += rest[-2:]
        m.insert(bytes(raw))
        if icon > 0:
            m.set(m.count(), 2, 0x1E)
            m.set(m.count(), 1, icon)

    main = _MacMenu(128)
    menus = {0: main}
    first, seg = True, start
    for i in range(start, len(t)):
        c = t[i]
        if first and c in dset and (lead != "nonempty" or i != seg):
            name = t[seg:i][:255]
            if c == 0x2C:
                first = False
                if not (i + 1 < len(t) and t[i + 1] in dset):
                    name = (name + b"/\x1b")[:255]
            add(name, main)
        if c == 0x3B:
            seg, first = i + 1, True
    if item > 0:
        main.set(item, 3, 0x12)
    inserted = {128: main}
    line, first, have, seg = 0, True, False, 0
    for i in range(start, len(t)):
        c = t[i]
        if c not in dset:
            continue
        if first:
            line += 1
            menus[line] = None
            if c == 0x2C:
                main.set(line, 3, line + 128)
                menus[line] = _MacMenu(line + 128)
                have = False
        else:
            if i != seg and menus.get(line):
                add(t[seg:i][:255], menus[line])
                have = True
            if c == 0x3B and menus.get(line):
                if not have:
                    main.set(line, 3, 0)
                    menus[line] = None
                else:
                    inserted[line + 128] = menus[line]
        seg, first = i + 1, c == 0x3B
    if not it.host.mouse_is_down():
        return ""
    m, n, enabled = main.select(it, inserted, h, v)
    if n == 0:
        return ""
    if m is main:
        top, sub, t1, t2 = n, 0, main.item(n)[0], b""
    else:
        top, sub = m.id - 128, n
        t1 = main.item(top)[0] if main.item(top) else b""
        t2 = m.item(n)[0]
    if not ((n > 31 and not early) or m.flags & (1 << (n & 31))) or not t1:
        return ""
    out = b"".join([t1, b",", t2, b",", str(top).encode(), b",", str(sub).encode()])[:255]
    return out.split(b"\0")[0].decode("mac_roman").replace("\r", "\n")


class _FullHPopError(Exception):
    pass


def _fhp_num(raw):
    """HyperCard's StrToNum/StrToLong callbacks: 0 for what isn't a number."""
    t = raw.decode("mac_roman", "replace").strip()
    n = int(ht.to_num(t)) if t and ht.is_number(t) else 0
    return n


def _word(n):
    n &= 0xFFFF
    return n - 0x10000 if n >= 0x8000 else n


def _fhp_is_int(raw):
    """Rinaldi's integer test: all but the last char among "+-0..9", the
    last a digit."""
    if not raw:
        return False
    i = 0
    while i < len(raw) - 1 and raw[i] in b"+-0123456789":
        i += 1
    return raw[i] in b"0123456789"


def _fhp_read(buf, pos, sep):
    """Reads up to sep or the end (a longer piece is cut at 254 chars and
    "…"), past the sep."""
    start = pos
    while buf[pos] and buf[pos] != sep:
        pos += 1
    tok = bytes(buf[start:pos])
    if len(tok) > 255:
        tok = tok[:254] + b"\xc9"
    if buf[pos]:
        pos += 1
    return tok, pos


def _fhp_token(buf, pos, sep):
    """A piece up to sep, where a backslash before sep makes it part of the piece."""
    acc = b""
    while True:
        tok, pos = _fhp_read(buf, pos, sep)
        if not tok or tok[-1] != 0x5C:
            break
        acc = (acc + tok[:-1] + bytes([sep]))[:255]
    return (acc + tok)[:255], pos


def _get_fnum(raw):
    from .render import MAC_FONT_IDS
    name = raw.decode("mac_roman", "replace").lower()
    num = 0
    for fid, n in MAC_FONT_IDS.items():
        if n.lower() == name:
            num = fid
    return num


def _same_text(a, b):
    """IUMagIDString: equal but for case."""
    return a.decode("mac_roman", "replace").upper() == b.decode("mac_roman", "replace").upper()


class _FullHPop:
    """Frédéric Rinaldi's FullHPop(menu [, loc] [, selected item] [, font[,
    size]] [, checkMark] [, sub owner select] [, "fastest"]): the
    parameters after the menu are told apart by what they look like. Lines
    (or the items of ";" or of "," when there is no return or ";") are the
    menu, and a line's items after a comma its submenu; a backslash makes
    the metacharacter after it plain. Items are capitalized, "^n" and "¨n" take real
    ICON and SICN ids. A four-char menu is the names of the resources of
    that type. Answers "item,subitem,item number,subitem number" and a
    line with their widths in the menu's font. 1.3 has no backslash before
    a separator, no "-" line and capitalizes after ";" too; 1.3 and 1.5
    escape "-" as well, and answer a comma as it is (1.3) or as "’" (1.5)."""

    def __init__(self, it, a, code):
        self.it, self.a, self.code = it, [str(x) for x in a], code
        k = code.find(b"FullHPop 1.")
        self.ver = code[k + 11] - 0x30 if k >= 0 else 7
        self.META = {3: b"(!</^\xac-", 5: b"(!</^\xac,-"}.get(self.ver, b"(!</^\xac,")
        self.base = 0xFF - len(self.META)
        self.fastest = False
        self.sicn = False
        self.check_ok = True
        self.next_id = 128
        self.menus = {}

    def pstr_at(self, marker):
        k = self.code.find(marker)
        return self.code[k:k + self.code[k - 1]].decode("mac_roman") if k > 0 else ""

    def eval(self, expr):
        try:
            v = self.it.eval_toks(ht.tokenize(expr))
        except ht.HTError:
            return b""
        return str(v).replace("\n", "\r").encode("mac_roman", "replace").split(b"\0")[0][:255]

    def width(self, raw):
        from .render import MAC_FONT_IDS
        font, size = MAC_FONT_IDS.get(self.font0, "Geneva"), self.size0 or 12
        text = raw.decode("mac_roman", "replace")
        try:
            return int(self.it.host.text_width(text, font, size))
        except Exception:  # noqa: BLE001
            return int(len(text) * size * 0.55)

    # -- items ---------------------------------------------------------------------------------------
    def icons(self, text):
        t = bytearray(text)
        self.sicn = False
        p = t.find(b"^")
        if p < 0:
            p = t.find(b"\xac")
            self.sicn = p >= 0
        if p < 0:
            return bytes(t)
        q = p + 1
        while q < len(t) and q < p + 4 and t[q] in b"0123456789":
            q += 1
        n = _word(_fhp_num(bytes(t[p + 1:q])))
        del t[p + 1:q]
        if n < 257 or n > 511:
            raise _FullHPopError("Bad ICON or SICN ID (257 ≤ x ≤ 511)")
        t[p + 1:p + 1] = bytes([(n - 0xD0) & 0xFF])
        if self.sicn:
            t[p] = 0x5E
        return bytes(t[:255])

    def cap_point(self, t, i):
        d7 = t[i] not in (b"!(-/<^\xac" if self.ver == 3 else b"!(,-/<^\xac")
        if i == 0:
            return d7
        d5 = t[i - 1] in b"!/<^\xac"
        if i == 1:
            return d7 and not d5
        return d7 and (not d5 or (t[i - 1] in b"/<" and t[i - 2] in b"^\xac"))

    def capitalize(self, text):
        t = bytearray(self.icons(text))
        done = False
        for i in range(len(t)):
            if not done and self.cap_point(t, i):
                done = True
                if 0x61 <= t[i] <= 0x7A:
                    t[i] -= 32
            if 0x61 <= t[i] <= 0x7A and i > 0 and t[i - 1] in b"/<" and (i == 1 or t[i - 2] != 0x5E):
                t[i] -= 32
        return bytes(t) if done else (bytes(t) + b" ")[:255]

    def add(self, m, n, text):
        esc = b"\\" in text
        t = bytearray(text)
        while esc:
            p = t.find(b"\\")
            if not (0 <= p < len(t) - 1 and t[p + 1] in self.META):
                break
            t[p:p + 2] = bytes([self.base + self.META.index(t[p + 1]) + 1])
        m.insert(bytes(t) if self.fastest else self.capitalize(bytes(t)))
        if esc and m.item(n):
            m.set(n, 0, bytes(self.unescape(c) for c in m.item(n)[0]))

    def unescape(self, c):
        if c <= self.base:
            return c
        return 0xD0 if c == 0xFF and self.META.endswith(b"-") else self.META[c - self.base - 1]

    def token(self, buf, pos, sep):
        return _fhp_read(buf, pos, sep) if self.ver == 3 else _fhp_token(buf, pos, sep)

    def text(self, m, n):
        e = m.item(n)
        return e[0] if e else b""

    def submenu(self, main, n, buf, pos):
        if self.next_id + 1 > 255:
            raise _FullHPopError("Cannot exceed 127 submenus")
        self.next_id += 1
        sub = self.menus[self.next_id] = _MacMenu(self.next_id)
        k = 0
        while True:
            tok, pos = self.token(buf, pos, 0x2C)
            if not tok:
                break
            k += 1
            if self.ver >= 6 and tok == b"-":
                self.add(sub, k, b"(-")
                continue
            if self.ver >= 6 and tok[:1] == b"-":
                tok = b"\xd0" + tok[1:]
            self.add(sub, k, tok)
            if self.sel == 0x7FFF and _same_text(tok if self.ver == 3 else self.text(sub, k), self.target_text):
                self.check_ok = False
                self.sel = n
                sub.set(k, 3, self.mark)
            if not self.fastest and self.sicn:
                sub.set(sub.count(), 2, 0x1E)
        main.set(n, 2, 0x1B)
        main.set(n, 3, sub.id)

    def build(self, main, t):
        sep = 0x0D if b"\r" in t else 0x3B if b";" in t else 0x2C
        buf = bytearray(t + b"\0")
        pos, n = 0, 0
        while True:
            n += 1
            start = pos
            line, pos = self.token(buf, pos, sep)
            if not line:
                break
            if buf[pos]:
                buf[pos - 1] = 0
            item, start = self.token(buf, start, 0x2C)
            if self.ver >= 5 and item == b"-":
                main.insert(b"(-")
                continue
            if self.ver >= 6 and item[:1] == b"-":
                item = b"\xd0" + item[1:]
            self.add(main, n, item)
            if self.sel == 0x7FFF and _same_text(item if self.ver == 3 else self.text(main, n), self.target_text):
                self.sel = n
            if self.ver == 3 and b"," in line or self.ver > 3 and any(
                    c == 0x2C and (k == 0 or line[k - 1] != 0x5C) for k, c in enumerate(line)):
                self.submenu(main, n, buf, start)
                continue
            if not self.fastest and self.sicn:
                main.set(n, 2, 0x1E)
            if self.check_ok and self.sel == n:
                main.set(n, 3, self.mark)

    # -- the call ------------------------------------------------------------------------------------
    def params(self):
        a = self.a
        self.loc = None
        self.sel, self.font, self.size, self.mark, self.sub_owner = 0x7FFF, -1, -1, 0xFF, True
        for i in range(1, len(a)):
            raw = a[i].replace("\n", "\r").encode("mac_roman", "replace")[:255]
            if not raw:
                raise _FullHPopError("Empty param #%d" % (i + 1))
            if raw.lower() == b"fastest":
                self.fastest = True
                continue
            pt = bytearray(raw + b"\0\0")
            x, k = _fhp_read(pt, 0, 0x2C)
            y, k = _fhp_read(pt, k, 0x2C)
            if x and y and _fhp_is_int(x) and _fhp_is_int(y):
                self.loc = (_word(_fhp_num(x)), _word(_fhp_num(y)))
                continue
            b = raw.decode("mac_roman", "replace").strip().lower()
            if b in ("true", "false"):
                self.sub_owner = b == "true"
                continue
            if _fhp_is_int(raw):
                self.sel = _word(_fhp_num(raw))
                continue
            if len(raw) == 1:
                self.mark = raw[0]
                continue
            name, k = _fhp_read(pt, 0, 0x2C)
            fid = _get_fnum(name)
            if fid == 0 and name.lower() != b"chicago":
                raise _FullHPopError("Incorrect param #%d" % (i + 1))
            self.font = fid
            size, k = _fhp_read(pt, k, 0x2C)
            if size:
                if not (_fhp_is_int(size) and _fhp_num(size) > 0):
                    raise _FullHPopError("Bad font size")
                self.size = _word(_fhp_num(size))

    def run(self):
        a = self.a
        if len(a) == 1 and len(a[0]) == 1 and a[0] in "!?=":
            return self.pstr_at({"!": b"FullHPop 1.", "?": b"FullHPop(<menu>", "=": b"<item name>"}[a[0]])
        try:
            return self.run_menu()
        except _FullHPopError as e:
            return "Error : " + str(e)

    def run_menu(self):
        a = self.a
        if not a or not a[0]:
            raise _FullHPopError("Missing or empty parameter(s)")
        self.params()
        tf = self.eval("textFont of the target")
        self.font0 = _get_fnum(tf) if tf else (0 if self.font == -1 else self.font)
        if self.font == -1:
            self.font = self.font0
        ts = self.eval("textSize of the target")
        self.size0 = _word(_fhp_num(ts)) if ts else (12 if self.size == -1 else self.size)
        if self.size == -1:
            self.size = self.size0
        if self.mark == 0xFF:
            self.mark = 0x12 if self.font == 0 else 0xC3
        target = self.eval("the target")
        h, v = self.loc if self.loc else (0, 0)
        if not self.loc:
            if b"field" in target or b"button" in target:
                r = self.eval("item 1 to 2 of rect of the target").split(b",")
                if len(r) >= 2 and _fhp_is_int(r[0]) and _fhp_is_int(r[1]):
                    h, v = _fhp_num(r[0]), _fhp_num(r[1])
            else:
                try:
                    mh, mv = (int(x) for x in str(self.it.host.mouse_loc()).split(","))
                except Exception:  # noqa: BLE001
                    mh, mv = 0, 0
                h, v = mh - self.size // 2, mv - 10
        self.target_text = b"@#&\xc0"
        if self.sel == 0x7FFF:
            if b"field" in target:
                self.target_text = self.eval("item 1 of line 1 of target")
            elif b"button" in target:
                self.target_text = self.eval("short name of the target")
        if self.ver == 5:
            self.target_text = self.target_text.replace(b"-", b"\xd0")
        elif self.ver >= 6 and self.target_text[:1] == b"-":
            self.target_text = b"\xd0" + self.target_text[1:]
        t = a[0].replace("\n", "\r").encode("mac_roman", "replace").split(b"\0")[0]
        t = t.rstrip(b"\r")
        main = self.menus[128] = _MacMenu(128)
        if len(t) == 4:
            names = res_menu_names(t.decode("mac_roman"), _all_resources(self.it))
            main.insert_names(names)
            if not main.count():
                raise _FullHPopError("No such resource")
            if self.sel == 0x7FFF:
                k = 0
                while True:
                    k += 1
                    same = self.text(main, k).lower() == self.target_text.lower()
                    if same or k >= main.count():
                        break
                if same:
                    self.sel = k
                    main.set(k, 3, self.mark)
        else:
            self.build(main, t)
        if self.sel == 0x7FFF:
            self.sel = 0
        if not self.it.host.mouse_is_down():
            return None
        m, n, enabled = main.select(self.it, self.menus, h, v)
        if not (enabled or self.sub_owner) or n <= 0:
            return None
        if not (n > 31 or m.flags >> n & 1):
            return None
        return self.result(m, n)

    def result(self, m, n):
        main = self.menus[128]
        t1, n1, s, sn = self.text(m, n), b"%d" % n, b"", b""
        if m.id != 128:
            s, sn = t1, n1
            for k in range(1, main.count() + 1):
                e = main.item(k)
                if (e[3] if e[2] == 0x1B else e[2]) == m.id:
                    t1, n1 = e[0], b"%d" % k
                    break
        w1, w2 = b"%d" % self.width(t1), b"%d" % self.width(s)

        def plain(x):
            if self.ver < 6:
                x = x.replace(b"\xd0", b"-")
                return x.replace(b",", b"\xd5") if self.ver == 5 else x
            x = b"".join(b"[%d]" % c if c in (0, 0x2C) else bytes([c]) for c in x)[:255]
            return b"-" + x[1:] if x[:1] == b"\xd0" else x
        out = b",".join([plain(t1), plain(s), n1, sn]) + b"\r" + w1 + b"," + w2
        return out[:255].decode("mac_roman").replace("\r", "\n")


def _h_pop_up_menu_counted(it, a):
    """The HPopupMenu(items, checkedItem, top, left) with no strings (6.2K,
    in HyprStat and TestIt!): items end at ",", ";" or return, and the
    items after a comma, up to the end of the line, are the submenu of
    the one before it. The scan skips the very first character, empty
    items aren't added but are counted, and the count is the submenu's
    menu id and the item it is attached to. Answers "item,1" for an item
    of the menu, "submenu,item+1" for one of a submenu, or "0"; anything
    but exactly 4 parameters answers "0"."""
    a = [str(x) for x in a]
    if len(a) != 4:
        return "0"
    t = a[0].encode("mac_roman", "replace").replace(b"\n", b"\r") + b"\0"
    item = _param_num(a[1])
    v, h = _param_num(a[2]), _param_num(a[3])
    main = _MacMenu(235)
    menus = {235: main}
    ends = b"\0\r,;"
    p = seg = count = 0
    while t[p]:
        p += 1
        if t[p] not in ends:
            continue
        if p > seg:
            main.insert(t[seg:p][:255])
        count += 1
        seg = p + 1
        if count < 235 and t[p] == 0x2C:
            sub = _MacMenu(count)
            main.set(count, 2, 0x1B)
            main.set(count, 3, count)
            while True:
                p += 1
                if t[p] in ends:
                    if p > seg:
                        sub.insert(t[seg:p][:255])
                    seg = p + 1
                if t[p] in b"\0\r;":
                    break
            menus[count] = sub
    main.set(item, 3, 0x12)
    m, n, enabled = main.select(it, menus, h, v)
    if n == 0 or not enabled:
        return "0"
    return "%d,1" % n if m is main else "%d,%d" % (m.id, n + 1)


def _h_pop_up_menu_lines(it, a):
    """The small HPopupMenu(lines, popUpItem, top, left) (2294 bytes, in
    Sunrise-set): the items of line 1 (comma-separated, AppendMenu's
    metacharacters work) are the menu and line k+1 is the submenu of item
    k (up to 50 lines). Answers "submenu,item" (submenu 0 for the menu
    itself), "0,0" for nothing. No checks at all: the mouse need not be
    down and popUpItem only places the menu."""
    a = [str(x) for x in a]
    t = a[0].encode("mac_roman", "replace").replace(b"\n", b"\r") if a else b""
    if not t:
        return "0,0"
    v, h = _string_to_num(_arg(a, 2, "").encode("mac_roman", "replace")), \
        _string_to_num(_arg(a, 3, "").encode("mac_roman", "replace"))
    lines = t.split(b"\r")[:50]
    menus = [_MacMenu(128 + k) for k in range(len(lines))]
    for k, ln in enumerate(lines):
        if k and not b"\r".join(lines[k:]):
            break       # nothing after the last return
        pos = 0
        while True:
            chunk = ln[pos:pos + 255]
            n, last = len(chunk), len(chunk) < 251
            if not last:    # a long line goes in pieces, cut at a comma
                j = 250
                while j > 0 and chunk[j - 1] != 0x2C:
                    j -= 1
                n = j - 1
            if n < 1:
                break
            menus[k].insert(chunk[:n].replace(b",", b";"))
            pos += n + 1
            if last:
                break
    main = menus[0]
    for k in range(1, len(menus)):
        if menus[k].count():
            main.set(k, 2, 0x1B)
            main.set(k, 3, 128 + k)
    m, n, enabled = main.select(it, {x.id: x for x in menus}, h, v)
    if n == 0 or not enabled:
        return "0,0"
    return "%d,%d" % (m.id - 128, n)


@xfcn("HPopUpMenu", "FullHPop")
def h_pop_up_menu(it, a):
    """HPopUpMenu(items, checkedItem, top, left) pops up a menu with the given
    items (lines, or one line of comma-separated items) with its top-left
    corner at left,top and returns "item text,,item number", or empty if
    nothing was chosen. A menu resource id as items isn't supported."""
    import zlib
    for name in ("HPopUpMenu", "FullHPop"):
        code = _ext_code(it, name)
        if code:
            if b"Jon Pugh" in code:
                return _h_pop_up_menu_pugh(it, a, code)
            if b"Usage: HPopupMenu choices" in code:
                return _h_pop_up_menu_choices(it, a, code)
            if b"Insufficient Parameters" in code:
                return _h_pop_up_menu_choices(it, a, code, early=True)
            if b"APPENDALLMENUITEMS" in code:
                return _h_pop_up_menu_lines(it, a)
            if zlib.crc32(code) in (0xCD6999AD, 0x0913BDB5):
                return _h_pop_up_menu_counted(it, a)
            if b"FullHPop(<menu>" in code:
                return _FullHPop(it, a, code).run()
            break
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
    where = _path_info_parts(it, str(_arg(a, 0)) if a else str(it.globals.get("xyztempglobal", "")))
    if where is None:
        return "0,0,"
    return "4294967295,%d,%s" % (_dir_id(where[0]), where[1])


def _path_info_parts(it, path):
    """(folder on disk, last name) as GetPathInfo sees a path, or None."""
    import zlib
    path = path.rstrip(":") if path.count(":") > 1 or not path.endswith(":") else path
    vol, _, rest = path.partition(":")
    leaf = rest.rsplit(":", 1)[-1] if rest else ""
    if not vol or (not leaf and zlib.crc32(_ext_code(it, "GetPathInfo")) == 0x6C6344FD):
        return None             # (the READ ME FIRST stack's version wants a name)
    err, parent = _mac_resolve(vol + ":" + rest.rsplit(":", 1)[0] if ":" in rest else vol + ":")
    if err or not os.path.isdir(parent):
        return None
    return parent, leaf


def _fs_delete(path):
    """Deletes what a Mac path names, as the File Manager's FSDelete does,
    and returns its result code: 0, or -35 (no such volume), -120 (folder
    not found), -43 (file not found), -47 (a volume or a folder that is not
    empty). A name without a colon is in the default folder, the top of the
    first volume. The resource fork HCX keeps next to a file goes with it."""
    err, local = _mac_resolve(path if ":" in path else ":" + path)
    if err:
        return {"nsv": -35, "dirnf": -120, "bdnam": -37}[err]
    if path.endswith(":") and not os.path.isdir(local):
        return -120
    return _fs_delete_local(local)


def _fs_delete_local(local):
    if not os.path.lexists(local):
        return -43
    if _is_volume(local) or (os.path.isdir(local) and any(not n.startswith(".") for n in os.listdir(local))):
        return -47
    try:
        if os.path.isdir(local):
            os.rmdir(local)
        else:
            os.remove(local)
            if os.path.isfile(local + ".rsrc"):
                os.remove(local + ".rsrc")
    except OSError:
        return -36
    return 0


_DELETEFILE_ERRS = {-58: "External File System", -47: "File Busy", -46: "Software Volume Lock",
                    -45: "File Locked", -44: "Disk Is Locked", -43: "File Not Found",
                    -37: "Bad File Name", -36: "I/O Error", -35: "No Such Volume"}


@xcmd("DeleteFile", "FileDelete")
def delete_file(it, a):
    """DeleteFile path: the versions of the test stacks (HyperBBS's, the
    Gibson Library's, the Core Collection's, which asks for the file in a
    file dialog, Didacticiel's and the File/Volume XCMDs'), told apart by
    their messages, each with its own errors in "the result". Folders go
    only if empty. Checked against the originals in tools/xemu."""
    import zlib
    a = [str(x) for x in a]
    code = _ext_code(it, "DeleteFile") or _ext_code(it, "FileDelete")
    if b"FSDelete returned error" in code:            # HyperBBS
        if len(a) != 1:
            it.result = "parameter count is not 1"
        elif _fs_delete(a[0]):
            it.result = "FSDelete returned error"
    elif b"xyzTempGlobal" in code:                    # the Gibson Library: through GetPathInfo
        if len(a) == 1:
            it.globals["xyztempglobal"] = ""
            where = _path_info_parts(it, a[0])
            if where is None or _fs_delete_in(*where):
                it.result = "File not deleted"
    elif zlib.crc32(code) == 0x3D386F9A:              # the Core Collection: any file the user picks
        r = it.host.choose_file("file", "", "", None)
        if r:
            _fs_delete(r)
    elif b"Filename Parameter Missing" in code:       # Didacticiel
        if not a:
            it.result = "Filename Parameter Missing"
        elif len(a) > 1:
            it.result = "Incorrect Parameter Count"
        else:
            e = _fs_delete(a[0])
            if e:
                it.result = _DELETEFILE_ERRS.get(e) or "Unknown Error: %d" % (e & 0xFFFFFFFF)
    elif b"Unable to delete file" in code:            # the File/Volume XCMDs
        e = _fs_delete(a[0] if a else "")
        if e:
            it.result = "Error: Unable to delete file: %d" % e
    elif _fs_delete(a[0] if a else ""):
        it.result = "File not deleted"


@xfcn("DeleteFile")
def delete_file_fn(it, a):
    """The DeleteFile XFCN of Best of HC Externals: deletes the file of a
    full path; 0, the File Manager's error (unsigned), or 1 when the
    parameter is missing or not a full path."""
    a = [str(x) for x in a]
    if len(a) != 1 or ":" not in a[0]:
        return "1"
    if a[0].startswith(":"):
        return str(-35 & 0xFFFFFFFF)
    return str(_fs_delete(a[0]) & 0xFFFFFFFF)


def _fs_delete_in(parent, leaf):
    """FSDelete of a name in a folder (HDelete), the Mac way."""
    if not leaf:
        return -47 if _is_volume(parent) else _fs_delete_local(parent)
    if ":" in leaf:
        return -120
    return _fs_delete_local(_ci_match(parent, leaf.replace("/", "_")))


@xcmd("CreateFolder", "NewFolder", "MakeFolder")
def create_folder(it, a):
    """CreateFolder: Frederic Rinaldi's 1.1 to 1.3 (CreateFolder <folder
    path>), the Gibson Library's (CreateFolder folder path, name) and
    Intellimation's (createFolder <path>). Other versions and the other
    names take a full path and make the folders on the way. Checked
    against the originals in tools/xemu."""
    a = [str(x) for x in a]
    code = _ext_code(it, "CreateFolder")
    if b"xyzTempGlobal" in code:
        r = _create_folder_gibson(it, a)
    elif b"Intellimation" in code:
        r = _create_folder_intellimation(a)
    elif b"RINALDI" in code:
        r = _create_folder_rinaldi(a, _rinaldi_version(code, "CreateFolder"))
    else:
        r = _create_folder_any(a)
    if r is not None:
        it.result = r


def _new_folder(path):
    """Makes the folder that a Mac path names ("" or an error code as
    in _mac_resolve, plus "dup"). A name without a colon is in the default
    folder, the top of the first volume, as on the Mac."""
    if ":" not in path:
        path = ":" + path
    err, local = _mac_resolve(path)
    if err:
        return err
    if os.path.lexists(local):
        return "dup"
    try:
        os.mkdir(local)
    except OSError:
        return "dirnf"
    return ""


def _create_folder_rinaldi(a, ver):
    if ver == (0, 0):
        ver = (1, 3)
    if len(a) == 1 and a[0] in ("?", "!", "="):
        years = {(1, 1): "1990,1991", (1, 2): "1990…1992"}.get(ver, "1990…1999")
        return {"?": ("68K_" if ver >= (1, 3) else "") + "CreateFolder <folder path>",
                "!": "Version %d.%d, © Frederic RINALDI %s" % (ver + (years,)),
                "=": "<error> if any"}[a[0]]
    if not a or not a[0]:
        return "Error : Missing or empty parameter"
    path = a[0]
    if ver < (1, 2):        # 1.1 first looks for the volume the path starts with
        from .hthost import files_root
        vol = path.split(":")[0]
        if not vol or not os.path.isdir(_ci_match(files_root(), vol.replace("/", "_"))):
            return "Error : Volume not found"
    if path.endswith(":"):
        path = path[:-1]
    err = _new_folder(path)
    return "Error : " + _RINALDI_ERR[err] if err else None


def _create_folder_gibson(it, a):
    """The Library's: the folder named by the 2nd param inside the one of
    the 1st, through its GetPathInfo XFCN and the global xyzTempGlobal
    (left empty); "the result" is empty or one of its messages."""
    if len(a) != 2:
        return ""
    it.globals["xyztempglobal"] = ""
    path, name = a
    err, folder = _mac_resolve(path) if path else ("dirnf", None)
    if err or not os.path.lexists(folder):
        return "Could not get directory id."
    name = name[1:] if name.startswith(":") else name
    if not name or ":" in name or not os.path.isdir(folder):
        return "Could not create new folder."
    target = _ci_match(folder, name.replace("/", "_"))
    if os.path.lexists(target):
        return "Could not create new folder."
    try:
        os.mkdir(target)
    except OSError:
        return "Could not create new folder."
    return ""


def _create_folder_intellimation(a):
    """Through FSMakeFSSpec and FSpDirCreate: their error numbers, and a
    name cut to 31 characters."""
    if not a or a[0] in ("?", "!"):
        return "createFolder <path> -- ©1996 Intellimation"
    path = a[0]
    if ":" not in path:
        return "Error: Invalid path #-35"
    codes = {"nsv": -35, "dirnf": -120, "bdnam": -37, "dup": -48}
    err, local = _mac_resolve(path)
    if not err and path.endswith(":") and not os.path.isdir(local):
        err = "dirnf"
    if err:
        return "Error: Invalid path #%d" % codes[err]
    local = os.path.join(os.path.dirname(local), os.path.basename(local)[:31])
    if os.path.lexists(local):
        return "Error: Failed to create #-48"
    try:
        os.mkdir(local)
    except OSError:
        return "Error: Failed to create #-120"
    return None


def _create_folder_any(a):
    if len(a) >= 2:
        parent = _local(_arg(a, 0).rstrip(":") or _arg(a, 0))
        if not os.path.isdir(parent):
            return "Could not get directory id."
        p = os.path.join(parent, str(_arg(a, 1)).replace("/", "_").replace(":", "_"))
        if os.path.exists(p):
            return "Could not create new folder."
        try:
            os.mkdir(p)
        except OSError:
            return "Could not create new folder."
        return None
    try:
        os.makedirs(_local(_arg(a, 0)), exist_ok=True)
    except OSError as e:
        return str(e)
    return None


# -- Finder info and Mac paths, for Frederic Rinaldi's file externals --------------------------
# Type and creator live in an extended attribute, as tools/xemu's File
# Manager keeps them; a file without one gets a type from its name.

_FINDER_XATTR = "user.hcx.finderinfo"


def _finder_info(path):
    """(type, creator, flags) of a file."""
    try:
        v = os.getxattr(path, _FINDER_XATTR)
        if len(v) >= 8:
            return v[:4], v[4:8], int.from_bytes(v[8:10], "big") if len(v) >= 10 else 0
    except (OSError, AttributeError):
        pass
    low = path.lower()
    if low.endswith((".txt", ".text")) or "." not in os.path.basename(path):
        return b"TEXT", b"ttxt", 0
    if low.endswith((".pict", ".pct")):
        return b"PICT", b"ttxt", 0
    return b"BINA", b"????", 0


def _set_finder_info(path, ftype=None, creator=None):
    old_t, old_c, flags = _finder_info(path)
    try:
        os.setxattr(path, _FINDER_XATTR, (ftype or old_t) + (creator or old_c) + flags.to_bytes(2, "big"))
        return True
    except (OSError, AttributeError):
        return False


# FindFolder's folder types: (name, inside the System Folder, always there)
FOLDERS = {
    "macs": ("System Folder", False, True),
    "extn": ("Extensions", True, True),
    "ctrl": ("Control Panels", True, True),
    "pref": ("Preferences", True, True),
    "amnu": ("Apple Menu Items", True, True),
    "strt": ("Startup Items", True, True),
    "shdn": ("Shutdown Items", True, True),
    "font": ("Fonts", True, True),
    "prnt": ("PrintMonitor Documents", True, False),
    "desk": ("Desktop Folder", False, True),
    "trsh": ("Trash", False, True),
    "empt": ("Trash", False, True),
    "temp": ("Temporary Items", False, True),
}


def find_folder(vol, kind, create=False):
    """FindFolder: the local path of a special folder on a volume (a
    folder at the top of HCX's folder for Mac files), made if every
    System 7 disk has it or if asked to; None if there is no such folder.
    tools/xemu's Alias Manager uses this too."""
    entry = FOLDERS.get(kind)
    if entry is None:
        return None
    name, in_system, always = entry
    path = os.path.join(_ci_match(vol, "System Folder") if in_system else vol, name)
    path = _ci_match(os.path.dirname(path), name)
    if not os.path.isdir(path):
        if not (always or create):
            return None
        os.makedirs(path, exist_ok=True)
    return path


def _ci_match(folder, name):
    """The entry of folder called name, in any case, as HFS finds it."""
    p = os.path.join(folder, name)
    if os.path.lexists(p):
        return p
    try:
        for n in os.listdir(folder):
            if n.lower() == name.lower():
                return os.path.join(folder, n)
    except OSError:
        pass
    return p


def _mac_resolve(path):
    """("", local path) for a Mac path, or (error, None) with the error the
    File Manager would give: "nsv" (no such volume), "dirnf" (a folder on
    the way is missing) or "bdnam". Names match in any case, "::" goes up a
    folder and a colon at the end is ignored. A path that starts with a
    colon starts from the default folder, the top of the first volume, as
    in tools/xemu. A name with no volume is looked for where HCX keeps such
    files (see hthost.mac_to_local)."""
    import re
    from .hthost import files_root
    root = files_root()
    path = str(path)
    if ":" in path and not path.startswith(":"):
        vol, _, rest = path.partition(":")
        base = _ci_match(root, vol.replace("/", "_"))
        if not vol or not os.path.isdir(base):
            return "nsv", None
        parts = rest.split(":")
    elif path.startswith(":"):     # from the default folder: the top of the first volume
        vols = sorted((n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))
                       and not n.startswith(".")), key=lambda n: (n.lower() != "hd", n.lower())) \
            if os.path.isdir(root) else []
        if not vols:
            return "nsv", None
        base, parts = os.path.join(root, vols[0]), path[1:].split(":")
    else:
        base, parts = root, path.split(":")
    if len(parts) > 1 and parts[-1] == "":
        parts.pop()
    top, cur = base, base
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "":
            if not last and cur != top:
                cur = os.path.dirname(cur)
            continue
        nxt = _ci_match(cur, re.sub(r"[/\x00]", "_", part))
        if not last and not os.path.isdir(nxt):
            return "dirnf", None
        cur = nxt
    return "", cur


def _is_volume(path):
    from .hthost import files_root
    return os.path.dirname(os.path.realpath(path)) == os.path.realpath(files_root())


def _mac_date(t, form):
    """A file date as the Mac's International Utilities print it (US)."""
    import time
    lt = time.localtime(t)
    if form == "date":
        return time.strftime("%-m/%-d/%y", lt)
    return time.strftime("%-I:%M:%S %p", lt)


_RINALDI_ERR = {"nsv": "Volume not found", "dirnf": "Folder not found", "fnf": "File not found",
                "bdnam": "Bad Name", "notfile": "Not a file", "busy": "File is busy", "io": "I/O Error",
                "locked": "Volume is locked", "dup": "Duplicate file/folder name"}


def _rinaldi_version(code, name):
    import re
    m = re.search(rb"%s (\d+)\.(\d+)" % name.encode(), code) or re.search(rb"Version (\d+)\.(\d+)", code)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


@xfcn("GetFInfo", "GetFileInfo")
def get_finfo(it, a):
    """Frederic Rinaldi's GetFInfo 1.0 to 1.5: "type,creator,creDate,
    creTime,modDate,modTime,size,data size,resource size" and the 16
    Finder flags as booleans (1.5 adds the space the file takes on disk)."""
    import time
    code = _ext_code(it, "GetFInfo") or _ext_code(it, "GetFileInfo")
    ver = _rinaldi_version(code, "GetFileInfo")
    v10 = ver < (1, 2)
    a = [str(x) for x in a]
    first = a[0] if a else ""
    if v10:
        if a == ["!"]:
            return "Version 1.0, © Frederic RINALDI 1990"
        if a == ["?"]:
            return "GetFInfo <file path or name>"
        if not a:
            return "Error : Missing parameter(s)"
        if first.endswith(":"):
            return "Error : First param must not end with colon"
        if ":" not in first.lstrip(":"):     # 1.0 wants a full path
            return "Error : Volume not found"
    else:
        if len(a) == 1 and first in ("!", "?", "="):
            return {"!": "GetFileInfo %d.%d, © Frederic RINALDI 1990%s" % (
                        ver + ({(1, 2): ",1991", (1, 3): "…1993", (1, 4): "…1998"}.get(ver, "…1999"),)),
                    "?": 'GetFileInfo <file path or name>[,"DontResolveAlias"]',
                    "=": "<type>,<creator>,<creDate>,<creTime>,<modDate>,<modTime>,<size>,<data len>,"
                         "<res len>,<flag 1>…<flag 16>"}[first]
        if not first:
            return "Error : Missing or empty parameter(s)"
        if len(a) > 1 and a[1].lower() != "dontresolvealias":
            return 'Error : Second param can only be "DontResolveAlias"'
    err, path = _mac_resolve(first)
    if not err:
        if not os.path.lexists(path):
            err = "fnf"
        elif os.path.isdir(path):
            err = "fnf" if v10 else "notfile"
    if err:
        msg = _RINALDI_ERR[err]
        return "Error : " + ("Directory not found" if v10 and err == "dirnf" else msg)
    st = os.stat(path)
    ftype, creator, flags = _finder_info(path)
    size = st.st_size
    if ver == (1, 2):       # its own date format
        lt = time.localtime(st.st_mtime)
        h = lt.tm_hour if lt.tm_hour <= 12 else lt.tm_hour - 12
        d = "%02d/%02d/%04d" % (lt.tm_mon, lt.tm_mday, lt.tm_year)
        t = "%02d:%02d:%02d %s" % (h, lt.tm_min, lt.tm_sec, "PM" if lt.tm_hour > 12 else "AM")
    else:
        d, t = _mac_date(st.st_mtime, "date"), _mac_date(st.st_mtime, "time")
    out = [ftype.decode("mac_roman"), creator.decode("mac_roman"), d, t, d, t]
    if v10:
        out = out[:3] + [d]
    out += [str(size), str(size), "0"]
    out += ["true" if flags & (1 << i) else "false" for i in range(16)]
    if ver >= (1, 5):
        out.append(str((size + 511) // 512 * 512))
    return ",".join(out)


@xfcn("GetVInfo")
def get_vinfo(it, a):
    """Frederic Rinaldi's GetVInfo 1.0, 1.2 and 1.6 for a folder at the
    top of HCX's folder for Mac files (a volume): "size,free,files,folders,
    locked,ejectable,creation date and time,modification date and time".
    Sizes are counted like an HFS volume of at most 65535 blocks of 32K,
    in K for 1.0 and 1.6 and in bytes for 1.2."""
    import shutil
    from .hthost import files_root
    code = _ext_code(it, "GetVInfo")
    ver = _rinaldi_version(code, "GetVInfo")
    a = [str(x) for x in a]
    first = a[0] if a else ""
    if len(a) == 1 and first in ("!", "?", "="):
        return {
            "!": "GetVInfo 1.0, © Frederic RINALDI 1992" if ver < (1, 1) else
                 "GetVInfo %d.%d, © Frederic RINALDI 1992…1998" % ver if ver < (1, 6) else
                 "68K_GetVInfo %d.%d, © Frederic RINALDI 1992…1999" % ver,
            "?": "GetVInfo(<volume name or volume alias pathname>)",
            "=": "<size,free,files nb,folders nb,locked,ejectable,Cre date,Cre time,Mod date,Mod time[,Bak date,Bak time]>"
                 if ver < (1, 1) else
                 "<size,free,files nb,folders nb,locked,ejectable,creation date,creation time,modification date,"
                 "modification time,backup date,backup time>" if ver < (1, 6) else
                 "<total size,free size,files nb,folders nb,locked,ejectable,creation date,creation time,"
                 "modification date,modification time,backup date,backup time>"}[first]
    if not first:
        return "Error : Missing or empty parameter"
    if first == ":":
        return "Error : Volume not found"
    err, path = _mac_resolve(first if ":" in first else first + ":")
    if not err and not os.path.lexists(path):
        err = "fnf"
    if err:
        return "Error : " + _RINALDI_ERR[err]
    if not os.path.isdir(path) and ver >= (1, 2):
        return "Error : Not a folder"
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(files_root()))
    vol = os.path.join(files_root(), rel.split(os.sep)[0])
    try:
        st = os.stat(vol)
        du = shutil.disk_usage(vol)
    except OSError:
        return "Error : I/O Error"
    blk = 32768
    total, free = min(du.total // blk, 0xFFFF), min(du.free // blk, 0x7FFF)
    unit = 1 if ver == (1, 2) else 1024
    files = dirs = 0
    for _d, ds, fs in os.walk(vol):
        ds[:] = [n for n in ds if not n.startswith(".")]
        dirs += len(ds)
        files += sum(1 for n in fs if not n.startswith("."))
    d, t = _mac_date(st.st_mtime, "date"), _mac_date(st.st_mtime, "time")
    return ",".join([str(total * blk // unit), str(free * blk // unit), str(files), str(dirs),
                     "false", "false", d, t, d, t])


def _restext_help(ver, what):
    if what == "!":
        years = {(1, 1): "1889,1990,1991", (1, 4): "1889,1990,1991,1992", (2, 0): "1989…1993",
                 (2, 3): "1989…1994"}.get(ver, "1989…1998")
        return "ResText %d.%d, © Frederic RINALDI %s" % (ver + (years,))
    if ver < (2, 0):
        return 'ResText(<res type>,<res ID or name>[,<fileName>][,<stripCtrl>][,"DontResolveAlias"])'
    return 'ResText(<res type>,<res ID or name>[,<fileName>][,<#nb>][,<stripCtrl>][,"hex"][,"DontResolveAlias"])'


@xfcn("ResText")
def res_text(it, a):
    """Frederic Rinaldi's ResText 1.1 to 2.5: a resource as text, from the
    stack's resources or from a file's resource fork (the "name.rsrc" file
    HCX keeps next to a file, see macfiles). STR and STR# give their
    strings one per line; 2.x takes "#n" for one string of a STR#, and
    "hex". Control characters show as [n] (1.1 drops them) unless the
    stripCtrl param is false. Checked against the originals in tools/xemu."""
    import re
    from .macfiles import _sidecar_rsrc
    from .resources import parse_resource_fork
    code = _ext_code(it, "ResText")
    ver = _rinaldi_version(code, "ResText")
    if ver == (0, 0):
        ver = (2, 5)
    v2 = ver >= (2, 0)
    a = [str(x) for x in a]
    if a in (["!"], ["?"]):
        return _restext_help(ver, a[0])
    if len(a) < 2 or not a[0] or not a[1]:
        return "Error : Missing or empty parameter(s)"
    rtype = a[0]
    if len(rtype) != 4:
        return "Error : Bad resource type"
    strip, hexa, nb, fname = True, False, None, None
    for p in a[2:]:
        low = p.strip().lower()
        if low in ("true", "false"):
            strip = low == "true"
        elif low == "dontresolvealias":
            pass
        elif v2 and low == "hex":
            hexa = True
        elif v2 and p.startswith("#"):
            nb = p[1:]
        else:
            fname = p
    if fname:
        err, path = _mac_resolve(fname)
        if not err and not os.path.lexists(path):
            err = "fnf"
        if err:
            return "Error : " + _RINALDI_ERR[err]
        if os.path.isdir(path):
            return "Error : Not a file"
        res = parse_resource_fork(_sidecar_rsrc(path))
        if not res:
            return "Error : No resource fork"
        chain = [res]
    else:
        try:
            chain = it.host.resource_chain()
        except Exception:  # noqa: BLE001
            chain = []
    key = a[1]
    by_id = re.fullmatch(r"[+-]?\d+", key) is not None
    found = None
    for res in chain:
        for r in res:
            if r[0] == rtype and (r[1] == int(key) if by_id else r[2].lower() == key.lower()):
                found = r
                break
        if found:
            break
    if found is None:
        return "Error : Resource not found"
    data = bytes(found[3])
    if ver >= (1, 4) and rtype in ("STR ", "STR#"):
        items = []
        if rtype == "STR ":
            items.append(data[1:1 + data[0]] if data else b"")
        else:
            n, pos = int.from_bytes(data[:2], "big") if len(data) >= 2 else 0, 2
            for _i in range(n):
                if pos >= len(data):
                    break
                items.append(data[pos + 1:pos + 1 + data[pos]])
                pos += 1 + data[pos]
            if v2 and nb is not None:
                if not re.fullmatch(r"\d+", nb) or int(nb) < 1:
                    return "Error : Incorrect STR# item number"
                if int(nb) > len(items):
                    return "Error : Index is out of range"
                items = [items[int(nb) - 1]]
        is_item = True
    else:
        items, is_item = [data], False

    def text(b):
        out = []
        for c in b:
            if c == 0:
                if v2 or ver >= (1, 4):
                    out.append("[0]")
            elif c == 13:
                out.append("[13]" if is_item and v2 else "\n")
            elif (c < 32 or c >= 254) and strip:
                if ver >= (1, 4):
                    out.append("[%d]" % c)
            else:
                out.append(bytes([c]).decode("mac_roman"))
        return "".join(out)

    if hexa:
        return "\n".join(" ".join("%02X" % c for c in b) for b in items)
    return "\n".join(text(b) for b in items)


def _res_fork_of(path):
    from .macfiles import _sidecar_rsrc
    from .resources import parse_resource_fork
    blob = _sidecar_rsrc(path)
    return [list(r) for r in parse_resource_fork(blob)] if blob else None


def _write_res_fork(path, res):
    """Writes a file's resources to the "name.rsrc" HCX keeps next to it."""
    from .resources import build_resource_fork
    with open(path + ".rsrc", "wb") as fh:
        fh.write(build_resource_fork([(t, rid, n, bytes(d)) for t, rid, n, d in res]))


def _unique_res_id(res, rtype):
    """A free ID for a new resource, as UniqueID would pick (at random)."""
    import random
    used = {r[1] for r in res if r[0] == rtype}
    while True:
        n = random.randint(1000, 32767)
        if n not in used:
            return n


def _short_id(n):
    return ((n + 0x8000) & 0xFFFF) - 0x8000


def _copyres_apple(it, a, jay):
    """Apple's CopyRes 1.1 (and Jay Hodgdon's 1.2, the same code): CopyRes
    type, ID, source path, dest path. A missing destination is made. Apple's
    gives the copy a free ID when its own is taken and returns the ID; Jay
    Hodgdon's refuses and returns nothing."""
    import re
    copyright = "CopyRes XCMD 1.2 © 1990 by Jay Hodgdon" if jay else \
        "CopyRes XCMD 1.1 © 1990, Apple Computer, Inc."
    if len(a) == 1 and a[0] in ("!", "?"):
        if a[0] == "?":
            return "Form: CopyRes <ResType>,<res name>,<source pathname>, <dest pathname>"
        return copyright if jay else "Copyright 1990 by Apple Computer, Inc.  All Rights Reserved"
    if len(a) != 4:
        return copyright
    rtype, key, src, dst = a
    os_err = {"nsv": -35, "dirnf": -120, "bdnam": -37}

    def open_file(p):
        err, path = _mac_resolve(p)
        if err in os_err:
            return "Error:  %d" % (os_err[err] & 0xFFFFFFFF), None
        if err or not os.path.lexists(path) or os.path.isdir(path):
            return "Error: file not found", None
        return "", path

    err, spath = open_file(src)
    if err:
        return err
    sres = _res_fork_of(spath)
    if sres is None:
        return "Error:  source file not found"
    err, dpath = _mac_resolve(dst)
    if err in os_err:
        return "Error:  %d" % (os_err[err] & 0xFFFFFFFF)
    if err or os.path.isdir(dpath):
        return "Error: file not found"
    if not os.path.lexists(dpath):
        open(dpath, "wb").close()
        _set_finder_info(dpath, b"\0\0\0\0", b"\0\0\0\0")
    m = re.fullmatch(r"\s*([+-]?\d+)\s*", key)
    rid = _short_id(int(m.group(1))) if m else None
    found = next((r for r in sres if r[0] == rtype and r[1] == rid), None)
    if found is None:
        return "Error:  resource not found"
    dres = sres if os.path.realpath(dpath) == os.path.realpath(spath) else (_res_fork_of(dpath) or [])
    if any(r[0] == rtype and r[1] == rid for r in dres):
        if jay:
            return "Error:  duplicate resource ID"
        rid = _unique_res_id(dres, rtype)
    dres.append([rtype, rid, found[2], bytes(found[3])])
    _write_res_fork(dpath, dres)
    return None if jay else str(rid)


@xcmd("CopyRes")
def copy_res(it, a):
    """Frederic Rinaldi's CopyRes 2.0 to 2.4, and Apple's 1.1 (with Jay
    Hodgdon's 1.2): copies a resource from one file's resource fork to
    another's (the "name.rsrc" files HCX keeps next to files, see macfiles).
    When the copy's name or ID is taken, Rinaldi's asks for another one, or
    whether to replace the old resource, unless told what to do by the 5th
    param. Checked against the originals in tools/xemu."""
    a = [str(x) for x in a]
    code = _ext_code(it, "CopyRes")
    if b"Apple Computer" in code or b"Jay Hodgdon" in code:
        r = _copyres_apple(it, a, b"Jay Hodgdon" in code)
    else:
        r = _copyres_rinaldi(it, a, _rinaldi_version(code, "CopyRes"))
    if r is not None:
        it.result = r


def _copyres_rinaldi(it, a, ver):
    import re
    if ver == (0, 0):
        ver = (2, 4)
    if len(a) == 1 and a[0] in ("!", "?"):
        if a[0] == "?":
            return "CopyRes <prov file>,<dest file>,<res type>,<res name or ID>[,<I|R|K>]" \
                   "[<duplicate name allowed>]"
        years = "1992" if ver < (2, 1) else "1993" if ver < (2, 3) else "1998"
        return "Version %d.%d, © Frederic RINALDI 1989…%s" % (ver + (years,))
    if len(a) < 4:
        return "Error : Missing parameter(s)"
    src, dst, rtype, key = a[:4]
    if len(rtype) != 4:
        return "Error : Bad resource type"

    def find(p):
        err, path = _mac_resolve(p)
        if not err and not os.path.lexists(path):
            err = "fnf"
        if not err and os.path.isdir(path):
            err = "fnf" if ver < (2, 1) else "notfile"
        return err, path

    err, spath = find(src)
    if err:
        return "Error : " + _RINALDI_ERR[err]
    mode = ""
    if len(a) > 4:
        mode = a[4].strip()[:1].upper()
        if mode not in ("R", "I", "K"):
            return "Error : Param 5 must begin with R[andom], I[ncrement], K[ill] or D[upName]"
    dup_ok = len(a) > 5 and _bool_param(a[5]) is True
    err, dpath = find(dst)
    if err == "fnf" and ver >= (2, 4) and dpath and not os.path.lexists(dpath):
        open(dpath, "wb").close()
        _set_finder_info(dpath, b"\0\0\0\0", b"\0\0\0\0")
        err = ""
    if err:
        return "Error : " + _RINALDI_ERR[err]
    if os.path.realpath(spath) == os.path.realpath(dpath):
        return "Error : Same dest and prov file"
    sres = _res_fork_of(spath) or []
    if re.fullmatch(r"-?\d+", key):
        found = next((r for r in sres if r[0] == rtype and r[1] == int(key)), None)
    else:
        found = next((r for r in sres if r[0] == rtype and r[2].lower() == key.lower()), None)
    if found is None:
        return "Error : Resource not found"
    dres = _res_fork_of(dpath) or []
    rid, name, data = found[1], found[2], bytes(found[3])
    aborted = "Error : Copy aborted"

    def done(target):
        if target is None:
            dres.append([rtype, rid, name, data])
            target = dres[-1]
        else:
            target[3] = data
        _write_res_fork(dpath, dres)
        return "%s,%d" % (target[2], target[1] & 0xFFFFFFFF if target[1] < 0 else target[1])

    def named(n):
        return next((r for r in dres if r[0] == rtype and r[2].lower() == n.lower()), None)

    def with_id(n):
        return next((r for r in dres if r[0] == rtype and r[1] == n), None)

    host = it.host
    if not dup_ok and named(name):     # an empty name counts too
        if mode == "K":
            return done(named(name))
        while named(name):      # "Rename ?" until the name is free, or the old one goes
            new = host.ask('%s "%s" exists. Rename ?' % (rtype, name), name)
            if not new:
                return aborted
            if new != name:
                name = new
                continue
            yes = host.answer('Kill previous %s "%s" ?' % (rtype, name), ["Cancel", "Yes", "No"])
            if yes == "Cancel":
                return aborted
            if yes == "Yes":
                return done(named(name))
    if mode == "R":
        rid = _unique_res_id(dres, rtype)
    elif mode == "I":
        ids = [r[1] for r in dres if r[0] == rtype]
        rid = max(ids) + 1 if ids else 1000
    elif mode == "K":
        return done(with_id(rid))
    while with_id(rid):         # "Renumber ?" until the ID is free, or the old one goes
        new = host.ask("%s ID=%d exists. Renumber ?" % (rtype, rid), str(_unique_res_id(dres, rtype)))
        if not new:
            return aborted
        m = re.match(r"\s*([+-]?\d+)", new)
        n = _short_id(int(m.group(1))) if m else 0
        if n != rid:
            rid = n
            continue
        yes = host.answer("Kill previous %s ID=%d  ?" % (rtype, rid), ["Cancel", "Yes", "No"])
        if yes == "Cancel":
            return aborted
        if yes == "Yes":
            return done(with_id(rid))
    return done(None)


_FILECOPY_YEARS = {(1, 3): "1990,1991", (2, 0): "1990…1992", (2, 6): "1990…1992", (2, 7): "1990…1994",
                   (3, 1): "1990…1994", (3, 2): "1990…1995", (3, 7): "1990…1997", (3, 8): "1990…1998",
                   (4, 0): "1990…1999"}


def _filecopy_help(ver, what):
    if what == "!":
        if ver < (1, 3):
            return "Version %d.%d, © Frederic RINALDI 1990" % ver
        years = _FILECOPY_YEARS.get(ver) or ("1990…1999" if ver >= (4, 0) else "1990…1998")
        return "%sFileCopy %d.%d, © Frederic RINALDI %s" % (("68K_" if ver >= (4, 0) else "",) + ver + (years,))
    if ver < (1, 3):
        return "FileCopy <file path or name> [,<dest path or name>][,<replace>]"
    if ver < (2, 0):
        return 'FileCopy <file path, name or pathname> [,<dest path or name>][,<replace>][,"DontResolveAlias"]'
    if ver < (2, 6):
        return 'FileCopy <prov file> [,<dest path or name>][,<replace>][,"DontResolveAlias"]'
    if ver < (2, 7):
        return 'FileCopy <prov file> [,<dest path or name>][,<replace>][,"DontResolveAlias"][,"DontShowProgress"]'
    return 'FileCopy <prov file> [,«dest path or name»][,«replace»][,«"DontResolveAlias"»][,' \
           '«"DontShowProgress"»][,«"DontResolveDestAlias"»]'


@xcmd("FileCopy")
def file_copy(it, a):
    r = _file_copy(it, a)
    if r:
        it.result = r


def _file_copy(it, a):
    """Frederic Rinaldi's FileCopy 1.2 to 4.0: copies a file of HCX's
    folder for Mac files (with its type and creator) to another name or
    folder; "the result" is the copy's path (empty in 1.x) or an error.
    Checked against the originals in tools/xemu. One bug is left out on
    purpose: 1.2, 2.7 and later empty a file copied onto itself with
    replace true; here the file stays as it is. (1.3's habit of adding the
    file's name to a destination file that exists is kept.) 3.8 never
    stores the size of its copy buffer where its copy loop reads it (an
    optimizer slip), so it reads whatever the stack holds and may hang;
    here 3.8 copies like 3.7."""
    import shutil
    from .hthost import local_to_mac
    code = _ext_code(it, "FileCopy")
    ver = _rinaldi_version(code, "FileCopy")
    if ver == (0, 0):
        ver = (4, 0)
    v12 = ver < (1, 3)
    a = [str(x) for x in a]
    if a in (["!"], ["?"]):
        return _filecopy_help(ver, a[0])

    def err(key):
        if v12:
            key = {"dirnf": "Directory not found", "dup": "Duplicate file name"}.get(key, key)
        return "Error : " + _RINALDI_ERR.get(key, key)

    if not a or (not a[0] and not v12):
        return "Error : Missing parameter(s)" if v12 else "Error : Missing or empty parameter(s)"
    src_name = a[0]
    # the other params: replace, flags and the destination
    flags = ["dontresolvealias"] if ver >= (1, 3) else []
    if ver >= (2, 6):
        flags.append("dontshowprogress")
    if ver >= (2, 7):
        flags.append("dontresolvedestalias")
    replace, dest = False, ""
    if v12:
        dest = a[1] if len(a) > 1 else ""
        if dest.strip().lower() in ("true", "false"):      # FileCopy file, replace
            replace, dest = dest.strip().lower() == "true", None
        if len(a) > 2:
            b = _bool_param(a[2])
            if b is None:
                return "Error : Replace param must be boolean"
            replace = b
    else:
        for p in a[1:]:
            low = p.strip().lower()
            if low in ("true", "false"):
                replace = low == "true"
            elif low not in flags:
                dest = p
    if v12 and src_name.endswith(":"):
        return "Error : First param must not end with colon"
    if ":" not in src_name and ver < (2, 6):
        return err("nsv" if v12 else "dirnf")
    e, src = _mac_resolve(src_name)
    if not e and src_name.endswith(":"):
        e = "dirnf"
    if e:
        return err(e)
    if not os.path.lexists(src) or _is_volume(src):
        return err("fnf")
    if os.path.isdir(src):
        return err("fnf" if v12 else "notfile")
    name = os.path.basename(src)
    folder = os.path.dirname(src)
    if not dest:
        if v12 and dest is not None and len(a) > 1:
            return err("dup")
        target = os.path.join(folder, "Copy of " + name)
    elif ":" not in dest:
        target = _ci_match(folder, dest.replace("/", "_"))
    else:
        e, target = _mac_resolve(dest)
        if e:
            return err(e)
        if dest.endswith(":"):
            if not os.path.isdir(target):
                return err("dirnf")
            target = _ci_match(target, name)
    if os.path.isdir(target):
        if ver < (1, 3):
            return err("dup")
        if ver < (2, 0):
            return err("notfile")
        if ver < (2, 6):
            return err("fnf")
        target = _ci_match(target, name)
    overwrite_in_place = (1, 3) <= ver < (2, 7)     # these write over the old data, no truncating
    if os.path.lexists(target):
        if ver < (2, 0) and not v12 and dest and not dest.endswith(":"):
            target = target + name          # 1.3's way with an existing file named as the destination
        elif not overwrite_in_place and not replace:
            return err("dup")
    same = os.path.lexists(target) and os.path.samefile(src, target)
    try:
        if not same:
            if overwrite_in_place and os.path.isfile(target):
                with open(src, "rb") as fi, open(target, "r+b") as fo:
                    shutil.copyfileobj(fi, fo)
            else:
                shutil.copyfile(src, target)
            shutil.copystat(src, target)
        ftype, creator, _f = _finder_info(src)
        _set_finder_info(target, ftype, creator)
    except OSError:
        return "Error : I/O Error"
    return "" if ver < (2, 0) else local_to_mac(target)


_GETDIR_OPS = ("=", "≠", ">", "<", "≥", "≤")


def _getdir_help(ver, what):
    v = "%d.%d" % ver
    if what == "!":
        if ver < (2, 0):
            return "Version %s, © Frederic RINALDI 1990" % v
        if ver >= (3, 0):
            return "68K_GetDir %s, © Frederic RINALDI 1990…%s" % (v, "2000" if ver >= (3, 4) else "1998")
        return "GetDir %s, © Frederic RINALDI 1990…%s" % (v, "1992" if ver < (2, 4) else "1994" if ver < (2, 8)
                                                          else "1998")
    if what == "=":
        return "<entry 1>[…¶<entry N> WITH <entry>=<item 1>[…,<item N>]"
    if ver < (2, 0):
        return "GetDir(<pathName>[,<a|f|d[+]>][,<filter 1>...[,<filter N>]]"
    if ver < (2, 4):
        return 'GetDir(<pathName>[,<a|f|d[+]>][,<filter 1>[…,<filter N>]] WITH <filter>=' \
               '"<b|c|d|e|f|l|m|n|o|r|s|t|x|y|z><=|≠|>|<|≥|≤><value>"'
    if ver < (2, 8):
        return 'GetDir(<pathName>[,«a|f|d[+]»][,«filter 1»[…,«filter N»]] WITH «filter»=' \
               '"<b|c|d|e|f|i|l|m|n|o|r|s|t|x|y|z><=|≠|>|<|≥|≤><value>"'
    if ver < (3, 4):
        return 'GetDir(<pathName>[,«a|f|d[+]»][«AND|OR»][,«filter 1»[…,«filter N»]] WITH «filter»=' \
               '"<b|c|d|e|f|i|l|m|n|o|r|s|t|x|y|z><=|≠|>|<|≥|≤><value%s>"' % ("1>[…|<valueN" if ver >= (3, 0) else "")
    return 'GetDir(<pathName>[,«a|f|d[+]»][,«AND|OR»][,«busy cursor»][,«filter 1»[…,«filter N»]] WITH ' \
           '«filter»="<b|c|d|e|f|i|l|m|n|o|r|s|t|x|y|z><=|≠|>|<|≥|≤><value1>[…|<valueN>]"'


def _getdir_text(name):
    return name.replace("\0", "[0]").replace("\r", "[13]").replace("\n", "[13]").replace(",", "[44]")


def _str_test(op, have, want, fold):
    if fold:
        have, want = _ascii_upper(have), _ascii_upper(want)
    return {"=": have == want, "≠": have != want, "<": have.startswith(want), ">": have.endswith(want),
            "≥": want in have, "≤": want not in have}[op]


def _num_test(op, have, want):
    return {"=": have == want, "≠": have != want, "<": have < want, ">": have > want,
            "≥": have >= want, "≤": have <= want}[op]


@xfcn("GetDir")
def get_dir(it, a):
    """Frederic Rinaldi's GetDir 1.8 to 3.4: the files and folders of a
    folder in HCX's folder for Mac files, filtered by kind, name, type,
    creator, sizes, dates and visibility, with the items chosen by "o=".
    Checked against the originals in tools/xemu, except the date filters
    (the emulator can't read dates yet)."""
    import time
    from .hthost import local_to_mac
    code = _ext_code(it, "GetDir")
    ver = _rinaldi_version(code, "GetDir")
    if ver == (0, 0):
        ver = (2, 2) if not code else (1, 8)
    v3 = ver >= (3, 0)
    a = [str(x) for x in a]
    first = a[0] if a else ""
    if len(a) == 1 and first in ("!", "?") or (a == ["="] and ver >= (2, 0)):
        return _getdir_help(ver, first)
    if not a:
        return "Error : Missing pathName" if ver < (2, 0) else "Error : Missing or empty pathName"
    if not first and ver >= (2, 0):
        return "Error : Missing or empty pathName"
    # the kind of items and recursion
    kind, recurse, rest = "a", False, a[1:]
    if rest and rest[0][:1].lower() in ("a", "f", "d") and rest[0][1:2] not in _GETDIR_OPS:
        kind, recurse, rest = rest[0][0].lower(), rest[0][1:2] == "+", rest[1:]
    use_or = False
    v18 = ver < (2, 0)
    # 1.8 also takes "x" and "y" (times), though its error message doesn't say so
    letters = "cdeflmnorstxy" if v18 else "bcdeflmnorstxyz" if ver < (2, 4) else "bcdefilmnorstxyz"
    filters, mask = [], None
    skip_empty = len(a) - len(rest)     # params 3 and later may be empty
    for i, p in enumerate(rest):
        if p == "" and i + skip_empty >= 2:
            continue
        low = p.lower()
        if ver >= (2, 8) and low in ("and", "or"):
            use_or = low == "or"
            continue
        if ver >= (3, 4) and low in ("true", "false"):
            continue            # the busy cursor
        key = low[:1]
        if not key or key not in letters:
            return "Error : First char of filter param must be [%s]" % ",".join(letters[:11] if v18 else letters)
        op = p[1:2]
        if op not in _GETDIR_OPS:
            return "Error : Second char of filter param must be [=,≠,>,<,≥,≤]"
        val = p[2:]
        if not val:
            return 'Error : Missing comparison value for "%s%s" test' % (key.upper(), op)
        if v3 and (any(f[0] == key for f in filters) or key == "o" and mask is not None):
            return 'Error : "%s" filter defined twice' % key.upper()
        if key == "o":
            if op != "=":
                return 'Error : Only "o=…" allowed'
            allowed = "cdeflmnprstvxy" + ("" if ver < (2, 0) else "bz")
            for ch in val:
                if ch.lower() not in allowed:
                    return 'Error : "%s" not allowed in output mask' % ch.upper()
            mask = val.lower()
            continue
        if key == "i":
            if op not in ("=", "≠"):
                return 'Error : Only "i=…" and "i≠…" allowed'
            b = _bool_param(val)
            if b is None:
                return "Error : Incorrect boolean value"
            filters.append(("i", op, b))
            continue
        if key in ("t", "c"):
            vals = val.split("|") if v3 else [val]
            what = "type" if key == "t" else "creator"
            if ver < (2, 0) and len(val) != 4:
                return "Error : Bad file " + what
            if (2, 8) <= ver < (3, 0) and len(val) < 4:
                return "Error : Incorrect file %s filter" % what
            if ver == (2, 4):           # 2.4 upper-cases the value (only)
                vals = [_ascii_upper(v) for v in vals]
            filters.append((key, op, vals))
            continue
        if key in ("d", "r", "l", "s", "f"):
            filters.append((key, op, int(ht.to_num(val)) if ht.is_number(val) else 0))
            continue
        if key == "n":
            filters.append((key, op, val))
            continue
        if v18 and key in "xy":     # 1.8 reads "h:mm" itself, minutes only when plain digits
            hm = val.split(":")
            if len(hm) < 2 or not hm[0].strip().isdigit():
                return "Error : Not a valid time"
            t = (int(hm[0]), int(hm[1]) if hm[1].strip().isdigit() else 0)
            filters.append((key, op, t))
            continue
        try:        # dates (b, e, m) and times (x, y, z)
            t = ht._parse_date(val)
        except ValueError:
            return "Error : Not a valid %s" % ("time" if key in "xyz" else "date")
        filters.append((key, op, t))
    if not use_or:          # without "or" a later filter replaces an earlier one
        filters = list({f[0]: f for f in filters}.values())
    err, root = _mac_resolve(first if ":" in first else first + ":")
    if not err and not os.path.lexists(root):
        err = "fnf"
    if err:
        if ver < (2, 0) and err in ("dirnf", "fnf"):
            return "Error : Directory not found"
        if ver == (2, 5) and err == "fnf":
            return "Error : Folder not found"
        return "Error : " + _RINALDI_ERR[err]
    if not os.path.isdir(root):
        return "Error : Directory not found" if ver < (2, 0) else \
            "Error : First param is not a volume or folder" if ver == (2, 5) else "Error : Not a folder"
    def tree_sizes(path):
        phys = logical = 0
        for d, ds, fs in os.walk(path):
            ds[:] = [n for n in ds if not n.startswith(".")]
            for n in fs:
                if not n.startswith("."):
                    size = os.path.getsize(os.path.join(d, n))
                    logical += size
                    phys += (size + 511) // 512 * 512
        return phys, logical

    def info(path):
        st = os.stat(path)
        folder = os.path.isdir(path)
        rec = {"folder": folder, "name": os.path.basename(path), "path": path, "mtime": st.st_mtime}
        if folder:
            names = [n for n in os.listdir(path) if not n.startswith(".")]
            rec["f"] = len(names)
            rec["s"], rec["l"] = tree_sizes(path)
        else:
            ftype, creator, flags = _finder_info(path)
            rec.update(t=ftype.decode("mac_roman"), c=creator.decode("mac_roman"), d=st.st_size, r=0,
                       l=st.st_size, s=(st.st_size + 511) // 512 * 512, flags=flags)
        rec["invisible"] = bool(rec.get("flags", 0) & 0x4000)
        return rec

    def passes(rec):
        results = []
        for key, op, want in filters:
            folder = rec["folder"]
            if key == "i":
                ok = (rec["invisible"] == want) == (op == "=")
            elif key in ("t", "c", "d", "r"):
                if folder:
                    ok = not (v3 or v18)
                elif key in ("t", "c") and v18:     # 1.8 compares the codes as numbers
                    ok = _num_test(op, rec[key].encode("mac_roman"), want[0].encode("mac_roman"))
                elif key in ("t", "c"):
                    ok = any(_str_test(op, rec[key], w, False) for w in want) if op != "≠" or len(want) == 1 \
                        else all(rec[key] != w for w in want)
                else:
                    ok = _num_test(op, rec[key], want)
            elif key == "f":
                ok = _num_test(op, rec["f"], want) if folder else ver >= (2, 4)
            elif key in ("l", "s"):
                ok = _num_test(op, rec[key], want)
            elif key == "n" and v18 and op in ("≥", "≤"):
                ok = op == "≤"      # 1.8's "contains" tests never match
            elif key == "n":
                ok = _str_test(op, rec["name"], want, True)
            elif v18 and key in "xy":
                lt = time.localtime(rec["mtime"])
                ok = _num_test(op, (lt.tm_hour, lt.tm_min), want)
            else:
                stamp = rec["mtime"] if key in "emxy" else -2082844800 + time.timezone
                if key in "bem":
                    lt, wt = time.localtime(stamp), time.localtime(want)
                    have, want2 = (lt.tm_year, lt.tm_yday), (wt.tm_year, wt.tm_yday)
                else:
                    lt, wt = time.localtime(stamp), time.localtime(want)
                    have, want2 = (lt.tm_hour, lt.tm_min), (wt.tm_hour, wt.tm_min)
                ok = _num_test(op, have, want2)
            results.append(ok)
        if not results:
            return True
        return any(results) if use_or else all(results)

    if not v18:
        text = _getdir_text
    elif mask:      # 1.8 turns commas into semicolons, and only with "o="
        def text(x):
            return x.replace(",", ";")
    else:
        def text(x):
            return x

    def field(rec, ch):
        folder = rec["folder"]
        if ch == "n":
            return text(rec["name"]) + (":" if folder else "")
        if ch == "p":
            return ":".join(text(x) for x in local_to_mac(rec["path"]).split(":")) + (":" if folder else "")
        if ch in "ctdrv":
            if folder:
                return ""
            return "unknown" if ch == "v" else text(rec[ch]) if ch in "ct" else str(rec[ch])
        if ch == "f":
            return str(rec["f"]) if folder else ""
        if ch in "sl":
            return str(rec[ch])
        stamp = rec["mtime"] if ch in "emxy" else None
        if ch in "bem":
            return _mac_date(stamp, "date") if stamp is not None else "1/1/04"
        secs = ":%S" if ver >= (3, 4) else ""
        if stamp is None:
            return "12:00%s AM" % (":00" if secs else "")
        return time.strftime("%-I:%M" + secs + " %p", time.localtime(stamp))

    out = []

    def scan(folder):
        try:
            names = sorted((n for n in os.listdir(folder) if not n.startswith(".")), key=str.lower)
        except OSError:
            return
        for n in names:
            p = os.path.join(folder, n)
            rec = info(p)
            if (kind == "a" or (kind == "d") == rec["folder"]) and passes(rec):
                if ver >= (2, 8) and not any(f[0] == "i" for f in filters) and rec["invisible"]:
                    pass
                else:
                    out.append(",".join(field(rec, ch) for ch in (mask or "n")))
            if recurse and rec["folder"]:
                scan(p)

    scan(root)
    if ver < (2, 0):
        return "".join(x + "\n" for x in out)
    return "\n".join(out)


@xcmd("SetFInfo", "SetFileInfo")
def set_finfo(it, a):
    """Frederic Rinaldi's SetFInfo 1.0, 1.1 and 1.4: SetFInfo file
    [,"t=type"][,"c=creator"][,"m=date"][,"e=date"][,"DontResolveAlias"].
    Errors go to the result. Linux keeps no creation date, so e= is only
    checked."""
    code = _ext_code(it, "SetFInfo") or _ext_code(it, "SetFileInfo")
    import re
    m = re.search(rb"SetF(?:ile)?Info (\d)\.(\d)", code)
    ver = (int(m.group(1)), int(m.group(2))) if m else (1, 0)
    dates = ver >= (1, 1)
    a = [str(x) for x in a]
    first = a[0] if a else ""
    if len(a) == 1 and first in ("!", "?", "="):
        name = "SetFInfo" if ver != (1, 1) else "SetFileInfo"
        it.result = {
            "!": "SetFInfo 1.0, © Frederic RINALDI 1992" if not dates else
                 "68K_%s %d.%d, © Frederic RINALDI 1992…%s" % (name, ver[0], ver[1], "2001" if ver >= (1, 4) else "1999"),
            "?": ("SetFileInfo" if ver < (1, 4) else "SetFInfo") + " <file path or name>[,<t=type>][,<c=creator>]" +
                 ("[,<m=modification date>][,<e=creation date>]" if dates else "") + '[,"DontResolveAlias"]',
            "=": "[<error>]"}[first]
        return
    if not first:
        it.result = "Error : Missing or empty parameter(s)"
        return
    ftype = creator = mtime = None
    prev = ""
    for n, p in enumerate(a[1:], 2):
        if dates and p == "" and prev in ("t=", "c="):
            p = prev        # 1.1 and later read the empty param as the one before
        key = p[:2].lower()
        val = p[2:]
        if key in ("t=", "c="):
            if len(val) != 4:
                it.result = ("Error : Bad file type" if key == "t=" else "Error : Bad file creator") if dates \
                    else "Error : Bad param #%d" % n
                return
            if key == "t=":
                ftype = val.encode("mac_roman", "replace")
            else:
                creator = val.encode("mac_roman", "replace")
        elif dates and key in ("m=", "e="):
            try:
                t = ht._parse_date(val)
            except ValueError:
                it.result = "Error : Bad param #%d" % n
                return
            if key == "m=":
                mtime = t
        elif p.lower() != "dontresolvealias":
            it.result = "Error : Bad param #%d" % n
            return
        prev = key
    err, path = _mac_resolve(first)
    if not err:
        if not os.path.lexists(path):
            err = "fnf"
        elif os.path.isdir(path):
            err = "notfile"
    if err:
        it.result = "Error : " + _RINALDI_ERR[err]
        return
    _set_finder_info(path, ftype, creator)
    if mtime is not None:
        try:
            os.utime(path, (mtime, mtime))
        except OSError:
            it.result = "Error : I/O Error"


@xcmd("EraseFile")
def erase_file(it, a):
    """Frederic Rinaldi's EraseFile 1.0 to 1.2: deletes a file in HCX's
    folder for Mac files (1.0 also deletes an empty folder). Errors go to
    the result."""
    code = _ext_code(it, "EraseFile")
    ver = _rinaldi_version(code, "EraseFile")
    v10 = ver < (1, 1)
    a = [str(x) for x in a]
    first = a[0] if a else ""
    if v10:
        if a == ["!"]:
            it.result = "Version 1.0, © Frederic RINALDI 1990"
            return
        if a == ["?"]:
            it.result = "EraseFile <file path or name>"
            return
        if not a:
            it.result = "Error : Missing parameter(s)"
            return
        if first.endswith(":"):
            it.result = "Error : First param must not end with colon"
            return
        if ":" not in first.lstrip(":"):
            it.result = "Error : Volume not found"
            return
    else:
        if len(a) == 1 and first in ("!", "?", "="):
            it.result = {"!": "EraseFile %d.%d, © Frederic RINALDI 1991" % ver,
                         "?": 'EraseFile <file path|name>[,"DontResolveAlias"]',
                         "=": "<error> if any"}[first]
            return
        if not first:
            it.result = "Error : Missing or empty parameter(s)"
            return
        if len(a) > 1 and a[1].lower() != "dontresolvealias":
            it.result = 'Error : Second param can only be "DontResolveAlias"'
            return
    err, path = _mac_resolve(first)
    if not err:
        if not os.path.lexists(path):
            err = "fnf"
        elif os.path.isdir(path):
            if not v10:
                err = "notfile"
            elif any(not n.startswith(".") for n in os.listdir(path)):
                err = "busy"
            elif _is_volume(path):
                err = "busy"
    if not err:
        try:
            if os.path.isdir(path):
                os.rmdir(path)
            else:
                os.remove(path)
        except OSError:
            err = "io"
    if err:
        it.result = "Error : " + ("Directory not found" if v10 and err == "dirnf" else _RINALDI_ERR[err])


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
    """AppendToClip text, font, size, styles (Gibson's Library): adds the
    text, in that font, size and comma-separated styles, to the end of the
    clipboard. Exactly four parameters, or "the result" says so. HCX's
    clipboard keeps only the text. Checked against the original in
    tools/xemu."""
    if len(a) != 4:
        it.result = "Incorrect number of parameters"
        return
    it.host.set_clipboard(it.host.clipboard_text() + str(a[0]))


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


@xfcn("GetMode")
def get_mode(it, a):
    """Frederic Rinaldi's GetMode 1.3 and 1.4: "B|C,depth,screen,B|C,max
    depth,width,height" for the screen, which is in colour at the depth
    GetDepth gives (1.3, made before thousands of colours, says 32)."""
    code = _ext_code(it, "GetMode")
    v14 = b"GetMode 1.4" in code or not code
    a = [str(x) for x in a]
    if a == ["!"]:
        return "GetMode 1.4, © Frederic RINALDI 1990…1993" if v14 else "Version 1.3, © Frederic RINALDI 1990"
    if a == ["?"]:
        return "GetMode ()"
    if a == ["="] and v14:
        return "<curr mode>,<curr depth>,<screen nb>,<max mode>,<max depth>,<screen width>,<screen height>"
    try:
        l, t, r, b = (int(v) for v in str(it.host.screen_rect()).split(","))
    except Exception:  # noqa: BLE001
        l, t, r, b = 0, 0, 640, 480
    depth = get_depth(it, [])
    return "C,%s,1,C,32,%d,%d" % (depth if v14 else "32", r - l, b - t)


def _volumes():
    """The volumes: folders at the top of HCX's folder for Mac files, HD first."""
    from .hthost import files_root
    root = files_root()
    try:
        names = [n for n in os.listdir(root) if not n.startswith(".") and os.path.isdir(os.path.join(root, n))]
    except OSError:
        names = []
    return [os.path.join(root, n) for n in sorted(names, key=lambda n: (n.lower() != "hd", n.lower()))]


def _hfs_blocks(path):
    """(total, free) 32K blocks of a volume, at most 65535 like HFS; free
    blocks stop at 32767 because many old externals read them as signed."""
    import shutil
    try:
        du = shutil.disk_usage(path)
    except OSError:
        return 0, 0
    return min(du.total // 32768, 0xFFFF), min(du.free // 32768, 0x7FFF)


@xfcn("SysENV")
def sys_env(it, a):
    """Brian Zuk's SysENV 2.2b1. HCX answers as a Mac IIci with System
    7.5.5 (the Mac tools/xemu emulates). The words come from the stack's
    STR# 9621 to 9626, as in the original, which prints numbers instead
    (or nothing) when a stack lacks them."""
    def strs(rid):
        r = _find_resource(it, ("STR#",), rid)
        return _str_list(r[3]) if r else []

    def pick(rid, i, default):
        lst = strs(rid)
        return lst[i] if 0 <= i < len(lst) else default

    misc = strs(9626)

    def word(i):
        return misc[i] if i < len(misc) else ""

    a = [str(x) for x in a]
    kind = a[0].lower() if a else None
    if kind == "?":
        return 'SysENV accepts up to 2 parameters.  0 parameters returns system info.  Passing "INITs/CDEVs", ' \
               '"Screens", "Volumes", or "SCSI" as parameter 1 returns related info.'
    if kind == "!":
        return "SysENV 2.2b1 ©1989-1991 Brian Zuk, XNet™."
    if kind is None:
        return ",".join([pick(9621, 9 + 2, "9"), "7.5.5", pick(9622, 3, "3"), pick(9623, 1, "true"),
                         pick(9624, 1, "true"), pick(9625, 2, "2"), "1", "0",
                         misc[1] if len(misc) > 1 else "true", "64" + word(2)])
    if kind == "screens":
        try:
            l, t, r, b = (int(v) for v in str(it.host.screen_rect()).split(","))
        except Exception:  # noqa: BLE001
            l, t, r, b = 0, 0, 640, 480
        return "1,%d,%d,%d,%d\n%s,%s%s,%d%s%d" % (l, t + 20, r, b, word(4), get_depth(it, []), word(5),
                                                   r - l, word(6), b - t)
    if kind == "volumes":
        vols = _volumes()
        lines = [str(len(vols))]
        for v in vols:
            total, free = _hfs_blocks(v)
            lines.append("%d%s,%d%s,%s" % (total * 32, word(7), free * 32, word(7), os.path.basename(v)))
        return "\n".join(lines)
    if kind == "scsi":
        return ""           # nothing on the SCSI bus
    if kind == "inits/cdevs":
        # INITs are files with INIT resources; files here have no resource
        # fork, so only control panels (type cdev) are found.
        cdevs = []
        vols = _volumes()
        if vols:
            for k in ("extn", "ctrl", "macs"):
                folder = find_folder(vols[0], k)
                try:
                    names = sorted((n for n in os.listdir(folder) if not n.startswith(".")), key=str.lower)
                except (OSError, TypeError):
                    continue
                for n in names:
                    p = os.path.join(folder, n)
                    if n != "System" and os.path.isfile(p) and _finder_info(p)[0] == b"cdev":
                        cdevs.append(n)
        return "," + ",".join(cdevs)
    return "\x13Incorrect parameter"


_FOLDERPATH_TYPES = {
    "SYSTEM FOLDER": "macs", "APPLE MENU ITEMS": "amnu", "CONTROL PANELS": "ctrl",
    "DESKTOP FOLDER": "desk", "EXTENSIONS": "extn", "PREFERENCES": "pref",
    "PRINTMONITOR DOCUMENTS": "prnt", "TRASH": "trsh", "NETWORK TRASH": "empt",
    "STARTUP ITEMS": "strt", "TEMPORARY ITEMS": "temp",
}


@xfcn("FolderPath")
def folder_path(it, a):
    """FolderPath(type, create) (Sudderth 1995, in Core Development Group's
    stacks): the Mac path of a special folder, from FindFolder. Apple's
    older FolderPath(prompt) asks the user for a folder instead."""
    from .hthost import local_to_mac
    a = [str(x) for x in a]
    code = _ext_code(it, "FolderPath")
    if b"Anup Murarka" in code:
        # Apple's FolderPath 1.0/1.1 (Anup Murarka & Eric Carlson):
        # FolderPath(prompt) lets the user pick a folder.
        if a == ["!"]:
            return "v1.1, ©1989, 1990 Apple Computer, Inc. by Anup Murarka & Eric Carlson" if b"v1.1" in code \
                else "v1.0, ©1989 Apple Computer, Inc. Anup Murarka & Eric Carlson"
        if a == ["?"]:
            return "FolderPath(<promptString>)"
        r = it.host.choose_file("folder", _arg(a, 0))
        if not r:
            return ""
        sep = "/" if r.startswith("/") else ":"     # a folder outside HCX's keeps its Linux path
        return r if r.endswith(sep) else r + sep
    if len(a) != 2:
        return "Syntax: FolderPath ( <type>, <create> )"
    kind = _FOLDERPATH_TYPES.get(_ascii_upper(a[0]))
    if kind is None:
        return "Error: Unrecognized type."
    vols = _volumes()
    path = find_folder(vols[0], kind, a[1] == "TRUE") if vols else None
    if path is None:
        return ""
    return local_to_mac(path) + ":"


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
    """The file dialog filter (name, globs) for a list of Mac file types:
    every type HCX knows an extension for, or None (all files)."""
    names, globs = [], []
    for t in types:
        t = str(t).strip().upper()
        t = "SND " if t[:3] == "SND" else t
        if t in _TYPE_PATTERNS and _TYPE_PATTERNS[t][0] not in names:
            names.append(_TYPE_PATTERNS[t][0])
            globs += _TYPE_PATTERNS[t][1]
    return (", ".join(names), globs) if names else None


@xfcn("GetFileName", "SFGetFile", "GetFile", "FilePath", "FileOpen")
def get_file_name(it, a):
    """GetFileName(["TYPE"...]) and the like: the full path of a file the
    user picks, or empty."""
    return it.host.choose_file("file", "", "", _patterns(a)) or ""


# The FileName XFCNs of the test stacks, by the CRC-32 of their code: how
# each reads its parameters. "first": only the first parameter is a file
# type, its first 4 characters ("prefix") or only if it has exactly 4
# ("exact"); "one": only when it is the only parameter; "two": only when
# there are no more than two; "prompt": the second parameter is the
# dialog's prompt; "all": every parameter is a type.
_FILENAME_RULES = {
    0xB7451A5E: "first prefix prompt", 0xB0478AFA: "first prefix", 0xF526F549: "first prefix",
    0x107926BB: "first prefix", 0x28C6D475: "first prefix", 0xBF97B84D: "first prefix one",
    0x05B7AB82: "first exact", 0x6A6D9D90: "first exact", 0xD95ADEB7: "first exact",
    0xC325A2ED: "first exact", 0x0E812A9F: "first exact two", 0xD6FBC77E: "all prefix",
}


@xfcn("FileName")
def file_name(it, a):
    """FileName(["TYPE"...]): the full path of a file the user picks in a
    file dialog, or empty if cancelled. The twelve versions in the test
    stacks differ only in how they read the file types (see
    _FILENAME_RULES); an unknown one reads them like the most common.
    Checked against the originals in tools/xemu."""
    import zlib
    code = _ext_code(it, "FileName")
    rule = _FILENAME_RULES.get(zlib.crc32(code), "first prefix").split()
    a = [str(x) for x in a]
    if ("one" in rule and len(a) != 1) or ("two" in rule and len(a) > 2):
        types = []
    else:
        types = a if "all" in rule else a[:1]
        types = [t[:4] for t in types if len(t) >= 4] if "prefix" in rule else [t for t in types if len(t) == 4]
    prompt = a[1] if "prompt" in rule and len(a) >= 2 else ""
    return it.host.choose_file("file", prompt, "", _patterns(types)) or ""


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


_SHOWLIST_APPLE = ("ShowList XFCN 1.1 \u00a91990 Apple Computer, Inc. All Rights Reserved.",
                   "Form:  ShowList(<listItems>, <textPrompt>, <multiSelect>, <typingSelect>, "
                   "<button names>, <selectedLine> [,<left>, <top>]",
                   "Written by Jay Hodgdon 11/2/90")


@xfcn("ShowList")
def show_list(it, a):
    """ShowList(listItems, prompt, multiSelect, typingSelect, "button
    names", selectedLine [, left, top]), Apple's XFCN 1.1: a list of the
    lines that are not empty, the prompt above it and up to four buttons
    in a column on the right, the first at the bottom (Return). A ^ at the
    end of a name draws a line above that button. Answers the button's
    name and, on a second line, the numbers of the chosen lines, joined by
    commas. ShowList("?"), ("!") and ("*") answer the form, the version and
    the author; other numbers of parameters, the version. Studium's
    version takes a delimiter after the list (see _show_list_studium)."""
    a = [str(x) for x in a]
    code = _ext_code(it, "ShowList")
    if b"Form: ShowList(listText,delimiter" in code:
        return _show_list_studium(it, a)
    version, form, author = _SHOWLIST_APPLE
    if len(a) == 1 and a[0] in ("!", "?", "*"):
        return {"!": version, "?": form, "*": author}[a[0]]
    if len(a) not in (6, 8):
        return version
    lines = [ln for ln in a[0].replace("\r", "\n").split("\n") if ln]
    multiple = _truth(a[2])
    names = a[4].split(",")[:4]
    lined = [n.endswith("^") for n in names]
    names = [n[:-1] if n.endswith("^") else n for n in names]
    sel = int(ht.to_num(a[5])) if ht.is_number(a[5]) else 0
    sel = min(sel, len(lines))
    tops = (176, 132, 92, 52)
    items = []
    for k, name in enumerate(names):
        left = 268 if k == 0 else 265
        right = 351 if k == 0 else 355
        items.append({"n": k + 1, "kind": "button", "text": name, "enabled": True,
                      "rect": (left, tops[k], right, tops[k] + 20)})
    items.append({"n": 5, "kind": "static", "text": a[1], "enabled": False, "rect": (10, 10, 355, 42)})
    items.append({"n": 6, "kind": "list", "lines": lines, "multiple": multiple, "enabled": True,
                  "selected": [sel] if sel > 0 else [], "rect": (10, 52, 250, 196)})
    for k in range(min(3, len(names))):
        if lined[k]:
            y = (162, 122, 82)[k]
            items.append({"n": 7 + k, "kind": "line", "enabled": False, "rect": (265, y, 355, y + 1)})
    spec = {"title": "", "proc": 1, "width": 365, "height": 210, "default": 1,
            "cancel": 2 if len(names) > 1 else None, "items": items}
    n, values = it.host.ditl_dialog(spec)
    if n is None or not 1 <= n <= len(names):
        n = spec["cancel"] or 1
    chosen = values.get(6, spec["items"][len(names) + 1]["selected"])
    return (names[n - 1] + "\n" + "".join("%d," % r for r in sorted(chosen)))[:-1]


def _show_list_studium(it, a):
    """Studium's ShowList(listText, delimiter, prompt, multiSelect,
    "Button 1-4", textToSelect [, HorzPos, VertPos]): the delimiter is a
    character code, or else its first character; the line equal to
    textToSelect (in any case) starts selected, or else the first. Up to
    four buttons in a column on the right, the first at the bottom
    (Return), a ^ after a name drawing a line above it. Answers the
    button's name, then each chosen line and its number ("text,2") on a
    line of its own."""
    form = "Form: ShowList(listText,delimiter,prompt,multiSelect,\"Button 1-4\",textToSelect,HorzPos,VertPos)"
    if not 5 <= len(a) <= 8:
        return form
    d = int(ht.to_num(a[1])) if ht.is_number(a[1]) else 0
    if d == 0:
        d = ord(a[1][0].replace("\n", "\r")) if a[1] else 0
    if d > 255:
        return "List delimiters must be in the range 1-255"
    delim = "\n" if d == 13 else chr(d)
    lines = a[0].replace("\r", "\n").split(delim) if d else [a[0]]
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    names, lined = [], []
    for name in a[4].split(",")[:4]:
        lined.append(name.endswith("^"))
        names.append(name[:-1] if name.endswith("^") else name)
    want = a[5].lower() if len(a) > 5 else ""
    sel = next((k + 1 for k, ln in enumerate(lines) if ln.lower() == want), 1)
    items, top = [], 175
    for k, name in enumerate(names):
        if k and lined[k - 1]:
            top -= 6
        items.append({"n": k + 1, "kind": "button", "text": name, "enabled": True,
                      "rect": (259, top, 349, top + 20)})
        top -= 35
    n = len(names)
    items.append({"n": n + 1, "kind": "static", "text": a[2], "enabled": False, "rect": (15, 2, 349, 43)})
    for k in range(n):
        if lined[k]:
            y = items[k]["rect"][1] - 11
            items.append({"n": n + 2 + k, "kind": "line", "enabled": False, "rect": (259, y, 349, y + 1)})
    items.append({"n": 10, "kind": "list", "lines": lines, "multiple": a[3].lower() == "true", "enabled": True,
                  "selected": [sel], "rect": (15, 40, 224, 200)})
    spec = {"title": "", "proc": 1, "width": 364, "height": 210, "default": 1, "cancel": None, "items": items}
    hit, values = it.host.ditl_dialog(spec)
    if hit is None or not 1 <= hit <= n:
        hit = 1
    chosen = sorted(values.get(10, [sel]))
    return names[hit - 1] + "\n" + "".join("%s,%d\n" % (lines[r - 1], r) for r in chosen if 1 <= r <= len(lines))


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
