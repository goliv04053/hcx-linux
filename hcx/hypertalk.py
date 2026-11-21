"""HyperTalk interpreter for HCX.

Covers the language people actually used in stacks: handlers and
functions with parameters, local and global variables, if/else, all repeat
forms, chunk expressions (char/word/item/line … of …), containers (variables,
fields, the message box, `it`), object references and their properties,
the usual built-in functions, `send`/`do`/`pass`/`return`, and the message
path (part → card → background → stack → stacks in use).

Commands that touch the outside world are delegated to the host window,
which maps them to Linux/GNOME (dialogs, sound, menus, files). Externals
(XCMD/XFCN) are looked up in hcx.xcmds, where they are reimplemented."""

import math
import random as _random
import re
import time

import os as _os
TRACE = bool(_os.environ.get("HCX_TRACE"))
MAX_DEPTH = 64
MAX_STEPS = 2_000_000

TRUE, FALSE = "true", "false"

CONSTANTS = {
    "empty": "", "return": "\n", "cr": "\n", "linefeed": "\n", "lf": "\n", "space": " ",
    "tab": "\t", "quote": '"', "comma": ",", "colon": ":", "true": TRUE, "false": FALSE,
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "pi": repr(math.pi), "formfeed": "\f",
    "up": "up", "down": "down", "slash": "/", "backslash": "\\",
}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
            "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "last": -1, "middle": "mid",
            "mid": "mid", "any": "any"}
CHUNKS = {"char": "char", "chars": "char", "character": "char", "characters": "char",
          "word": "word", "words": "word", "item": "item", "items": "item",
          "line": "line", "lines": "line"}
OBJ_WORDS = {"card": "card", "cd": "card", "cards": "card", "cds": "card",
             "background": "bg", "bkgnd": "bg", "bg": "bg", "backgrounds": "bg", "bkgnds": "bg", "bgs": "bg",
             "button": "button", "btn": "button", "buttons": "button", "btns": "button",
             "field": "field", "fld": "field", "fields": "field", "flds": "field",
             "stack": "stack", "stacks": "stack", "part": "part", "parts": "part"}
LAYER_WORDS = {"card": "cd", "cd": "cd", "background": "bg", "bkgnd": "bg", "bg": "bg"}


class HTError(Exception):
    where = None


class _Exit(Exception):
    def __init__(self, kind, name=None):
        super().__init__(kind)
        self.kind = kind      # 'handler' | 'hypercard' | 'repeat' | 'next' | 'pass'
        self.name = name


class _Return(Exception):
    def __init__(self, value):
        super().__init__("return")
        self.value = value


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOK = re.compile(r'''
    (?P<str>"[^"]*"?)
  | (?P<num>\d+\.\d*|\.\d+|\d+)
  | (?P<op><=|>=|<>|≤|≥|≠|&&|[-+*/^&=<>(),:¬]|\[|\])
  | (?P<word>[^\s"\-+*/^&=<>(),:¬≤≥≠\[\]]+)
''', re.X)


class Tok:
    __slots__ = ("kind", "val", "low")

    def __init__(self, kind, val):
        self.kind = kind
        self.val = val
        self.low = val.lower()

    def __repr__(self):
        return "%s:%s" % (self.kind, self.val)


def tokenize(line):
    out = []
    for m in _TOK.finditer(line):
        k = m.lastgroup
        v = m.group(k)
        if k == "str":
            v = v[1:-1] if v.endswith('"') and len(v) > 1 else v[1:]
            if "\u2028" in v:
                v = v.replace("\u2028", "\n")
        out.append(Tok(k, v))
    return out


def strip_comment(line):
    """Remove `--` comments that are not inside quotes."""
    inq = False
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif ch == "-" and not inq and line[i:i + 2] == "--":
            return line[:i]
    return line


# ---------------------------------------------------------------------------
# Script structure
# ---------------------------------------------------------------------------

class Stmt:
    __slots__ = ("kind", "toks", "a", "b", "c", "text")

    def __init__(self, kind, toks=None, a=None, b=None, c=None, text=""):
        self.kind = kind
        self.toks = toks
        self.a = a
        self.b = b
        self.c = c
        self.text = text


class Handler:
    def __init__(self, kind, name, params, body):
        self.kind = kind      # 'on' | 'function'
        self.name = name
        self.params = params
        self.body = body


class Script:
    _cache = {}

    @classmethod
    def get(cls, text):
        text = text or ""
        s = cls._cache.get(text)
        if s is None:
            if len(cls._cache) > 4000:
                cls._cache.clear()
            s = cls._cache[text] = Script(text)
        return s

    def __init__(self, text):
        raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = []
        buf = ""
        for ln in raw:
            ln = strip_comment(ln).rstrip()
            # ¬ is byte 0xC2, which a Japanese (Shift-JIS) stack shows as ﾂ
            if ln.endswith(("¬", "\uff82")):
                buf += ln[:-1] + " "
                continue
            lines.append((buf + ln).strip())
            buf = ""
        if buf:
            lines.append(buf.strip())
        self.lines = lines
        self.handlers = {}
        self.functions = {}
        i = 0
        n = len(lines)
        while i < n:
            toks = tokenize(lines[i])
            if len(toks) >= 2 and toks[0].low in ("on", "function") and toks[1].kind == "word":
                kind = toks[0].low
                name = toks[1].low
                params = [t.low for t in toks[2:] if t.kind == "word"]
                body, i = self._block(lines, i + 1, ("end",), name)
                h = Handler(kind, name, params, body)
                (self.functions if kind == "function" else self.handlers).setdefault(name, h)
            i += 1

    # parse lines[i:] into statements until a line `end <name>` (consumed by caller)
    def _block(self, lines, i, enders, name=None):
        out = []
        n = len(lines)
        while i < n:
            ln = lines[i]
            if not ln:
                i += 1
                continue
            toks = tokenize(ln)
            w = toks[0].low if toks else ""
            if w == "end":
                w2 = toks[1].low if len(toks) > 1 else ""
                if "end" in enders and (name is None or w2 == name or w2 not in ("if", "repeat")):
                    return out, i
                if w2 in ("if", "repeat") and ("end " + w2) in enders:
                    return out, i
                i += 1
                continue
            if w == "else" and "else" in enders:
                return out, i
            st, i = self._stmt(lines, i, toks)
            if st is not None:
                out.append(st)
        return out, i

    def _if_block(self, lines, i, enders):
        """The statements of a block-form if (between then/else and end if)."""
        self._nest = getattr(self, "_nest", 0) + 1
        try:
            return self._block(lines, i, enders)
        finally:
            self._nest -= 1

    def _stmt(self, lines, i, toks):
        """Parse one statement starting at lines[i]. Returns (Stmt, next index)."""
        if not toks:
            return None, i + 1
        w = toks[0].low
        if w == "if":
            return self._if(lines, i, toks)
        if w == "repeat":
            body, j = self._block(lines, i + 1, ("end repeat",))
            return Stmt("repeat", toks[1:], body, text=lines[i]), j + 1
        return Stmt("cmd", toks, text=lines[i]), i + 1

    @staticmethod
    def _find_then(toks):
        depth = 0
        for k, t in enumerate(toks):
            if t.kind == "op" and t.val == "(":
                depth += 1
            elif t.kind == "op" and t.val == ")":
                depth -= 1
            elif depth == 0 and t.low == "then":
                return k
        return -1

    def _if(self, lines, i, toks):
        k = self._find_then(toks)
        n = len(lines)
        if k < 0:
            # `then` on the following line (blank lines in between are allowed)
            j = i + 1
            while j < n and not lines[j]:
                j += 1
            if j < n:
                nt = tokenize(lines[j])
                if nt and nt[0].low == "then":
                    toks = toks + nt
                    k = len(toks) - len(nt)
                    i = j
            if k < 0:
                return Stmt("if", toks[1:], [], []), i + 1
        cond = toks[1:k]
        rest = toks[k + 1:]
        i += 1
        if rest:
            if rest[0].low == "if":
                st, i = self._if_inline(rest, lines, i)
                then_block = [st]
                else_block, i, _ = self._else(lines, i, block_form=False)
            else:
                then_block, else_block, i = self._then_else_inline(rest, lines, i)
        else:
            then_block, i = self._if_block(lines, i, ("else", "end if"))
            else_block, i, _ = self._else(lines, i, block_form=True)
        return Stmt("if", cond, then_block, else_block), i

    def _if_inline(self, toks, lines, i):
        return self._if_virtual(toks, lines, i)

    def _if_virtual(self, toks, lines, i):
        # an `if` that starts in the middle of a line (after then/else)
        k = self._find_then(toks)
        j = i
        while k < 0 and j < len(lines) and not lines[j]:
            j += 1
        if k < 0 and j < len(lines):
            nt = tokenize(lines[j])
            if nt and nt[0].low == "then":
                k = len(toks)
                toks = toks + nt
                i = j + 1
        if k < 0:
            return Stmt("if", toks[1:], [], []), i
        cond = toks[1:k]
        rest = toks[k + 1:]
        if rest:
            if rest[0].low == "if":
                then_block = [self._stmt_from_toks(rest, lines, i)[0]]
                else_block, i, _ = self._else(lines, i, block_form=False)
            else:
                then_block, else_block, i = self._then_else_inline(rest, lines, i)
        else:
            then_block, i = self._if_block(lines, i, ("else", "end if"))
            else_block, i, _ = self._else(lines, i, block_form=True)
        return Stmt("if", cond, then_block, else_block), i

    def _then_else_inline(self, rest, lines, i):
        """`if c then a else b` on one line, or `if c then a` with the else
        on a following line. Returns (then block, else block, next index)."""
        k = -1
        depth = 0
        for j, t in enumerate(rest):
            if t.kind == "op" and t.val == "(":
                depth += 1
            elif t.kind == "op" and t.val == ")":
                depth -= 1
            elif depth == 0 and t.kind == "word" and t.low == "else":
                k = j
                break
        if k < 0:
            then_block = [Stmt("cmd", rest, text=" ".join(t.val for t in rest))]
            else_block, i, _ = self._else(lines, i, block_form=False)
            return then_block, else_block, i
        a, b = rest[:k], rest[k + 1:]
        then_block = [Stmt("cmd", a, text=" ".join(t.val for t in a))] if a else []
        if not b:
            else_block, i = self._if_block(lines, i, ("end if",))
            return then_block, else_block, i + 1
        st, i = self._stmt_from_toks(b, lines, i)
        return then_block, [st], i

    def _stmt_from_toks(self, toks, lines, i):
        if toks and toks[0].low == "if":
            return self._if_virtual(toks, lines, i)
        return Stmt("cmd", toks, text=" ".join(t.val for t in toks)), i

    def _else(self, lines, i, block_form):
        """Handle an optional else after an if. Returns (else block, next index, closed)."""
        n = len(lines)
        j = i
        while j < n and not lines[j]:
            j += 1
        if j >= n:
            return [], j, False
        toks = tokenize(lines[j])
        if not toks:
            return [], j, False
        if toks[0].low == "end" and len(toks) > 1 and toks[1].low == "if":
            return [], (j + 1 if block_form else i), True
        if toks[0].low != "else":
            return [], i, False
        rest = toks[1:]
        j += 1
        if not rest:
            blk, j = self._if_block(lines, j, ("end if",))
            return blk, j + 1, True
        if rest[0].low == "if":
            st, j = self._if_virtual(rest, lines, j)
            return [st], j, True
        st = Stmt("cmd", rest, text=" ".join(t.val for t in rest))
        if block_form:
            # `else stmt` closes a block if; tolerate a stray `end if` right after,
            # unless this if sits inside another block if: that `end if` is then its own
            k = j
            while k < n and not lines[k]:
                k += 1
            if k < n and not getattr(self, "_nest", 0):
                t2 = tokenize(lines[k])
                if len(t2) >= 2 and t2[0].low == "end" and t2[1].low == "if":
                    j = k + 1
        return [st], j, True


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def is_number(s):
    try:
        float(str(s).strip())
        return str(s).strip() != ""
    except ValueError:
        return False


def to_num(s):
    s = str(s).strip()
    if s == "":
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            raise HTError("Expected a number here, got “%s”" % s)


def _finite(x, f):
    """Apply f to x unless x is INF or NAN (what HyperCard shows for them)."""
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return fmt_num(x)
    return fmt_num(f(x))


DEFAULT_NUMBER_FORMAT = "0.######"
_NUMFMT = [DEFAULT_NUMBER_FORMAT]


def _apply_number_format(x, f):
    """Format x like HyperCard's numberFormat: "0.00" (always two decimals),
    "0.##" (up to two), "00" (at least two integer digits)…"""
    ip, _, fp = f.partition(".")
    min_int = ip.count("0")
    min_frac = fp.count("0")
    max_frac = len(fp)
    s = "%.*f" % (max_frac, x)
    if "." in s:
        whole, frac = s.split(".")
        frac = frac.rstrip("0")
        frac = frac + "0" * (min_frac - len(frac))
    else:
        whole, frac = s, ""
    neg = whole.startswith("-")
    whole = whole.lstrip("-")
    if whole == "0" and min_int == 0 and frac:
        whole = ""
    whole = whole.rjust(min_int, "0")
    if neg and (whole.strip("0") or frac.strip("0")):
        whole = "-" + whole
    return whole + ("." + frac if frac else "")


def fmt_num(x):
    if isinstance(x, bool):
        return TRUE if x else FALSE
    if _NUMFMT[0] != DEFAULT_NUMBER_FORMAT and isinstance(x, (int, float)) and math.isfinite(x):
        return _apply_number_format(float(x), _NUMFMT[0])
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if math.isinf(x):
            return "INF" if x > 0 else "-INF"
        if math.isnan(x):
            return "NAN"
        if x.is_integer() and abs(x) < 1e15:
            return str(int(x))
        s = ("%.6f" % x).rstrip("0").rstrip(".")
        return s
    return str(x)


def to_bool(s):
    s = str(s).strip().lower()
    if s == TRUE:
        return True
    if s in (FALSE, ""):
        return False
    raise HTError("Expected true or false, got “%s”" % s)


def b(v):
    return TRUE if v else FALSE


# chunk helpers --------------------------------------------------------------

def _spans(text, ctype, delim=","):
    """List of (start, end) of each chunk element."""
    if ctype == "char":
        return [(i, i + 1) for i in range(len(text))]
    if ctype == "word":
        return [(m.start(), m.end()) for m in re.finditer(r"[^ \t\n]+", text)]
    sep = "\n" if ctype == "line" else delim
    out = []
    start = 0
    while True:
        k = text.find(sep, start)
        if k < 0:
            if start < len(text) or not out or True:
                if start <= len(text) and (start < len(text) or not text.endswith(sep) or not out):
                    out.append((start, len(text)))
            break
        out.append((start, k))
        start = k + 1
    if text == "":
        return []
    return out


def _resolve_index(idx, count):
    if idx == "mid":
        return count // 2 + 1
    if idx == "any":
        return _random.randint(1, max(1, count))
    idx = int(idx)
    if idx < 0:
        return count + 1 + idx
    return idx


def chunk_get(text, ctype, a, z, delim=","):
    sp = _spans(text, ctype, delim)
    n = len(sp)
    a = _resolve_index(a, n)
    z = _resolve_index(z, n) if z is not None else a
    if a < 1:
        a = 1
    if z > n:
        z = n
    if a > n or z < a:
        return ""
    return text[sp[a - 1][0]:sp[z - 1][1]]


def chunk_put(text, ctype, a, z, value, prep="into", delim=","):
    sp = _spans(text, ctype, delim)
    n = len(sp)
    a = _resolve_index(a, max(n, 1))
    z = _resolve_index(z, max(n, 1)) if z is not None else a
    if ctype in ("line", "item") and a > n:
        sep = "\n" if ctype == "line" else delim
        if n == 0:
            text = sep * (a - 1)
        else:
            # "a<return>" already has an empty line 2 after its trailing return
            text = text + sep * (a - n - (1 if text.endswith(sep) else 0))
        sp = _spans(text, ctype, delim)
        if len(sp) < a:
            sp.append((len(text), len(text)))
        n = len(sp)
        if z > n:
            z = a
    if n == 0:
        return value if prep != "before" else value + text
    a = max(1, min(a, n))
    z = max(a, min(z, n))
    s0, s1 = sp[a - 1][0], sp[z - 1][1]
    if prep == "into":
        return text[:s0] + value + text[s1:]
    if prep == "before":
        return text[:s0] + value + text[s0:]
    return text[:s1] + value + text[s1:]


def chunk_delete(text, ctype, a, z, delim=","):
    sp = _spans(text, ctype, delim)
    n = len(sp)
    if n == 0:
        return text
    a = _resolve_index(a, n)
    z = _resolve_index(z, n) if z is not None else a
    if a < 1 or a > n:
        return text
    z = min(max(z, a), n)
    s0, s1 = sp[a - 1][0], sp[z - 1][1]
    if ctype in ("line", "item"):
        if s1 < len(text):
            s1 += 1          # remove the delimiter after
        elif s0 > 0:
            s0 -= 1          # or the one before (last element)
    elif ctype == "word":
        while s1 < len(text) and text[s1] in " \t":
            s1 += 1
    return text[:s0] + text[s1:]


def chunk_span(text, ctype, a, z, delim=","):
    """(start, end) character offsets of a chunk inside text."""
    sp = _spans(text, ctype, delim)
    n = len(sp)
    if n == 0:
        return (0, 0)
    a = _resolve_index(a, n)
    z = _resolve_index(z, n) if z is not None else a
    a = max(1, min(a, n))
    z = max(a, min(z, n))
    return (sp[a - 1][0], sp[z - 1][1])


def count_chunks(text, ctype, delim=","):
    if ctype == "char":
        return len(text)
    sp = _spans(text, ctype, delim)
    if ctype in ("line", "item") and text.endswith("\n" if ctype == "line" else delim) and sp:
        if sp[-1][0] == sp[-1][1] == len(text):
            return len(sp) - 1
    return len(sp)


# ---------------------------------------------------------------------------
# References (containers and objects)
# ---------------------------------------------------------------------------

class Obj:
    """A HyperCard object: ('stack', stack) ('bg', bg) ('card', card)
    ('part', kind, part, layer) ('menu', name) ('menuitem', menu, item) ('window', name)."""
    __slots__ = ("t", "a", "b", "c", "d")

    def __init__(self, t, a=None, b=None, c=None, d=None):
        self.t, self.a, self.b, self.c, self.d = t, a, b, c, d   # d: the card of a part

    def same(self, other):
        return isinstance(other, Obj) and self.t == other.t and self.a is other.a and self.b is other.b


class VarRef:
    def __init__(self, name):
        self.name = name


class ChunkRef:
    def __init__(self, ctype, a, z, base):
        self.ctype, self.a, self.z, self.base = ctype, a, z, base


class MsgRef:
    pass


class SelRef:
    pass


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

class Frame:
    def __init__(self, me, params, msg):
        self.vars = {}
        self.globals = set()
        self.me = me
        self.params = params
        self.msg = msg
        self.result = ""


class Interp:
    def __init__(self, host):
        self.host = host
        self.globals = {}
        self.depth = 0
        self.steps = 0
        self.frame = None
        self.cur = None
        self.target = None
        self.result = ""
        self.effect = None
        self.props = {"itemdelimiter": ",", "numberformat": "0.######", "lockscreen": FALSE,
                      "lockmessages": FALSE, "userlevel": "5", "cursor": "browse", "blindtyping": FALSE,
                      "powerkeys": FALSE, "textarrows": TRUE, "dragspeed": "0", "editbkgnd": FALSE,
                      "language": "English", "lockrecent": FALSE, "usermodify": FALSE,
                      "lockerrordialogs": FALSE, "longwindowtitles": FALSE,
                      "messagewatcher": "MessageWatcher", "variablewatcher": "VariableWatcher",
                      "scripteditor": "ScriptEditor", "scripttextfont": "Monaco", "scripttextsize": "9",
                      "tracedelay": "0", "printmargins": "0,0,0,0", "printtextalign": "left",
                      "printtextfont": "Geneva", "printtextheight": "13", "printtextsize": "10",
                      "printtextstyle": "plain", "dialingtime": "180", "dialingvolume": "7",
                      # painting properties
                      "brush": "8", "centered": FALSE, "filled": FALSE, "grid": FALSE, "linesize": "1",
                      "multiple": FALSE, "multispace": "1", "pattern": "12", "polysides": "4",
                      "textalign": "left", "textfont": "Geneva", "textheight": "16", "textsize": "12",
                      "textstyle": "plain"}
        from . import xcmds
        self.xcmds = xcmds

    # -- message path ---------------------------------------------------------
    def path_objects(self, target):
        h = self.host
        out = []
        card = h.card
        if target is not None:
            out.append(target)
        t = target.t if target is not None else None
        if t is None or t == "part":
            if target is not None and t == "part" and target.c is not None and target.c.kind == "card":
                card = target.c if target.a == "cd" else card
            if card is not None:
                out.append(Obj("card", card))
        if t in (None, "part", "card"):
            c = target.a if t == "card" else card
            bg = h.bg_of(c)
            if bg is not None:
                out.append(Obj("bg", bg))
        if t != "stack" or target.a is not h.stack:
            out.append(Obj("stack", h.stack))
        for st in h.stacks_in_use():
            out.append(Obj("stack", st))
        res = []
        for o in out:
            if not any(o.same(x) for x in res):
                res.append(o)
        return res

    def has_function(self, name):
        for obj in self.path_objects(self.frame.me if self.frame else None):
            if name in Script.get(self.script_of(obj)).functions:
                return True
        return False

    def script_of(self, obj):
        if obj.t == "part":
            return obj.b.script
        if obj.t in ("card", "bg", "stack"):
            return obj.a.script
        return ""

    def send(self, msg, target=None, params=(), is_function=False, set_target=False):
        """Send a message along the message path starting at `target`.
        Returns (handled, value)."""
        name = msg.lower()
        if self.depth == 0:
            self.steps = 0
            self.effect = None
            # like HyperCard at idle time
            self.props["itemdelimiter"] = ","
            self.props["numberformat"] = DEFAULT_NUMBER_FORMAT
            _NUMFMT[0] = DEFAULT_NUMBER_FORMAT
        objs = self.path_objects(target)
        prev_target = self.target
        if set_target:
            self.target = target if target is not None else (objs[0] if objs else None)
        try:
            for obj in objs:
                sc = Script.get(self.script_of(obj))
                h = (sc.functions if is_function else sc.handlers).get(name)
                if h is None:
                    continue
                try:
                    val = self.call(h, obj, list(params), msg)
                    return True, val
                except _Exit as e:
                    if e.kind == "pass":
                        continue
                    if e.kind == "hypercard" and self.depth > 0:
                        raise
                    return True, ""
            if not is_function:
                return self.builtin_message(name, params), ""
            return False, ""
        finally:
            if set_target:
                self.target = prev_target

    def call(self, h, me, params, msg):
        if self.depth >= MAX_DEPTH:
            chain = []
            f = self.frame
            while f is not None and len(chain) < 12:
                chain.append(getattr(f, "handler", "?"))
                f = getattr(f, "parent", None)
            raise HTError("Too much recursion: " + " < ".join(chain))
        frame = Frame(me, params, msg)
        frame.handler = h.name
        frame.parent = self.frame
        for k, pname in enumerate(h.params):
            frame.vars[pname] = params[k] if k < len(params) else ""
        saved = self.frame
        self.frame = frame
        self.depth += 1
        try:
            self.run_block(h.body)
            return frame.result
        except HTError as e:
            if e.where is None:
                e.where = self.where()
            raise
        except _Return as r:
            return r.value
        except _Exit as e:
            if e.kind == "handler":
                return frame.result
            raise
        finally:
            self.depth -= 1
            self.frame = saved
            if saved is not None:
                saved.result = frame.result

    def builtin_message(self, name, params):
        """What HyperCard does with messages nobody handled."""
        h = self.host
        if name in ("mouseup", "mousedown", "mousestilldown", "mouseenter", "mouseleave", "mousewithin",
                    "opencard", "closecard", "openstack", "closestack", "openbackground",
                    "closebackground", "resumestack", "suspendstack", "idle", "startup", "resume",
                    "suspend", "openfield", "closefield", "exitfield", "newcard", "deletecard"):
            return True
        if name == "domenu":
            h.domenu(params[0] if params else "", params[1] if len(params) > 1 else None)
            return True
        if name in ("arrowkey",):
            d = (params[0] if params else "").lower()
            if d == "up":
                h.go_forth(None)
            elif d == "down":
                h.go_back(None)
            elif d in ("left", "right"):
                h.go_rel("prev" if d == "left" else "next", None)
            return True
        if name in ("returnkey", "enterkey"):
            # run the statement typed in the message box
            text = h.msg_text().strip()
            if text:
                self.do_text(text)
            return True
        if name == "keydown":
            h.type_text(params[0] if params else "")
            return True
        if name == "returninfield":
            h.type_text("\n")
            return True
        if name in ("enterinfield", "tabkey"):
            h.close_field()
            return True
        if name == "functionkey":
            n = str(params[0] if params else "").strip()
            item = {"1": "Undo", "2": "Cut", "3": "Copy", "4": "Paste"}.get(n)
            if item:
                h.domenu(item, None)
            return True
        if name in ("controlkey", "commandkeydown", "appleevent", "errordialog"):
            return True
        if name == "help":
            return True
        x = self.xcmds.command(name)
        if x is not None:
            try:
                r = x(self, list(params))
            except Exception as e:  # noqa: BLE001
                h.log("XCMD %s: %s" % (name, e))
                r = None
            if r is not None:
                self.result = str(r)
            return True
        return False

    # -- execution --------------------------------------------------------------------
    def tick(self):
        self.steps += 1
        if not self.steps & 1023:
            # let the window stay responsive; it raises _Exit("hypercard") if the user stopped the script
            self.host.poll()
        limit = getattr(self.host, "max_steps", MAX_STEPS)
        if limit and self.steps > limit:
            raise HTError("Script ran too long")

    def run_block(self, block):
        for st in block:
            self.tick()
            self.cur = st
            if TRACE:
                print("  " * self.depth + (st.text or st.kind + " " + " ".join(t.val for t in (st.toks or []))))
            k = st.kind
            if k == "cmd":
                self.command(st.toks)
            elif k == "if":
                cond = self.eval_toks(st.toks) if st.toks else FALSE
                try:
                    ok = to_bool(cond)
                except HTError:
                    ok = False
                self.run_block(st.a if ok else (st.b or []))
            elif k == "repeat":
                self.repeat(st)

    def where(self):
        f = self.frame
        hn = getattr(f, "handler", "?") if f else "?"
        obj = self.obj_name(f.me) if f and f.me is not None else "?"
        line = self.cur.text if self.cur is not None and self.cur.kind == "cmd" else (
            "if/repeat " + " ".join(t.val for t in (self.cur.toks or []))) if self.cur is not None else ""
        return "%s, handler %s: %s" % (obj, hn, line.strip())

    def repeat(self, st):
        toks = st.toks
        body = st.a
        low = [t.low for t in toks]

        def loop_once():
            try:
                self.run_block(body)
            except _Exit as e:
                if e.kind == "repeat":
                    return False
                if e.kind == "next":
                    return True
                raise
            return True

        # without a window to stop it from, a runaway loop ends after a million turns
        cap = 1_000_000 if getattr(self.host, "max_steps", MAX_STEPS) else None
        if not toks or low[0] == "forever":
            n = 0
            while loop_once():
                n += 1
                if cap and n > cap:
                    break
            return
        if low[0] in ("until", "while"):
            want = low[0] == "while"
            n = 0
            while True:
                c = to_bool(self.eval_toks(toks[1:]))
                if c != want:
                    break
                if not loop_once():
                    break
                n += 1
                if cap and n > cap:
                    break
            return
        if low[0] == "with":
            # repeat with v = a [down] to b
            var = toks[1].low
            p = Parser(self, toks, 3)
            a = to_num(p.expr(stop=("to", "down")))
            down = False
            if p.peek_low() == "down":
                p.next()
                down = True
            p.expect("to")
            z = to_num(p.expr())
            i = a
            while (i >= z) if down else (i <= z):
                self.set_var(var, str(int(i)) if float(i).is_integer() else repr(float(i)))
                if not loop_once():
                    break
                try:
                    i = to_num(self.get_var(var)) + (-1 if down else 1)
                except HTError:
                    break
            return
        # repeat [for] N [times]
        start = 1 if low[0] == "for" else 0
        end = len(toks)
        if low and low[-1] == "times":
            end -= 1
        n = to_num(self.eval_toks(toks[start:end]))
        for _ in range(int(n)):
            if not loop_once():
                break

    # -- variables -----------------------------------------------------------------------
    def get_var(self, name):
        f = self.frame
        if f is not None and name in f.globals:
            return self.globals.get(name, "")
        if f is not None and name in f.vars:
            return f.vars[name]
        if f is None and name in self.globals:
            return self.globals[name]
        if name == "it":
            return ""
        return None

    def set_var(self, name, value):
        f = self.frame
        if f is None:
            self.globals[name] = value
        elif name in f.globals:
            self.globals[name] = value
        else:
            f.vars[name] = value

    # -- evaluation ------------------------------------------------------------------------
    def eval_toks(self, toks):
        p = Parser(self, toks, 0)
        v = p.expr()
        return v

    def value_of(self, ref):
        if isinstance(ref, VarRef):
            v = self.get_var(ref.name)
            return v if v is not None else ""
        if isinstance(ref, ChunkRef):
            return chunk_get(self.value_of(ref.base), ref.ctype, ref.a, ref.z, self.props["itemdelimiter"])
        if isinstance(ref, MsgRef):
            return self.host.msg_text()
        if isinstance(ref, SelRef):
            return self.host.selection_text()
        if isinstance(ref, Obj):
            if ref.t == "part":
                c = self.host.content_of(ref.a, ref.b, ref.c, card=ref.d)
                return c.text if c else ""
            if ref.t == "menu":
                return "\n".join(i[0] for i in self.host.menu_items(ref.a))
            if ref.t == "menuitem":
                return self.host.menu_item_prop(ref.a, ref.b, "name")
            return self.obj_name(ref, "long")
        return str(ref)

    def put_into(self, ref, value, prep="into"):
        if isinstance(ref, VarRef):
            old = self.get_var(ref.name) or ""
            self.set_var(ref.name, value if prep == "into" else (old + value if prep == "after" else value + old))
            return
        if isinstance(ref, ChunkRef):
            base = self.value_of(ref.base)
            new = chunk_put(base, ref.ctype, ref.a, ref.z, value, prep, self.props["itemdelimiter"])
            self.put_into(ref.base, new, "into")
            return
        if isinstance(ref, MsgRef):
            old = self.host.msg_text()
            self.host.set_msg(value if prep == "into" else (old + value if prep == "after" else value + old))
            return
        if isinstance(ref, SelRef):
            return
        if isinstance(ref, Obj) and ref.t == "part":
            c = self.host.content_of(ref.a, ref.b, ref.c, create=True, card=ref.d)
            old = c.text
            c.text = value if prep == "into" else (old + value if prep == "after" else value + old)
            if prep != "after":
                c.runs = []
            self.host.changed(ref)
            return
        if isinstance(ref, Obj) and ref.t == "menu":
            self.host.menu_put(ref.a, value, prep)
            return
        if isinstance(ref, Obj) and ref.t == "menuitem":
            self.host.menu_item_put(ref.a, ref.b, value, prep)
            return
        raise HTError("Can't put into that")

    def delete_ref(self, ref):
        if isinstance(ref, ChunkRef):
            base = self.value_of(ref.base)
            self.put_into(ref.base, chunk_delete(base, ref.ctype, ref.a, ref.z, self.props["itemdelimiter"]))
        elif isinstance(ref, Obj) and ref.t == "menu":
            self.host.menu_delete(ref.a)
        elif isinstance(ref, Obj) and ref.t == "menuitem":
            self.host.menu_delete_item(ref.a, ref.b)
        elif isinstance(ref, (VarRef, MsgRef)):
            self.put_into(ref, "")

    # -- names and properties ----------------------------------------------------------------
    def obj_name(self, o, form="abbr"):
        h = self.host
        if o.t == "stack":
            st = o.a
            if form == "short":
                return st.name
            if form == "long":
                return 'stack "%s"' % (h.stack_path(st))
            return 'stack "%s"' % st.name
        if o.t in ("card", "bg"):
            layer = o.a
            word = "card" if o.t == "card" else "bkgnd"
            if layer.name:
                base = '%s "%s"' % (word, layer.name) if form != "short" else layer.name
            else:
                base = "%s id %d" % (word, layer.id)
            if form == "long" and o.t == "card":
                return base + ' of stack "%s"' % h.stack_path(h.stack)
            return base
        if o.t == "part":
            kind, p = o.a, o.b
            word = ("card " if kind == "cd" else "bkgnd ") + p.type
            if p.name:
                return p.name if form == "short" else '%s "%s"' % (word, p.name)
            return "%s id %d" % (word, p.id)
        if o.t == "menu":
            return o.a
        if o.t == "window":
            return o.a
        return ""

    def obj_number(self, o):
        h = self.host
        if o.t == "card":
            return h.stack.cards.index(o.a) + 1 if o.a in h.stack.cards else 0
        if o.t == "bg":
            return h.stack.backgrounds.index(o.a) + 1 if o.a in h.stack.backgrounds else 0
        if o.t == "part":
            same = [q for q in o.c.parts if q.type == o.b.type]
            return same.index(o.b) + 1 if o.b in same else 0
        return 0

    def chunk_range(self, ref):
        """For a ChunkRef: (base object, start, end) in the base's text."""
        chain = []
        while isinstance(ref, ChunkRef):
            chain.append(ref)
            ref = ref.base
        base = ref
        text = self.value_of(base)
        start, end = 0, len(text)
        for c in reversed(chain):
            s0, s1 = chunk_span(text[start:end], c.ctype, c.a, c.z, self.props["itemdelimiter"])
            start, end = start + s0, start + s1
        return base, start, end

    def _chunk_base(self, ref):
        while isinstance(ref, ChunkRef):
            ref = ref.base
        return ref

    def get_prop(self, prop, o):
        prop = prop.lower()
        h = self.host
        if isinstance(o, ChunkRef):
            base = self._chunk_base(o)
            if isinstance(base, Obj) and base.t == "part":
                return h.chunk_prop(base, o, prop) if hasattr(h, "chunk_prop") else self.get_prop(prop, base)
            return ""
        if not isinstance(o, Obj) and o is not None:
            return ""
        if o is None:
            # global property
            if prop in self.props:
                return self.props[prop]
            raise HTError("Unknown property " + prop)
        if o.t == "menuitem":
            return h.menu_item_prop(o.a, o.b, prop.split()[-1] if prop.endswith("name") else prop)
        if o.t == "menu":
            return h.menu_prop(o.a, prop)
        if prop == "owner" and o.t == "card":
            return self.obj_name(Obj("bg", h.bg_of(o.a)))
        if prop == "owner" and o.t == "bg":
            return self.obj_name(Obj("stack", h.stack))
        if prop in ("name", "short name", "long name", "abbr name", "abbrev name", "abbreviated name"):
            form = prop.split()[0] if " " in prop else "abbr"
            form = {"abbrev": "abbr", "abbreviated": "abbr"}.get(form, form)
            return self.obj_name(o, form)
        if prop in ("id", "short id", "long id", "abbr id"):
            if o.t == "part":
                return str(o.b.id)
            if o.t in ("card", "bg"):
                return str(o.a.id) if "long" not in prop else "%s id %d" % ("card" if o.t == "card" else "bkgnd", o.a.id)
            if o.t == "stack":
                return str(o.a.id)
        if prop == "number":
            return str(self.obj_number(o))
        if o.t == "part":
            p = o.b
            simple = {"visible": "visible", "enabled": "enabled", "hilite": None, "highlight": None,
                      "hilight": None, "highlite": None, "autohilite": "auto_hilite",
                      "sharedhilite": "shared_hilite", "showname": "show_name", "locktext": "lock_text",
                      "dontwrap": "dont_wrap", "dontsearch": "dont_search", "sharedtext": "shared_text",
                      "fixedlineheight": "fixed_line_height", "autotab": "auto_tab",
                      "showlines": "show_lines", "widemargins": "wide_margins", "autoselect": "auto_select",
                      "multiplelines": "multiple_lines"}
            if prop in simple:
                if simple[prop] is None:
                    return b(h.get_hilite(o.a, p, o.d))
                return b(getattr(p, simple[prop]))
            if prop == "style":
                return {"roundrect": "roundRect", "checkbox": "checkBox"}.get(p.style, p.style)
            if prop in ("rect", "rectangle"):
                return "%d,%d,%d,%d" % (p.left, p.top, p.right, p.bottom)
            if prop in ("loc", "location"):
                return "%d,%d" % ((p.left + p.right) // 2, (p.top + p.bottom) // 2)
            if prop == "topleft":
                return "%d,%d" % (p.left, p.top)
            if prop in ("bottomright", "botright"):
                return "%d,%d" % (p.right, p.bottom)
            if prop == "partnumber":
                return str(o.c.parts.index(p) + 1) if p in o.c.parts else "0"
            if prop == "owner":
                return self.obj_name(Obj("card" if o.a == "cd" else "bg", o.c))
            if prop in ("left", "top", "right", "bottom", "width", "height"):
                return str(getattr(p, prop))
            if prop == "icon":
                return str(p.icon)
            if prop == "family":
                return str(p.family)
            if prop == "textfont":
                return h.font_name(p.text_font_id)
            if prop == "textsize":
                return str(p.text_size)
            if prop == "textheight":
                return str(p.text_height)
            if prop == "textstyle":
                return ",".join(sorted(p.text_style)) or "plain"
            if prop == "textalign":
                return p.text_align
            if prop == "script":
                return p.script
            if prop == "titlewidth":
                return str(p.title_width)
            if prop in ("selectedline", "selectedtext"):
                if p.selected_line:
                    if prop == "selectedline":
                        return "line %d of %s" % (p.selected_line, self.obj_name(o))
                    return chunk_get(self.value_of(o), "line", p.selected_line, None)
                return ""
            if prop == "scroll":
                return str(h.field_scroll(o.a, p))
            if prop == "text":
                return self.value_of(o)
        if o.t in ("card", "bg"):
            L = o.a
            m = {"cantdelete": "cant_delete", "dontsearch": "dont_search", "showpict": "show_pict",
                 "marked": "marked"}
            if prop in m:
                return b(getattr(L, m[prop], False))
            if prop == "script":
                return L.script
            if prop in ("rect", "rectangle"):
                return "0,0,%d,%d" % (h.stack.width, h.stack.height)
            if prop in ("width", "height"):
                return str(h.stack.width if prop == "width" else h.stack.height)
        if o.t == "stack":
            st = o.a
            m = {"cantmodify": "cant_modify", "cantdelete": "cant_delete", "cantabort": "cant_abort",
                 "cantpeek": "cant_peek", "privateaccess": "private_access"}
            if prop in m:
                return b(getattr(st, m[prop], False))
            if prop == "script":
                return st.script
            if prop in ("rect", "rectangle", "cardrect"):
                return "0,0,%d,%d" % (st.width, st.height)
            if prop in ("width", "height"):
                return str(st.width if prop == "width" else st.height)
            if prop == "version":
                return "%s,%s,%s,%s,0" % (st.created_by, st.compacted_by, st.modified_by, st.opened_by)
            if prop in ("freesize",):
                return "0"
            if prop in ("size",):
                return "1"
            if prop == "reportTemplates":
                return ""
        if o.t == "menuitem":
            return h.menu_item_prop(o.a, o.b, prop)
        if o.t == "window":
            return h.window_prop(o.a, prop)
        if prop in self.props:
            return self.props[prop]
        return ""

    def set_prop(self, prop, o, value):
        prop = prop.lower()
        h = self.host
        if isinstance(o, ChunkRef):
            base = self._chunk_base(o)
            if isinstance(base, Obj) and base.t == "part" and prop in ("textsize", "textfont", "textstyle",
                                                                        "textheight"):
                text = self.value_of(base)
                full = self.value_of(o)
                if len(full) >= len(text):
                    self.set_prop(prop, base, value)
                elif hasattr(h, "set_chunk_prop"):
                    h.set_chunk_prop(base, o, prop, value)
            return
        if not isinstance(o, Obj) and o is not None:
            return
        if o is None:
            if prop in ("lockscreen", "lockmessages", "lockrecent"):
                self.props[prop] = b(to_bool(value))
                if prop == "lockscreen":
                    h.lock_screen(to_bool(value))
                return
            if prop == "cursor":
                self.props[prop] = value
                h.set_cursor(value)
                return
            if prop == "numberformat":
                _NUMFMT[0] = str(value) or DEFAULT_NUMBER_FORMAT
            self.props[prop] = value
            return
        if o.t == "part":
            p = o.b
            simple = {"visible": "visible", "enabled": "enabled", "autohilite": "auto_hilite",
                      "sharedhilite": "shared_hilite", "showname": "show_name", "locktext": "lock_text",
                      "dontwrap": "dont_wrap", "dontsearch": "dont_search", "sharedtext": "shared_text",
                      "fixedlineheight": "fixed_line_height", "autotab": "auto_tab",
                      "showlines": "show_lines", "widemargins": "wide_margins", "autoselect": "auto_select",
                      "multiplelines": "multiple_lines"}
            if prop in simple:
                setattr(p, simple[prop], to_bool(value))
            elif prop in ("hilite", "highlight", "hilight", "highlite"):
                h.set_hilite(o.a, p, to_bool(value), o.d)
            elif prop == "style":
                p.style = value.lower()
            elif prop in ("rect", "rectangle"):
                v = [int(round(to_num(x))) for x in value.split(",")[:4]]
                if len(v) == 4:
                    p.left, p.top, p.right, p.bottom = v
            elif prop in ("topleft", "bottomright", "botright"):
                v = [int(round(to_num(x))) for x in value.split(",")[:2]]
                if len(v) == 2:
                    w, hh = p.width, p.height
                    if prop == "topleft":
                        p.left, p.top = v
                    else:
                        p.left, p.top = v[0] - w, v[1] - hh
                    p.right, p.bottom = p.left + w, p.top + hh
            elif prop in ("loc", "location"):
                v = [int(round(to_num(x))) for x in value.split(",")[:2]]
                if len(v) == 2:
                    w, hh = p.width, p.height
                    p.left, p.top = v[0] - w // 2, v[1] - hh // 2
                    p.right, p.bottom = p.left + w, p.top + hh
            elif prop in ("left", "top", "right", "bottom"):
                v = int(round(to_num(value)))
                if prop in ("left", "right"):
                    w = p.width
                    p.left = v if prop == "left" else v - w
                    p.right = p.left + w
                else:
                    hh = p.height
                    p.top = v if prop == "top" else v - hh
                    p.bottom = p.top + hh
            elif prop == "width":
                c = (p.left + p.right) // 2
                w = int(round(to_num(value)))
                p.left, p.right = c - w // 2, c - w // 2 + w
            elif prop == "height":
                c = (p.top + p.bottom) // 2
                hh = int(round(to_num(value)))
                p.top, p.bottom = c - hh // 2, c - hh // 2 + hh
            elif prop == "icon":
                p.icon = h.icon_id(value)
            elif prop == "name":
                p.name = value
            elif prop == "family":
                p.family = int(to_num(value))
            elif prop == "textfont":
                p.text_font_id = h.font_id(value)
            elif prop == "textsize":
                p.text_size = int(to_num(value))
            elif prop == "textheight":
                p.text_height = int(to_num(value))
            elif prop == "textstyle":
                p.text_style = {s.strip().lower() for s in value.split(",") if s.strip().lower() != "plain"}
            elif prop == "textalign":
                p.text_align = value.lower()
            elif prop == "script":
                p.script = value
            elif prop == "scroll":
                h.set_field_scroll(o.a, p, int(to_num(value)))
            elif prop == "titlewidth":
                p.title_width = int(to_num(value))
            h.changed(o)
            return
        if o.t in ("card", "bg"):
            m = {"cantdelete": "cant_delete", "dontsearch": "dont_search", "showpict": "show_pict",
                 "marked": "marked"}
            if prop in m:
                setattr(o.a, m[prop], to_bool(value))
            elif prop == "name":
                o.a.name = value
            elif prop == "script":
                o.a.script = value
            h.changed(o)
            return
        if o.t == "stack":
            m = {"cantmodify": "cant_modify", "cantdelete": "cant_delete", "cantabort": "cant_abort",
                 "cantpeek": "cant_peek"}
            if prop in m:
                setattr(o.a, m[prop], to_bool(value))
            return
        if o.t == "menuitem":
            h.set_menu_item_prop(o.a, o.b, prop, value)
            return
        if o.t == "menu":
            h.set_menu_prop(o.a, prop, value)
            return
        if o.t == "window":
            h.set_window_prop(o.a, prop, value)
            return

    # -- commands ---------------------------------------------------------------------
    def command(self, toks):
        w = toks[0].low
        f = getattr(self, "cmd_" + w, None)
        me = self.frame.me if self.frame else None
        if f is not None and w not in _NEVER_REDEFINED and self.handles(w, me):
            # a script can redefine a built-in command; `pass` gets the built-in one
            try:
                params = Parser(self, toks, 1).arg_list() if len(toks) > 1 else []
            except HTError:
                params = [" ".join(t.val for t in toks[1:])]
            if self._send_command(toks[0].val, me, params, builtin=True):
                return
            return f(toks)
        if f is not None:
            return f(toks)
        # user handler / external
        p = Parser(self, toks, 1)
        params = p.arg_list() if len(toks) > 1 else []
        handled, _ = self.send(toks[0].val, me, params)
        if not handled:
            self.host.log("unhandled: " + " ".join(t.val for t in toks))

    def handles(self, name, me):
        for obj in self.path_objects(me):
            if name in Script.get(self.script_of(obj)).handlers:
                return True
        return False

    def _send_command(self, name, me, params, builtin=False):
        """Run a handler for a redefined command. False when every handler passed it."""
        low = name.lower()
        for obj in self.path_objects(me):
            h = Script.get(self.script_of(obj)).handlers.get(low)
            if h is None:
                continue
            try:
                self.call(h, obj, list(params), name)
                return True
            except _Exit as e:
                if e.kind == "pass":
                    continue
                if e.kind == "hypercard" and self.depth > 0:
                    raise
                return True
        return False

    # simple ones
    def cmd_global(self, toks):
        for t in toks[1:]:
            if t.kind == "word":
                self.frame.globals.add(t.low)
                self.globals.setdefault(t.low, "")

    def cmd_put(self, toks):
        p = Parser(self, toks, 1)
        val = p.expr(stop=("into", "after", "before"))
        if p.at_end():
            self.host.set_msg(val)
            return
        prep = p.next().low
        if p.peek_low() in ("menu",) or (p.peek_low() == "the" and False):
            p.next()
            name = p.expr(stop=("with",))
            msgs = None
            if p.peek_low() == "with":
                p.next()
                if p.peek_low() in ("menumsg", "menumsgs", "menumessage", "menumessages"):
                    p.next()
                msgs = p.expr()
            self.host.menu_put(name, val, prep, msgs)
            return
        ref = p.container()
        if p.peek_low() == "with" and isinstance(ref, Obj) and ref.t == "menu":
            p.next()
            if p.peek_low() in ("menumsg", "menumsgs", "menumessage", "menumessages"):
                p.next()
            self.host.menu_put(ref.a, val, prep, p.expr())
            return
        if isinstance(ref, Obj) and ref.t == "menuitem":
            msgs = None
            if p.peek_low() == "with":
                p.next()
                if p.peek_low() in ("menumsg", "menumsgs", "menumessage", "menumessages"):
                    p.next()
                msgs = p.expr()
            self.host.menu_item_put(ref.a, ref.b, val, prep, msgs)
            return
        self.put_into(ref, val, prep)

    def cmd_get(self, toks):
        self.set_var("it", Parser(self, toks, 1).expr())

    def cmd_set(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() == "the":
            p.next()
        words = []
        while not p.at_end() and p.peek_low() not in ("of", "to"):
            words.append(p.next().low)
        prop = " ".join(words)
        obj = None
        if p.peek_low() == "of":
            p.next()
            p.stop = ("to",)
            obj = p.prop_target()
        p.expect("to")
        val = p.expr()
        self.set_prop(prop, obj, val)

    def _arith(self, toks, op, word):
        p = Parser(self, toks, 1)
        v = to_num(p.expr(stop=(word,)))
        p.expect(word)
        ref = p.container()
        cur = to_num(self.value_of(ref) or "0")
        if op == "+":
            r = cur + v
        elif op == "-":
            r = cur - v
        elif op == "*":
            r = cur * v
        else:
            if v == 0:
                raise HTError("Division by zero")
            r = cur / v
        self.put_into(ref, fmt_num(r))

    def cmd_add(self, toks):
        self._arith(toks, "+", "to")

    def cmd_subtract(self, toks):
        self._arith(toks, "-", "from")

    def cmd_multiply(self, toks):
        p = Parser(self, toks, 1)
        ref = p.container()
        p.expect("by")
        v = to_num(p.expr())
        self.put_into(ref, fmt_num(to_num(self.value_of(ref) or "0") * v))

    def cmd_divide(self, toks):
        p = Parser(self, toks, 1)
        ref = p.container()
        p.expect("by")
        v = to_num(p.expr())
        if v == 0:
            raise HTError("Division by zero")
        self.put_into(ref, fmt_num(to_num(self.value_of(ref) or "0") / v))

    def cmd_return(self, toks):
        v = Parser(self, toks, 1).expr() if len(toks) > 1 else ""
        if self.frame:
            self.frame.result = v
        raise _Return(v)

    def cmd_exit(self, toks):
        w = toks[1].low if len(toks) > 1 else ""
        if w == "repeat":
            raise _Exit("repeat")
        if w == "to":
            raise _Exit("hypercard")
        raise _Exit("handler", w)

    def cmd_next(self, toks):
        if len(toks) > 1 and toks[1].low == "repeat":
            raise _Exit("next")

    def cmd_pass(self, toks):
        raise _Exit("pass")

    def cmd_send(self, toks):
        p = Parser(self, toks, 1)
        msg = p.expr(stop=("to",))
        target = None
        if p.peek_low() == "to":
            p.next()
            if p.peek_low() == "hypercard":
                # straight to HyperCard: a built-in command runs as written, without the scripts
                mtoks = tokenize(msg)
                f = getattr(self, "cmd_" + mtoks[0].low, None) \
                    if mtoks and mtoks[0].kind == "word" and mtoks[0].low != "domenu" else None
                if f is not None:
                    f(mtoks)
                    return
                self.builtin_message(msg.split()[0].lower() if msg.split() else "", self._msg_params(msg))
                return
            target = p.object_ref()
        words = msg.split(None, 1)
        if not words:
            return
        if target is not None and target.t == "window":
            self.host.window_message(target.a, words[0], self._msg_params(msg))
            return
        params = self._msg_params(msg)
        self.depth += 1
        try:
            self.send(words[0], target, params, set_target=target is not None)
        finally:
            self.depth -= 1

    def _msg_params(self, msg):
        words = msg.split(None, 1)
        if len(words) < 2:
            return []
        try:
            return Parser(self, tokenize(words[1]), 0).arg_list()
        except HTError:
            return [words[1]]

    def do_text(self, src):
        """Run what was typed in the message box: a statement, or an
        expression whose value goes back into the message box."""
        toks = tokenize(src)
        if toks and len(src.split("\n")) == 1:
            w = toks[0].low
            me = self.frame.me if self.frame else None
            is_cmd = toks[0].kind == "word" and (hasattr(self, "cmd_" + w) or self.handles(w, me) or
                                                  self.xcmds.command(w) is not None or
                                                  w in ("if", "repeat", "global"))
            if not is_cmd:
                try:
                    v = Parser(self, toks, 0).expr()
                except HTError:
                    v = None
                if v is not None:
                    self.host.set_msg(v)
                    return
        sc = Script("on __do__\n%s\nend __do__" % src)
        h = sc.handlers.get("__do__")
        if h:
            self.call(h, None, [], "do")

    def cmd_do(self, toks):
        src = Parser(self, toks, 1).expr()
        sc = Script("on __do__\n%s\nend __do__" % src)
        h = sc.handlers.get("__do__")
        if h:
            saved_vars = self.frame
            self.run_block(h.body) if saved_vars else self.call(h, None, [], "do")

    def cmd_go(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() == "to":
            p.next()
        self.host.go_dest(p, self.effect)
        self.effect = None

    def cmd_visual(self, toks):
        words = [t.low for t in toks[1:] if t.low != "effect"]
        text = " ".join(words)
        names = ("dissolve", "wipe", "scroll", "iris", "barn door", "zoom", "venetian blinds",
                 "checkerboard", "shrink", "stretch", "push", "plain", "cut")
        for n in names:
            if text.startswith(n):
                rest = text[len(n):]
                direction = next((d for d in ("left", "right", "up", "down", "open", "close", "in", "out")
                                  if re.search(r"\b%s\b" % d, rest)), "")
                speed = "normal"
                for sp in ("very slowly", "very slow", "slowly", "slow", "very fast", "fast"):
                    if sp in rest:
                        speed = sp.replace("ly", "")
                        break
                self.effect = (n, direction, speed)
                return
        self.effect = ("dissolve", "", "normal")


    def cmd_play(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() == "stop":
            self.host.stop_sound()
            return
        name = p.expr(stop=("tempo",))
        self.host.play_sound(name)

    def cmd_beep(self, toks):
        n = int(to_num(self.eval_toks(toks[1:]))) if len(toks) > 1 else 1
        for _ in range(min(n, 5)):
            self.host.beep()

    def cmd_answer(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() in ("file", "folder", "program"):
            kind = p.next().low
            prompt = p.expr(stop=("of",)) if not p.at_end() else ""
            r = self.host.choose_file(kind, prompt)
            self.set_var("it", r or "")
            self.result = "" if r else "Cancel"
            return
        text = p.expr(stop=("with",))
        buttons = []
        if p.peek_low() == "with":
            p.next()
            buttons.append(p.expr(stop=("or",)))
            while p.peek_low() == "or":
                p.next()
                buttons.append(p.expr(stop=("or",)))
        r = self.host.answer(text, buttons or ["OK"])
        self.set_var("it", r)

    def cmd_ask(self, toks):
        p = Parser(self, toks, 1)
        pw = False
        if p.peek_low() in ("password",):
            p.next()
            pw = True
        if p.peek_low() == "file":
            p.next()
            prompt = p.expr(stop=("with",))
            r = self.host.choose_file("save", prompt)
            self.set_var("it", r or "")
            self.result = "" if r else "Cancel"
            return
        prompt = p.expr(stop=("with",))
        default = ""
        if p.peek_low() == "with":
            p.next()
            default = p.expr()
        r = self.host.ask(prompt, default, pw)
        if r is None:
            self.set_var("it", "")
            self.result = "Cancel"
        else:
            self.set_var("it", r)
            self.result = ""

    def _show_hide(self, toks, show):
        p = Parser(self, toks, 1)
        low = p.peek_low()
        rest = " ".join(t.low for t in toks[1:])
        if low in ("menubar", "msg", "message", "tool", "pattern", "titlebar", "groups", "nav",
                   "scroll", "all", "cards", "marked", "messagewatcher", "variablewatcher"):
            if low in ("msg", "message"):
                self.host.show_msg(show)
            return
        if "picture" in rest or "pict" in rest:
            layer = self.host.bg_of(self.host.card) if re.match(r"(bg|bkgnd|background)\b", rest) else self.host.card
            if layer is not None:
                layer.show_pict = show
                self.host.changed(None)
            return
        if low == "window":
            p.next()
            self.host.window_visible(p.expr(), show)
            return
        o = p.object_ref(stop=("at",))
        if o is not None and o.t == "part":
            o.b.visible = show
            if p.peek_low() == "at":
                p.next()
                v = [int(round(to_num(x))) for x in p.expr().split(",")[:2]]
                if len(v) == 2:
                    w, hh = o.b.width, o.b.height
                    o.b.left, o.b.top = v[0] - w // 2, v[1] - hh // 2
                    o.b.right, o.b.bottom = o.b.left + w, o.b.top + hh
            self.host.changed(o)
        elif o is not None and o.t == "window":
            self.host.window_visible(o.a, show)

    def cmd_show(self, toks):
        self._show_hide(toks, True)

    def cmd_hide(self, toks):
        self._show_hide(toks, False)

    def cmd_lock(self, toks):
        if len(toks) > 1 and toks[1].low == "screen":
            self.props["lockscreen"] = TRUE
            self.host.lock_screen(True)

    def cmd_unlock(self, toks):
        if len(toks) > 1 and toks[1].low == "screen":
            p = Parser(self, toks, 2)
            if p.peek_low() == "with":
                p.next()
                self.cmd_visual([Tok("word", "visual")] + toks[p.i:])
            self.props["lockscreen"] = FALSE
            self.host.lock_screen(False, self.effect)
            self.effect = None

    def cmd_wait(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() in ("until", "while"):
            return
        if p.peek_low() == "for":
            p.next()
        n = to_num(p.expr(stop=("ticks", "tick", "seconds", "second", "secs", "sec")))
        unit = p.peek_low()
        secs = n / 60.0 if unit in ("", "ticks", "tick") else n
        self.host.wait(min(secs, 5.0))

    def cmd_push(self, toks):
        self.host.push_card()

    def cmd_pop(self, toks):
        self.host.pop_card(self.effect)
        self.effect = None

    def cmd_find(self, toks):
        p = Parser(self, toks, 1)
        mode = "normal"
        if p.peek_low() in ("whole", "string", "words", "chars") or \
                (p.peek_low() in ("word", "char") and not (p.peek(1) is not None and p.peek(1).kind == "num"
                                                          and p.peek_low(2) in ("of", "in", "to"))):
            mode = p.next().low
        text = p.expr(stop=("in", "of"))
        text, mode = self._find_mode_var(p, text, mode, ("in", "of"))
        field = None
        marked = False
        while not p.at_end():
            w = p.next().low
            if w in ("in", "of"):
                if p.peek_low() == "marked":
                    marked = True
                    break
                try:
                    o = p.object_ref()
                    if o is not None and o.t == "part":
                        field = o
                except HTError:
                    pass
        self.host.find(text, mode, field, marked)

    _FIND_MODES = ("whole", "string", "word", "words", "chars", "char", "normal")

    def _find_mode_var(self, p, text, mode, stops):
        """`find how theText` where the variable `how` holds the find form
        (or is empty): the first value is the form, the next one the text."""
        if mode == "normal" and not p.at_end() and p.peek_low() not in stops:
            m = str(text).strip().lower()
            if m == "" or m in self._FIND_MODES:
                return p.expr(stop=stops), (m or "normal")
        return text, mode

    def cmd_domenu(self, toks):
        p = Parser(self, toks, 1)
        name = p.expr(stop=("without",))
        handled, _ = self.send("doMenu", None, [name])
        if not handled:
            self.host.domenu(name)

    def cmd_choose(self, toks):
        return

    def cmd_select(self, toks):
        return

    def cmd_click(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() == "at":
            p.next()
            v = [int(round(to_num(x))) for x in p.expr(stop=("with",)).split(",")[:2]]
            if len(v) == 2:
                self.host.click_at(v[0], v[1])

    def cmd_type(self, toks):
        p = Parser(self, toks, 1)
        text = p.expr(stop=("with",))
        self.host.type_text(text)

    def cmd_reply(self, toks):
        return

    def cmd_request(self, toks):
        # AppleEvents to other programs don't exist on Linux
        self.set_var("it", "")
        self.result = "No reply"

    def cmd_copy(self, toks):
        return  # "copy template": report templates are not supported

    def cmd_delete(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() in ("menu",):
            p.next()
            self.host.menu_delete(p.expr())
            return
        if p.peek_low() in ("this", "card", "cd") and len(toks) <= 3:
            return  # deleting cards is not supported
        ref = p.container(menus=True)
        self.delete_ref(ref)

    def cmd_create(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() == "menu":
            p.next()
            self.host.menu_create(p.expr())

    def cmd_enable(self, toks):
        self._enable(toks, True)

    def cmd_disable(self, toks):
        self._enable(toks, False)

    def _enable(self, toks, on):
        p = Parser(self, toks, 1)
        o = p.object_ref()
        if o is None:
            return
        if o.t == "menuitem":
            self.host.set_menu_item_prop(o.a, o.b, "enabled", b(on))
        elif o.t == "menu":
            self.host.set_menu_prop(o.a, "enabled", b(on))
        elif o.t == "part":
            o.b.enabled = on
            self.host.changed(o)

    def cmd_mark(self, toks):
        self._mark(toks, True)

    def cmd_unmark(self, toks):
        self._mark(toks, False)

    def _mark(self, toks, on):
        rest = [t.low for t in toks[1:]]
        h = self.host
        if rest[:2] == ["all", "cards"] or rest[:2] == ["all", "cds"]:
            for c in h.stack.cards:
                c.marked = on
            return
        if len(rest) > 3 and rest[0] in ("cards", "cds") and rest[1] == "by" and rest[2] == "finding":
            p = Parser(self, toks, 4)
            mode = "normal"
            if p.peek_low() in ("whole", "string", "word", "words", "chars", "char"):
                mode = p.next().low
            text = p.expr(stop=("in",))
            text, mode = self._find_mode_var(p, text, mode, ("in",))
            for c in h.find_cards(text, mode):
                c.marked = on
            return
        if len(rest) > 2 and rest[0] in ("cards", "cds") and rest[1] == "where":
            cur = h.index
            for i, c in enumerate(h.stack.cards):
                h.index = i
                try:
                    if to_bool(Parser(self, toks, 3).expr()):
                        c.marked = on
                except HTError:
                    pass
            h.index = cur
            return
        p = Parser(self, toks, 1)
        o = p.object_ref()
        if o is not None and o.t == "card":
            o.a.marked = on

    def cmd_sort(self, toks):
        # sort [lines|items of] container [ascending|descending] [text|numeric]
        p = Parser(self, toks, 1)
        kind = "line"
        if p.peek_low() == "the" and p.peek_low(1) in ("cards", "cds", "marked"):
            p.next()
        if p.peek_low() in ("lines", "items", "line", "item"):
            kind = "item" if p.next().low.startswith("item") else "line"
            if p.peek_low() == "of":
                p.next()
        elif p.peek_low() in ("cards", "cds", "this", "stack", "marked", "ascending", "descending", "text",
                              "numeric", "international", "datetime", "by") or \
                (p.peek_low() in ("card", "cd", "bkgnd", "background", "bg") and
                 p.peek_low(1) not in ("field", "fld", "button", "btn")):
            return self._sort_cards(p)
        ref = p.container(stop=("ascending", "descending", "text", "numeric", "international", "datetime", "by"))
        desc = False
        numeric = False
        by = None
        while not p.at_end():
            w = p.next().low
            if w == "descending":
                desc = True
            elif w in ("numeric", "datetime"):
                numeric = True
            elif w == "by":
                by = p.toks[p.i:]
                break
        text = self.value_of(ref)
        sep = "\n" if kind == "line" else self.props["itemdelimiter"]
        trail = text.endswith(sep)
        parts = (text[:-len(sep)] if trail else text).split(sep)
        if by:
            keys = {}
            for k, x in enumerate(parts):
                self.set_var("each", x)
                try:
                    keys[k] = str(self.eval_toks(by))
                except HTError:
                    keys[k] = ""
            order = list(range(len(parts)))
        else:
            keys = dict(enumerate(parts))
            order = list(range(len(parts)))
        if numeric:
            # HyperCard puts lines that are not numbers before the numbers.
            order.sort(key=lambda k: (1, to_num(keys[k])) if is_number(keys[k]) else (0, 0), reverse=desc)
        else:
            order.sort(key=lambda k: keys[k].lower(), reverse=desc)
        parts = [parts[k] for k in order]
        self.put_into(ref, sep.join(parts) + (sep if trail else ""))

    def _sort_cards(self, p):
        """sort [marked] [cards] [of this stack | of bkgnd X] [ascending|descending] [type] by expr"""
        h = self.host
        st = h.stack
        if st is None or not st.cards:
            return
        marked = False
        bg = None
        while not p.at_end() and p.peek_low() not in ("ascending", "descending", "text", "numeric",
                                                     "international", "datetime", "by"):
            w = p.peek_low()
            if w == "marked":
                p.next()
                marked = True
            elif w in ("cards", "cds", "card", "cd", "of", "in", "this", "stack"):
                p.next()
            elif w in ("bkgnd", "background", "bg"):
                o = p.object_ref(stop=("ascending", "descending", "text", "numeric", "international",
                                       "datetime", "by"))
                if o is not None and o.t == "bg":
                    bg = o.a
            else:
                p.next()   # a stack name: only the current stack can be sorted
        desc = numeric = dt = False
        by = None
        while not p.at_end():
            w = p.next().low
            if w == "descending":
                desc = True
            elif w == "numeric":
                numeric = True
            elif w == "datetime":
                dt = True
            elif w == "by":
                by = p.toks[p.i:]
                break
        if not by:
            raise HTError("Expected “by” in sort")
        cur = h.card
        slots = [k for k, c in enumerate(st.cards)
                 if (not marked or c.marked) and (bg is None or c.bg_id == bg.id)]
        keys = {}
        saved = h.index
        try:
            for k in slots:
                h.index = k
                try:
                    keys[k] = str(self.eval_toks(by))
                except HTError:
                    keys[k] = ""
        finally:
            h.index = saved

        def key(k):
            v = keys[k]
            if numeric:
                return (1, to_num(v)) if is_number(v) else (0, 0)
            if dt:
                try:
                    return (1, _parse_date(v))
                except Exception:  # noqa: BLE001
                    return (0, 0)
            return v.lower()
        order = sorted(slots, key=key, reverse=desc)
        cards = list(st.cards)
        for slot, src in zip(slots, order):
            st.cards[slot] = cards[src]
        if cur in st.cards:
            h.index = st.cards.index(cur)
        h.changed(None)

    def cmd_start(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() == "using":
            p.next()
            if p.peek_low() == "stack":
                p.next()
            self.host.start_using(p.expr())

    def cmd_stop(self, toks):
        p = Parser(self, toks, 1)
        if p.peek_low() == "using":
            p.next()
            if p.peek_low() == "stack":
                p.next()
            self.host.stop_using(p.expr())

    def cmd_open(self, toks):
        p = Parser(self, toks, 1)
        w = p.peek_low()
        if w == "file":
            p.next()
            try:
                self.host.file_open(p.expr(stop=("for",)))
            except OSError:
                self.result = "Can't open that file."
        elif w in ("stack",):
            p.next()
            self.host.open_stack(p.expr(stop=("in",)), new_window=True)

    def cmd_close(self, toks):
        p = Parser(self, toks, 1)
        w = p.peek_low()
        if w == "file":
            p.next()
            self.host.file_close(p.expr())
        elif w == "window":
            p.next()
            self.host.window_close(p.expr())
        elif w in ("card", "cd") and p.peek_low(1) == "window":
            return

    def cmd_read(self, toks):
        p = Parser(self, toks, 1)
        p.expect("from")
        if p.peek_low() == "file":
            p.next()
        path = p.expr(stop=("until", "for", "at"))
        until = None
        count = None
        if p.peek_low() == "until":
            p.next()
            until = p.expr()
        elif p.peek_low() == "for":
            p.next()
            count = int(to_num(p.expr()))
        self.set_var("it", self.host.file_read(path, until, count))

    def cmd_write(self, toks):
        p = Parser(self, toks, 1)
        text = p.expr(stop=("to",))
        p.expect("to")
        if p.peek_low() == "file":
            p.next()
        try:
            self.host.file_write(p.expr(stop=("at",)), text)
        except OSError:
            self.result = "Can't write to that file."

    def cmd_print(self, toks):
        self.host.domenu("print card")

    def cmd_reset(self, toks):
        if len(toks) > 1 and toks[1].low == "menubar":
            self.host.menu_reset()

    def cmd_flash(self, toks):
        return

    def cmd_drag(self, toks):
        return

    def cmd_convert(self, toks):
        p = Parser(self, toks, 1)
        ref = p.container(stop=("to", "from"))
        while not p.at_end() and p.peek_low() != "to":
            p.next()
        if p.at_end():
            raise HTError("Expected \"to\"")
        p.next()
        fmt = " ".join(t.low for t in toks[p.i:])
        v = self.value_of(ref)
        try:
            secs = _parse_date(v)
        except (ValueError, OverflowError):
            self.result = "Invalid date"
            return
        out = _format_date(secs, fmt)
        if out is None:
            raise HTError("Unknown date format: " + fmt)
        self.result = ""
        if isinstance(ref, _Literal):
            self.set_var("it", out)
        else:
            self.put_into(ref, out)

    def cmd_save(self, toks):
        return

    def cmd_palette(self, toks):
        return

    def cmd_debug(self, toks):
        return

    def cmd_edit(self, toks):
        return

    def cmd_dial(self, toks):
        return

    def cmd_import(self, toks):
        return

    def cmd_export(self, toks):
        return

    def cmd_lockmessages(self, toks):
        return

    def cmd_move(self, toks):
        return

    def cmd_speak(self, toks):
        return


# ---------------------------------------------------------------------------
# Expression parser (evaluates as it parses)
# ---------------------------------------------------------------------------

_BIN_PREC = [
    ("or",),
    ("and",),
    ("=", "is", "<>", "≠", "is not"),
    ("<", ">", "<=", ">=", "≤", "≥", "contains", "is in", "is not in", "is a", "is an", "is not a",
     "is not an", "is within", "is not within"),
    ("&", "&&"),
    ("+", "-"),
    ("*", "/", "div", "mod"),
    ("^",),
]


_TYPE_WORDS = ("number", "integer", "logical", "point", "rect", "rectangle", "date", "empty")
# plural object words can't name a single object, so `put x into stacks` is a variable
_PLURAL_OBJ_WORDS = ("cards", "cds", "backgrounds", "bkgnds", "bgs", "buttons", "btns", "fields", "flds",
                     "stacks", "parts")


class Parser:
    def __init__(self, interp, toks, start=0):
        self.it = interp
        self.toks = toks
        self.i = start
        self.stop = ()

    # token helpers
    def at_end(self):
        return self.i >= len(self.toks)

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def peek_low(self, k=0):
        t = self.peek(k)
        return t.low if t is not None else ""

    def next(self):
        t = self.peek()
        if t is None:
            raise HTError("Unexpected end of line")
        self.i += 1
        return t

    def expect(self, word):
        t = self.next()
        if t.low != word:
            raise HTError("Expected “%s” here, found “%s”" % (word, t.val))

    # expressions ------------------------------------------------------------
    def expr(self, stop=()):
        self.stop = tuple(stop)
        return self._bin(0)

    def _match_op(self, level):
        t = self.peek()
        if t is None or t.low in self.stop:
            return None
        ops = _BIN_PREC[level]
        if t.kind == "op":
            if t.val in ops:
                return t.val, 1
            return None
        w = t.low
        if w == "is":
            n1 = self.peek_low(1)
            n2 = self.peek_low(2)
            for cand in ("is not within", "is not in", "is not an", "is not a", "is within", "is in",
                         "is an", "is a", "is not"):
                parts = cand.split()
                if [w, n1, n2][:len(parts)] == parts and cand in ops:
                    # "if x is a then": `a` is a variable unless a type name follows
                    if parts[-1] in ("a", "an") and self.peek_low(len(parts)) not in _TYPE_WORDS:
                        continue
                    return cand, len(parts)
            if "is" in ops:
                return "is", 1
            return None
        if w in ops:
            return w, 1
        if w == "contains" and "contains" in ops:
            return w, 1
        return None

    def _bin(self, level):
        if level >= len(_BIN_PREC):
            return self.unary()
        left = self._bin(level + 1)
        while True:
            m = self._match_op(level)
            if not m:
                return left
            op, n = m
            self.i += n
            if op in ("is a", "is an", "is not a", "is not an"):
                typ = self.next().low
                r = _is_type(left, typ)
                left = b(r if "not" not in op else not r)
                continue
            right = self._bin(level + 1)
            left = self.apply(op, left, right)

    def apply(self, op, a, c):
        try:
            return self._apply(op, a, c)
        except OverflowError:
            return "INF"
        except ValueError:
            # fmod(INF, 2) and the like: HyperCard answers NAN instead of stopping
            return "NAN"

    def _apply(self, op, a, c):
        if op in ("or", "and"):
            x = to_bool(a)
            y = to_bool(c)
            return b(x or y) if op == "or" else b(x and y)
        if op in ("=", "is"):
            return b(_eq(a, c))
        if op in ("<>", "≠", "is not"):
            return b(not _eq(a, c))
        if op in ("<", ">", "<=", ">=", "≤", "≥"):
            if is_number(a) and is_number(c):
                x, y = to_num(a), to_num(c)
            else:
                x, y = str(a).lower(), str(c).lower()
            r = {"<": x < y, ">": x > y, "<=": x <= y, "≤": x <= y, ">=": x >= y, "≥": x >= y}[op]
            return b(r)
        if op == "contains":
            return b(str(c).lower() in str(a).lower())
        if op == "is in":
            return b(str(a).lower() in str(c).lower())
        if op == "is not in":
            return b(str(a).lower() not in str(c).lower())
        if op in ("is within", "is not within"):
            try:
                x, y = [to_num(v) for v in a.split(",")[:2]]
                l, t, r, bb = [to_num(v) for v in c.split(",")[:4]]
                ok = l <= x < r and t <= y < bb
            except (HTError, ValueError):
                ok = False
            return b(ok if op == "is within" else not ok)
        if op == "&":
            return str(a) + str(c)
        if op == "&&":
            return str(a) + " " + str(c)
        x, y = to_num(a), to_num(c)
        if op == "+":
            return fmt_num(x + y)
        if op == "-":
            return fmt_num(x - y)
        if op == "*":
            return fmt_num(x * y)
        if op == "/":
            if y == 0:
                return "INF"
            return fmt_num(x / y)
        if op == "div":
            if y == 0:
                return "INF"
            return fmt_num(int(x / y))
        if op == "mod":
            if y == 0:
                return "INF"
            return fmt_num(math.fmod(x, y))
        if op == "^":
            try:
                r = x ** y
            except (OverflowError, ZeroDivisionError):
                return "INF"
            if isinstance(r, complex):
                return "NAN"
            return fmt_num(r)
        raise HTError("Unknown operator " + op)

    def unary(self):
        t = self.peek()
        if t is None:
            raise HTError("Missing value")
        if t.kind == "op" and t.val == "-":
            self.i += 1
            return fmt_num(-to_num(self.unary()))
        if t.kind == "op" and t.val == "+":
            self.i += 1
            return self.unary()
        if t.low == "not":
            self.i += 1
            return b(not to_bool(self.unary()))
        if t.low == "there":
            return self.there_is()
        return self.factor()

    def there_is(self):
        self.next()
        self.expect("is")
        neg = False
        if self.peek_low() in ("not", "no"):
            neg = self.next().low in ("not", "no")
        if self.peek_low() in ("a", "an"):
            self.next()
        try:
            o = self.object_ref(must_exist=False)
            ok = o is not None and o.t != "missing"
        except HTError:
            ok = False
        return b(ok != neg)

    def factor(self):
        it = self.it
        t = self.next()
        k, low = t.kind, t.low
        if k == "str":
            return t.val
        if k == "num":
            return t.val
        if k == "op" and t.val == "(":
            saved = self.stop
            v = self.expr()
            self.stop = saved
            if self.peek() is not None and self.peek().val == ")":
                self.i += 1
            return v
        if k == "op":
            raise HTError("Unexpected “%s”" % t.val)
        # the …
        if low == "the":
            return self.the_form()
        # function call
        nt = self.peek()
        if nt is not None and nt.kind == "op" and nt.val == "(" and low not in CHUNKS and \
                (low not in OBJ_WORDS or it.has_function(low)):
            self.i += 1
            args = self.paren_args()
            return self.call_function(t.val, args)
        # property without "the": "short id of this cd", "textsize of char 1 of fld 2"
        if self.peek_low() == "of" and it.get_var(low) is None and \
                (low in PROP_WORDS or low in _FUNCS_OF or
                 (t.kind == "word" and self.peek_low(1) in ("window", "wd", "menu", "menuitem"))):
            self.next()
            return self.prop_of(low)
        if low in ("short", "long", "abbr", "abbrev", "abbreviated") and self.peek_low() in ("name", "id") and \
                self.peek_low(1) == "of":
            name = low + " " + self.next().low
            self.next()
            return self.prop_of(name if not name.startswith("abbr") else "name")
        if low in CHUNKS or (low in ORDINALS and self.peek_low() in CHUNKS):
            self.i -= 1
            ref = self.chunk_ref()
            return it.value_of(ref)
        if low in ORDINALS and self.peek_low() in OBJ_WORDS:
            self.i -= 1
            o = self.object_ref()
            return it.value_of(o)
        if low == "number" and self.peek_low() == "of":
            self.next()
            return self.number_of()
        if (low in OBJ_WORDS and (low not in _PLURAL_OBJ_WORDS or it.get_var(low) is None)) or \
                low in ("me", "menu", "menuitem", "window", "target") or \
                (low in ("next", "prev", "previous", "this") and self.peek_low() in OBJ_WORDS):
            self.i -= 1
            o = self.object_ref()
            if o is None:
                return ""
            return it.value_of(o)
        if low in ("msg", "message") or low == "message box":
            if self.peek_low() == "box" or self.peek_low() == "window":
                self.i += 1
            return it.host.msg_text()
        if low == "selection":
            return it.host.selection_text()
        if low in CONSTANTS:
            v = it.get_var(low) if low not in ("empty", "return", "true", "false", "space", "tab", "quote",
                                               "comma", "colon") else None
            return v if v is not None else CONSTANTS[low]
        if low == "it":
            v = it.get_var("it")
            return v or ""
        if low in ("param", "params", "paramcount", "result", "target"):
            return self.no_arg_function(low)
        v = it.get_var(low)
        if v is not None:
            return v
        # a known built-in that can be written without "the" and parentheses
        if low in _NOARG:
            return self.no_arg_function(low)
        # unquoted literal: HyperTalk evaluates an unknown word to itself
        return t.val

    def paren_args(self):
        args = []
        if self.peek() is not None and self.peek().val == ")":
            self.i += 1
            return args
        saved = self.stop
        while True:
            self.stop = ()
            args.append(self._arg_expr())
            t = self.peek()
            if t is None:
                break
            if t.val == ",":
                self.i += 1
                continue
            if t.val == ")":
                self.i += 1
                break
            break
        self.stop = saved
        return args

    def _arg_expr(self):
        # an expression ending at a top-level comma or closing paren
        start = self.i
        depth = 0
        j = start
        while j < len(self.toks):
            t = self.toks[j]
            if t.kind == "op" and t.val == "(":
                depth += 1
            elif t.kind == "op" and t.val == ")":
                if depth == 0:
                    break
                depth -= 1
            elif t.kind == "op" and t.val == "," and depth == 0:
                break
            j += 1
        sub = Parser(self.it, self.toks[start:j], 0)
        v = sub.expr() if j > start else ""
        self.i = j
        return v

    def arg_list(self):
        """Command parameters: comma-separated expressions to the end of the line."""
        args = []
        while not self.at_end():
            args.append(self._arg_expr())
            if self.peek() is not None and self.peek().val == ",":
                self.i += 1
                if self.at_end():
                    args.append("")
            else:
                break
        return args

    # the … ------------------------------------------------------------------
    def the_form(self):
        it = self.it
        words = []
        # gather the property/function name (up to 3 words), stopping at "of"/"in"
        while not self.at_end():
            t = self.peek()
            if t.kind != "word" or t.low in ("of", "in") or t.low in self.stop or t.low in _EXPR_STOP:
                break
            if words and (" ".join(words) in _THE_NOARG or " ".join(words) in _THE_PROPS_NO_OBJ) and \
                    (" ".join(words + [t.low]) not in _THE_NOARG and
                     " ".join(words + [t.low]) not in _THE_PROPS_NO_OBJ and
                     " ".join(words + [t.low]) not in _MULTIWORD_PROPS):
                break
            if t.low in CHUNKS or (t.low in ORDINALS and self.peek_low(1) in tuple(CHUNKS) + tuple(OBJ_WORDS)):
                if not words:
                    if t.low in ORDINALS and self.peek_low(1) in OBJ_WORDS:
                        # "the last card field", "the first card"
                        o = self.object_ref()
                        return it.value_of(o) if o is not None else ""
                    ref = self.chunk_ref()
                    return it.value_of(ref)
                break
            if t.low in OBJ_WORDS and not words:
                break
            words.append(t.low)
            self.i += 1
            if len(words) >= 3:
                break
        name = " ".join(words)
        if not words:
            # "the card field 3" etc.
            if self.peek_low() in OBJ_WORDS:
                o = self.object_ref()
                return it.value_of(o) if o is not None else ""
            raise HTError("Expected a property after “the”")
        if name == "number" and self.peek_low() == "of":
            self.next()
            return self.number_of()
        if self.peek_low() == "of" and self.peek_low(1) == "hypercard":
            self.i += 2
            return self.no_arg_function(name)
        if self.peek_low() == "of" and name not in _THE_NOARG_STRICT:
            self.next()
            return self.prop_of(name)
        if name in it.props:
            return it.props[name]
        return self.no_arg_function(name)

    def prop_of(self, name):
        """After `<prop> of`: a property of an object or chunk, or a
        one-argument function written as `the f of x`."""
        it = self.it
        if self.peek_low() == "hypercard":
            self.next()
            return self.no_arg_function(name)
        if name == "number":
            return self.number_of()
        fn = _FUNCS_OF.get(name)
        if fn is not None and not self._looks_like_object():
            return self.call_function(name, [self.unary_of()])
        o = self.prop_target()
        if o is None:
            return self.call_function(name, [""]) if fn else ""
        if fn is not None:
            return self.call_function(name, [it.value_of(o)])
        return it.get_prop(name, o)

    def prop_target(self):
        """The thing after `of` in a property reference: an object, a chunk
        of a field, or an expression whose value describes one (scripts
        often keep "char 3 to 9 of bg field 2" in a variable)."""
        it = self.it
        w = self.peek_low()
        if w in CHUNKS or (w in ORDINALS and self.peek_low(1) in CHUNKS) or \
                (w == "the" and self.peek_low(1) in CHUNKS):
            if w == "the":
                self.next()
            return self.chunk_ref()
        if self._looks_like_object() or w in ("the", "next", "prev", "previous", "msg", "message"):
            save = self.i
            try:
                o = self.object_ref(stop=self.stop)
                if o is not None:
                    return o
            except HTError:
                pass
            self.i = save
        v = self.unary()
        return parse_ref(it, v)
    def _looks_like_object(self):
        w = self.peek_low()
        return w in OBJ_WORDS or w in ("me", "target", "menu", "menuitem", "window") or \
            (w == "this" and self.peek_low(1) in OBJ_WORDS) or \
            (w in ORDINALS and self.peek_low(1) in OBJ_WORDS) or \
            (w == "the" and self.peek_low(1) in ("target",) + tuple(OBJ_WORDS))

    def unary_of(self):
        # the argument of "the f of x": a factor, but chunks extend ("the length of line 2 of x")
        return self.unary()

    def no_arg_function(self, name):
        it = self.it
        h = it.host
        n = name
        if n in ("result",):
            return it.result
        if n in ("target", "short target", "long target"):
            o = it.target
            if o is None:
                return ""
            form = "short" if n.startswith("short") else ("long" if n.startswith("long") else "abbr")
            return it.obj_name(o, form) if form != "abbr" or o.t != "part" else it.obj_name(o, "abbr")
        if n in ("param",):
            return ""
        if n == "paramcount":
            return str(len(it.frame.params)) if it.frame else "0"
        if n == "params":
            f = it.frame
            if not f:
                return ""
            return f.msg + (" " + ",".join('"%s"' % p for p in f.params) if f.params else "")
        if n in ("date", "short date"):
            return time.strftime("%-m/%-d/%y")
        if n in ("long date",):
            return time.strftime("%A, %B %-d, %Y")
        if n in ("abbr date", "abbreviated date", "abbrev date"):
            return time.strftime("%a, %b %-d, %Y")
        if n in ("time", "short time"):
            return time.strftime("%-I:%M %p")
        if n in ("long time",):
            return time.strftime("%-I:%M:%S %p")
        if n in ("seconds", "secs"):
            return str(int(time.time()) + 2082844800)
        if n == "ticks":
            return str(int(time.monotonic() * 60))
        if n in ("version", "short version"):
            return "2.41"
        if n == "long version":
            return "02418000"
        if n in ("systemversion",):
            return "7.55"
        if n == "platform":
            return "MacOS"
        if n in ("diskspace", "heapspace", "stackspace", "freesize"):
            return "100000000"
        if n == "screenrect":
            return h.screen_rect()
        if n in ("mouse",):
            return "down" if getattr(h, "mouse_is_down", lambda: False)() else "up"
        if n in ("mouseh", "mousev", "mouseloc", "clickh", "clickv", "clickloc"):
            x, y = h.mouse_loc() if "mouse" in n else h.click_loc()
            return {"mouseh": str(x), "mousev": str(y), "clickh": str(x), "clickv": str(y)}.get(n, "%d,%d" % (x, y))
        if n in ("mouseclick",):
            return b(h.mouse_clicked())
        if n in ("commandkey", "cmdkey", "optionkey", "shiftkey", "controlkey", "capslockkey"):
            return "up"
        if n in ("sound",):
            return h.sound_state()
        if n in ("tool",):
            return "browse tool"
        if n in ("stacksinuse",):
            return "\n".join(h.stack_path(s) for s in h.stacks_in_use())
        if n in ("menus",):
            return "\n".join(h.menu_names())
        if n in ("windows",):
            return "card window\nmessage window"
        if n in ("selectedtext", "selection"):
            return h.selection_text()
        if n in ("selectedline", "selectedchunk", "selectedfield", "selectedloc"):
            return ""
        if n in ("clicktext",):
            return h.click_text()
        if n in ("clickline", "clickchunk"):
            return h.click_chunk(n)
        if n in ("foundtext", "foundline", "foundchunk", "foundfield"):
            return h.found(n)
        if n in ("random",):
            return str(_random.randint(1, 100))
        if n in ("number",):
            return "0"
        if n in it.props:
            return it.props[n]
        if n in ("address",):
            return "HyperCard"
        if n in ("environment",):
            return "development"
        if n in ("suspended",):
            return FALSE
        if n in ("menubar",):
            return "true"
        if n in ("destination", "tempo"):
            return ""
        if n in ("long name", "name", "short name"):
            return ""
        return ""

    def number_of(self):
        """after `number of`: cards/bgs/buttons/fields/marked cards/chunks."""
        it = self.it
        h = it.host
        w = self.peek_low()
        if w in ("marked",):
            self.next()
            self.next()
            return str(sum(1 for c in h.stack.cards if c.marked))
        if w in CHUNKS:
            ctype = CHUNKS[self.next().low]
            self.expect_in_or_of()
            val = self.unary()
            return str(count_chunks(val, ctype, it.props["itemdelimiter"]))
        if w in ("menus",):
            self.next()
            return str(len(h.menu_names()))
        if w in ("menuitems",):
            self.next()
            self.expect_in_or_of()
            o = self.object_ref()
            return str(len(h.menu_items(o.a))) if o else "0"
        if w in ("windows",):
            self.next()
            return "2"
        if w in OBJ_WORDS:
            start = self.i
            word = self.next().low
            typ = OBJ_WORDS[word]
            layer_kind = None
            if typ in ("card", "bg") and self.peek_low() in ("button", "buttons", "btn", "btns",
                                                             "field", "fields", "fld", "flds", "part", "parts"):
                layer_kind = "cd" if typ == "card" else "bg"
                word = self.next().low
                typ = OBJ_WORDS[word]
            if word in _SINGULAR_OBJ_WORDS and typ != "stack":
                # "the number of card button 3": the number of one object
                self.i = start
                o = self.object_ref()
                return str(it.obj_number(o)) if o is not None else "0"
            if typ == "card":
                if self.peek_low() in ("in", "of"):
                    self.next()
                    o = self.object_ref()
                    if o is not None and o.t == "bg":
                        return str(sum(1 for c in h.stack.cards if c.bg_id == o.a.id))
                return str(len(h.stack.cards))
            if typ == "bg":
                if self.peek_low() in ("in", "of"):
                    self.next()
                    self.object_ref()
                return str(len(h.stack.backgrounds))
            if typ in ("button", "field", "part"):
                if layer_kind is None:
                    layer_kind = "cd" if typ == "button" else "bg"
                layer = h.card if layer_kind == "cd" else h.bg_of(h.card)
                if self.peek_low() in ("in", "of"):
                    self.next()
                    o = self.object_ref()
                    if o is not None and o.t in ("card", "bg"):
                        layer = o.a
                if layer is None:
                    return "0"
                return str(sum(1 for p in layer.parts if typ == "part" or p.type == typ))
            if typ == "stack":
                return "1"
        # number of <object>
        o = self.object_ref()
        return str(it.obj_number(o)) if o is not None else "0"

    def expect_in_or_of(self):
        if self.peek_low() in ("in", "of"):
            self.next()

    # functions -----------------------------------------------------------------
    def call_function(self, name, args):
        it = self.it
        n = name.lower()
        f = _BUILTIN.get(n)
        # user-defined functions first (HyperTalk lets scripts override)
        handled, val = it.send(name, it.frame.me if it.frame else None, args, is_function=True)
        if handled:
            return val if val is not None else ""
        if f is not None:
            return f(it, args)
        if n in _THE_NOARG and not args or (n in _NOARG and all(a == "" for a in args)):
            return self.no_arg_function(n)
        x = it.xcmds.function(n)
        if x is not None:
            try:
                r = x(it, args)
            except Exception as e:  # noqa: BLE001
                it.host.log("XFCN %s: %s" % (name, e))
                r = ""
            return "" if r is None else str(r)
        if n in ("param",):
            k = int(to_num(args[0])) if args else 0
            f = it.frame
            if not f:
                return ""
            if k == 0:
                return f.msg
            return f.params[k - 1] if 0 < k <= len(f.params) else ""
        it.host.log("unknown function: " + name)
        return ""

    # chunks and containers -------------------------------------------------------------
    def chunk_ref(self):
        """[ordinal] chunk [expr [to expr]] of <container>"""
        t = self.next()
        if t.low in ORDINALS:
            idx = ORDINALS[t.low]
            ctype = CHUNKS[self.next().low]
            a, z = idx, None
        else:
            ctype = CHUNKS[t.low]
            saved = self.stop
            a = self._index_expr()
            z = None
            if self.peek_low() in ("to", "thru", "through"):
                self.next()
                z = self._index_expr()
            self.stop = saved
        if self.peek_low() not in ("of", "in"):
            raise HTError("Expected “of” after chunk")
        self.next()
        if self.peek_low() in CHUNKS or (self.peek_low() in ORDINALS and self.peek_low(1) in CHUNKS):
            base = self.chunk_ref()
        else:
            base = self.container_simple()
        return ChunkRef(ctype, a, z, base)

    def _index_expr(self):
        w = self.peek_low()
        if w in ORDINALS and w not in ("first",) or w == "last":
            self.next()
            return ORDINALS[w]
        if w == "middle" or w == "mid" or w == "any":
            self.next()
            return ORDINALS[w]
        sub_stop = ("to", "thru", "through", "of", "in")
        saved = self.stop
        self.stop = sub_stop
        v = self._bin(4)  # arithmetic level and above
        self.stop = saved
        return int(to_num(v))

    def container_simple(self):
        """A container that can follow `of` in a chunk: variable, field, msg, it, selection."""
        w = self.peek_low()
        if w in ("msg", "message"):
            self.next()
            if self.peek_low() in ("box", "window"):
                self.next()
            return MsgRef()
        if w == "selection" or (w == "the" and self.peek_low(1) == "selection"):
            self.i += 1 if w == "selection" else 2
            return SelRef()
        if (w in OBJ_WORDS and w not in _PLURAL_OBJ_WORDS) or w in ("me", "target") or \
                (w in ORDINALS and self.peek_low(1) in OBJ_WORDS) or \
                (w == "the" and self.peek_low(1) == "target") or \
                (w == "the" and self.peek_low(1) in ORDINALS and self.peek_low(2) in OBJ_WORDS):
            o = self.object_ref()
            if o is None:
                raise HTError("No such object")
            return o
        if w == "(":
            # parenthesised expression used as a container: treat as value
            v = self.factor()
            return _Literal(v)
        t = self.next()
        if t.kind == "str":
            return _Literal(t.val)
        if t.kind == "num":
            return _Literal(t.val)
        if t.low == "the":
            self.i -= 1
            return _Literal(self.factor())
        nt = self.peek()
        if nt is not None and nt.kind == "op" and nt.val == "(":
            self.i += 1
            return _Literal(self.call_function(t.val, self.paren_args()))
        # "item 2 of loc of me": a property written without "the"
        if self.peek_low() == "of" and self.it.get_var(t.low) is None and \
                (t.low in PROP_WORDS or t.low in _FUNCS_OF or
                 t.low in ("short", "long", "abbr", "abbrev", "abbreviated")):
            self.i -= 1
            return _Literal(self.factor())
        if t.low in CONSTANTS and self.it.get_var(t.low) is None:
            return _Literal(CONSTANTS[t.low])
        return VarRef(t.low)

    def container(self, stop=(), menus=False):
        w = self.peek_low()
        self.stop = tuple(stop)
        if w in CHUNKS or (w in ORDINALS and self.peek_low(1) in CHUNKS):
            return self.chunk_ref()
        if w == "the" and (self.peek_low(1) in CHUNKS or
                           (self.peek_low(1) in ORDINALS and self.peek_low(2) in CHUNKS)):
            self.next()
            return self.chunk_ref()
        if w in ("menu", "menuitem"):
            return self.object_ref()
        return self.container_simple()

    # objects ------------------------------------------------------------------------------
    def object_ref(self, stop=(), must_exist=True):
        it = self.it
        h = it.host
        w = self.peek_low()
        if w == "the":
            self.next()
            w = self.peek_low()
        if w == "me":
            self.next()
            return it.frame.me if it.frame else None
        if w == "target":
            self.next()
            return it.target
        if w == "this":
            self.next()
            w = self.next().low
            typ = OBJ_WORDS.get(w)
            if typ == "card":
                return Obj("card", h.card)
            if typ == "bg":
                return Obj("bg", h.bg_of(h.card))
            if typ == "stack":
                return Obj("stack", h.stack)
            return None
        if w in ("next", "prev", "previous"):
            self.next()
            typ = OBJ_WORDS.get(self.next().low)
            if typ == "card":
                cards = h.stack.cards
                i = cards.index(h.card) if h.card in cards else 0
                return Obj("card", cards[(i + (1 if w == "next" else -1)) % len(cards)])
            if typ == "bg":
                bgs = h.stack.backgrounds
                cur = h.bg_of(h.card)
                i = bgs.index(cur) if cur in bgs else 0
                return Obj("bg", bgs[(i + (1 if w == "next" else -1)) % len(bgs)])
            return None
        if w == "menu":
            self.next()
            name = self._name_expr(stop)
            if not h.has_menu(name) and not must_exist:
                return Obj("missing")
            return Obj("menu", name)
        if w == "menuitem":
            self.next()
            item = self._name_expr(("of", "in") + tuple(stop))
            if self.peek_low() in ("of", "in"):
                self.next()
            if self.peek_low() == "menu":
                self.next()
            menu = self._name_expr(stop)
            if not must_exist and not h.has_menu_item(menu, item):
                return Obj("missing")
            return Obj("menuitem", menu, item)
        if w == "window":
            self.next()
            name = self._name_expr(stop)
            if not must_exist and not h.has_window(name):
                return Obj("missing")
            return Obj("window", name)
        if w in ("msg", "message"):
            self.next()
            if self.peek_low() in ("box", "window"):
                self.next()
            return Obj("window", "message")
        if w in ("card", "cd") and self.peek_low(1) == "window":
            self.next()
            self.next()
            return Obj("window", "card")
        ordinal = None
        if w in ORDINALS:
            ordinal = ORDINALS[self.next().low]
            w = self.peek_low()
            if w == "menuitem":
                # `the last menuItem of menu "Edit"`
                self.next()
                if self.peek_low() in ("of", "in"):
                    self.next()
                if self.peek_low() == "menu":
                    self.next()
                menu = self._name_expr(stop)
                m = h._menu(menu) if hasattr(h, "_menu") else None
                n = len(m[1]) if m else 0
                k = {-1: n, "mid": (n + 1) // 2, "any": _random.randint(1, max(1, n))}.get(ordinal, ordinal)
                return Obj("menuitem", menu, str(k))
        layer_word = None
        if w in LAYER_WORDS and self.peek_low(1) in ("button", "btn", "field", "fld", "part", "buttons",
                                                      "fields"):
            layer_word = LAYER_WORDS[self.next().low]
            w = self.peek_low()
        typ = OBJ_WORDS.get(w)
        if typ is None:
            if ordinal is not None:
                raise HTError("Expected an object")
            return None
        self.next()
        if typ == "stack":
            name = self._name_expr(stop) if not self._ref_end(stop) else ""
            if not name:
                return Obj("stack", h.stack)
            st = h.find_stack(name, load=must_exist)
            if st is None:
                if not must_exist:
                    return Obj("missing")
                return Obj("stack", h.stack)
            return Obj("stack", st)
        if typ in ("card", "bg"):
            obj = self._layer_ref(typ, ordinal, stop, must_exist)
            spec = self._last_card_spec
            # "card 3 of bg 2", "card x of this stack"
            while self.peek_low() == "of" and self.peek_low(1) in ("bg", "bkgnd", "background", "this", "stack"):
                self.next()
                sub = self.object_ref(stop, must_exist)
                if typ == "card" and sub is not None and sub.t == "bg" and obj is not None and obj.t == "card" \
                        and spec is not None:
                    obj = self._card_in_bg(spec, sub.a, must_exist)
            if obj is not None and obj.t == "card" and obj.a is None:
                return Obj("missing") if not must_exist else None
            return obj
        # button / field / part
        ptype = typ
        kind = layer_word or ("cd" if ptype in ("button", "part") else "bg")
        spec = self._spec(ordinal, stop)
        layer_obj = None
        if self.peek_low() in ("of", "in") and self.peek_low(1) in ("card", "cd", "this", "bg", "bkgnd",
                                                                      "background", "next", "prev", "previous",
                                                                      "first", "last") \
                and not (self.peek_low(1) in ("card", "cd") and self.peek_low(2) in ("button", "btn", "field", "fld")):
            self.next()
            layer_obj = self.object_ref(stop, must_exist)
        if layer_obj is not None and layer_obj.t == "card":
            card = layer_obj.a
        else:
            card = h.card
        layer = card if kind == "cd" else h.bg_of(card)
        if layer is None:
            return Obj("missing") if not must_exist else None
        cands = [p for p in layer.parts if ptype == "part" or p.type == ptype]
        p = _pick(cands, spec)
        if p is None:
            if not must_exist:
                return Obj("missing")
            raise HTError("No such %s" % ptype)
        return Obj("part", kind, p, layer, card)

    _last_card_spec = None

    def _ref_end(self, stop):
        w = self.peek_low()
        return self.at_end() or w in stop or w in ("of", "in", "to", "into", "then", "with", "and", "or",
                                                   "is", "=", "<>", "&", "&&", ",", ")")

    def _spec(self, ordinal, stop):
        if ordinal is not None:
            return ("num", ordinal)
        w = self.peek_low()
        if w == "id":
            self.next()
            return ("id", int(to_num(self._atom_expr(stop))))
        v = self._atom_expr(stop)
        if is_number(v):
            return ("num", int(to_num(v)))
        return ("name", v)

    def _atom_expr(self, stop):
        # the thing right after "card"/"button": a number, string, parenthesised expr or variable
        t = self.peek()
        if t is None:
            raise HTError("Expected a name or number")
        if t.kind in ("str", "num"):
            self.i += 1
            return t.val
        if t.kind == "op" and t.val == "(":
            self.i += 1
            saved = self.stop
            v = self.expr()
            self.stop = saved
            if self.peek() is not None and self.peek().val == ")":
                self.i += 1
            return v
        if t.kind == "op" and t.val == "-":
            self.i += 1
            return "-" + self._atom_expr(stop)
        if t.low in CHUNKS or (t.low in ORDINALS and self.peek_low(1) in CHUNKS):
            return self.it.value_of(self.chunk_ref())
        if t.low in ORDINALS:
            self.i += 1
            return str(ORDINALS[t.low])
        nt = self.peek(1)
        if t.kind == "word" and nt is not None and nt.kind == "op" and nt.val == "(" and \
                t.low not in OBJ_WORDS:
            self.i += 2
            return self.call_function(t.val, self.paren_args())
        # word: variable or bare name
        self.i += 1
        if t.low == "the":
            self.i -= 1
            saved = self.stop
            self.stop = tuple(stop) + ("of",)
            v = self.factor()
            self.stop = saved
            return v
        v = self.it.get_var(t.low)
        return v if v is not None else t.val

    def _name_expr(self, stop):
        return self._atom_expr(stop)

    def _layer_ref(self, typ, ordinal, stop, must_exist):
        h = self.it.host
        st = h.stack
        if ordinal is None and self._ref_end(stop):
            # plain "card" / "bg" = this one
            self._last_card_spec = None
            return Obj("card", h.card) if typ == "card" else Obj("bg", h.bg_of(h.card))
        spec = self._spec(ordinal, stop)
        self._last_card_spec = spec if typ == "card" else None
        items = st.cards if typ == "card" else st.backgrounds
        if typ == "card" and self.peek_low() == "of" and self.peek_low(1) in ("bg", "bkgnd", "background"):
            return Obj("card", None)  # resolved by caller with the background
        x = _pick(items, spec)
        if x is None:
            if not must_exist:
                return Obj("missing")
            raise HTError("No such %s" % ("card" if typ == "card" else "background"))
        return Obj(typ, x)

    def _card_in_bg(self, spec, bg, must_exist):
        cards = [c for c in self.it.host.stack.cards if c.bg_id == bg.id]
        x = _pick(cards, spec)
        if x is None:
            if not must_exist:
                return Obj("missing")
            raise HTError("No such card")
        return Obj("card", x)


PROP_WORDS = {
    "name", "id", "number", "visible", "enabled", "hilite", "highlight", "hilight", "highlite",
    "autohilite", "sharedhilite", "showname", "locktext", "dontwrap", "dontsearch", "sharedtext",
    "fixedlineheight", "autotab", "showlines", "widemargins", "autoselect", "multiplelines", "style",
    "rect", "rectangle", "loc", "location", "left", "top", "right", "bottom", "width", "height",
    "topleft", "bottomright", "botright", "icon", "family", "textfont", "textsize", "textheight",
    "textstyle", "textalign", "script", "titlewidth", "selectedline", "selectedtext", "scroll",
    "cantdelete", "cantmodify", "cantabort", "cantpeek", "showpict", "marked", "checkmark",
    "menumsg", "menumessage", "markchar", "cmdchar", "commandchar", "alternatetext", "freesize",
    "size", "reporttemplates", "owner", "partnumber", "selectedfield", "zoom", "picture"}

_NEVER_REDEFINED = {"global", "put", "get", "set", "return", "exit", "next", "pass", "if", "repeat",
                    "end", "else", "do", "send"}

_EXPR_STOP = {"into", "to", "then", "is", "and", "or", "else", "with", "contains", "before", "after",
              "div", "mod", "by", "from", "thru", "through", "times", "down"}


def parse_ref(it, text):
    """Turn a value such as 'char 3 to 7 of bkgnd field 2' or 'card button id 5'
    back into a reference. Returns None if it is not one."""
    toks = tokenize(str(text))
    if not toks:
        return None
    p = Parser(it, toks, 0)
    w = p.peek_low()
    try:
        if w in CHUNKS or (w in ORDINALS and p.peek_low(1) in CHUNKS):
            return p.chunk_ref()
        if p._looks_like_object():
            return p.object_ref()
    except HTError:
        return None
    return None


class _Literal:
    def __init__(self, v):
        self.v = v


_orig_value_of = Interp.value_of


def _value_of(self, ref):
    if isinstance(ref, _Literal):
        return ref.v
    return _orig_value_of(self, ref)


def _put_into_wrap(orig):
    def f(self, ref, value, prep="into"):
        if isinstance(ref, _Literal):
            return
        return orig(self, ref, value, prep)
    return f


Interp.value_of = _value_of
Interp.put_into = _put_into_wrap(Interp.put_into)


def _pick(items, spec):
    kind, v = spec
    if kind == "num":
        n = len(items)
        if v == "mid":
            v = n // 2 + 1
        elif v == "any":
            v = _random.randint(1, max(1, n))
        elif v < 0:
            v = n + 1 + v
        return items[v - 1] if 1 <= v <= n else None
    if kind == "id":
        return next((x for x in items if x.id == v), None)
    low = str(v).lower()
    return next((x for x in items if (x.name or "").lower() == low), None)


def _eq(a, c):
    if is_number(a) and is_number(c):
        return to_num(a) == to_num(c)
    # surrounding spaces don't count, but a return is a real character:
    # `repeat until char n of it is not return` must stop at the end of the text
    # (but empty is not equal to a space: `repeat while char 1 of x is space` must end)
    a, c = str(a).lower(), str(c).lower()
    if a == c:
        return True
    sa, sc = a.strip(" \t"), c.strip(" \t")
    return sa == sc and sa != ""


def _is_type(v, typ):
    if typ == "number":
        return is_number(v)
    if typ == "integer":
        return is_number(v) and float(to_num(v)).is_integer()
    if typ == "logical":
        return str(v).lower() in (TRUE, FALSE)
    if typ in ("point",):
        p = str(v).split(",")
        return len(p) == 2 and all(is_number(x) for x in p)
    if typ in ("rect", "rectangle"):
        p = str(v).split(",")
        return len(p) == 4 and all(is_number(x) for x in p)
    if typ in ("date",):
        try:
            _parse_date(v)
            return True
        except Exception:  # noqa: BLE001
            return False
    if typ == "empty":
        return v == ""
    return False


_DATE_FMTS = ("%m/%d/%y", "%m/%d/%Y", "%A, %B %d, %Y", "%a, %b %d, %Y", "%B %d, %Y", "%b %d, %Y",
              "%A, %b %d, %Y", "%a, %B %d, %Y", "%m-%d-%y", "%m-%d-%Y", "%Y-%m-%d")
_TIME_FMTS = ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S", "%I:%M%p", "%I:%M:%S%p")


def _parse_date(v):
    """A HyperCard date, time, date and time, dateItems or seconds value,
    as Unix seconds in local time."""
    v = " ".join(str(v).split())
    if is_number(v):
        return float(v) - 2082844800
    parts = v.split(",")
    if len(parts) in (6, 7) and all(is_number(x) for x in parts):
        y, mo, d, hh, mi, ss = (int(to_num(x)) for x in parts[:6])
        if y < 100:
            y += 1900 if y >= 20 else 2000
        return time.mktime((y, mo, d, hh, mi, ss, 0, 0, -1))
    for df in _DATE_FMTS:
        for tf in ("",) + _TIME_FMTS:
            try:
                t = time.strptime(v, (df + " " + tf).strip())
            except ValueError:
                continue
            return time.mktime(t[:8] + (-1,))
    for tf in _TIME_FMTS:
        try:
            t = time.strptime(v, tf)
        except ValueError:
            continue
        now = time.localtime()
        return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, 0, 0, -1))
    raise ValueError(v)


def _format_one(t, secs, fmt):
    fmt = fmt.strip()
    if fmt in ("seconds", "secs", "second"):
        return str(int(round(secs)) + 2082844800)
    if fmt == "dateitems":
        return ",".join(str(x) for x in (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec,
                                         (t.tm_wday + 1) % 7 + 1))
    if fmt == "long date":
        return time.strftime("%A, %B %-d, %Y", t)
    if fmt in ("abbr date", "abbrev date", "abbreviated date"):
        return time.strftime("%a, %b %-d, %Y", t)
    if fmt in ("date", "short date"):
        return time.strftime("%-m/%-d/%y", t)
    if fmt == "long time":
        return time.strftime("%-I:%M:%S %p", t)
    if fmt in ("time", "short time", "abbr time", "abbrev time", "abbreviated time"):
        return time.strftime("%-I:%M %p", t)
    return None


def _format_date(secs, fmt):
    t = time.localtime(secs)
    fmt = " ".join(fmt.lower().split())
    out = []
    for piece in fmt.split(" and "):
        r = _format_one(t, secs, piece)
        if r is None:
            return None
        out.append(r)
    return " ".join(out)


# built-in functions -----------------------------------------------------------------

def _f(fn):
    return lambda it, a: fn(*a)


def _nums(a):
    vals = []
    for x in a:
        for y in str(x).split(","):
            if y.strip():
                vals.append(to_num(y))
    return vals


def _offset(it, a):
    if len(a) < 2:
        return "0"
    k = str(a[1]).lower().find(str(a[0]).lower())
    return str(k + 1)


def _value(it, a):
    src = a[0] if a else ""
    try:
        return Parser(it, tokenize(src), 0).expr()
    except HTError:
        return src


def _annuity(rate, periods):
    if rate == 0:
        return periods
    return (1 - (1 + rate) ** -periods) / rate


_BUILTIN = {
    "length": lambda it, a: str(len(a[0]) if a else 0),
    "len": lambda it, a: str(len(a[0]) if a else 0),
    "offset": _offset,
    "abs": lambda it, a: fmt_num(abs(to_num(a[0]))),
    "round": lambda it, a: _finite(to_num(a[0]), lambda x: int(math.floor(x + 0.5)) if len(a) < 2 else round(x, int(to_num(a[1])))),
    "trunc": lambda it, a: _finite(to_num(a[0]), int),
    "random": lambda it, a: str(_random.randint(1, max(1, int(to_num(a[0])))) if a else 1),
    "max": lambda it, a: fmt_num(max(_nums(a))) if _nums(a) else "0",
    "min": lambda it, a: fmt_num(min(_nums(a))) if _nums(a) else "0",
    "sum": lambda it, a: fmt_num(sum(_nums(a))),
    "average": lambda it, a: fmt_num(sum(_nums(a)) / len(_nums(a))) if _nums(a) else "0",
    "sqrt": lambda it, a: fmt_num(math.sqrt(to_num(a[0]))),
    "sin": lambda it, a: fmt_num(math.sin(to_num(a[0]))),
    "cos": lambda it, a: fmt_num(math.cos(to_num(a[0]))),
    "tan": lambda it, a: fmt_num(math.tan(to_num(a[0]))),
    "atan": lambda it, a: fmt_num(math.atan(to_num(a[0]))),
    "exp": lambda it, a: fmt_num(math.exp(to_num(a[0]))),
    "ln": lambda it, a: fmt_num(math.log(to_num(a[0]))),
    "log2": lambda it, a: fmt_num(math.log2(to_num(a[0]))),
    "exp2": lambda it, a: fmt_num(2 ** to_num(a[0])),
    "exp1": lambda it, a: fmt_num(math.expm1(to_num(a[0]))),
    "ln1": lambda it, a: fmt_num(math.log1p(to_num(a[0]))),
    "annuity": lambda it, a: fmt_num(_annuity(to_num(a[0]), to_num(a[1]))) if len(a) > 1 else "0",
    "compound": lambda it, a: fmt_num((1 + to_num(a[0])) ** to_num(a[1])) if len(a) > 1 else "0",
    "chartonum": lambda it, a: str(ord(a[0][0])) if a and a[0] else "0",
    "numtochar": lambda it, a: chr(int(to_num(a[0]))) if a else "",
    "value": _value,
    "date": lambda it, a: time.strftime("%-m/%-d/%y"),
    "time": lambda it, a: time.strftime("%-I:%M %p"),
    "seconds": lambda it, a: str(int(time.time()) + 2082844800),
    "ticks": lambda it, a: str(int(time.monotonic() * 60)),
    "upper": lambda it, a: a[0].upper() if a else "",
    "lower": lambda it, a: a[0].lower() if a else "",
}



def _math_safe(f, name):
    # HyperCard answers INF or NAN instead of stopping the script: sqrt(-1), ln(0), cos(1e400)
    def g(it, a):
        try:
            return f(it, a)
        except ZeroDivisionError:
            return "INF"
        except OverflowError:
            return "INF"
        except ValueError:
            if name in ("ln", "log2", "ln1") and a and to_num(a[0]) == (0 if name != "ln1" else -1):
                return "-INF"
            return "NAN"
    return g


for _k in ("sqrt", "sin", "cos", "tan", "atan", "exp", "exp1", "exp2", "ln", "ln1", "log2",
           "annuity", "compound"):
    if _k in _BUILTIN:
        _BUILTIN[_k] = _math_safe(_BUILTIN[_k], _k)

_FUNCS_OF = {k: v for k, v in _BUILTIN.items() if k in (
    "length", "abs", "round", "trunc", "random", "sqrt", "sin", "cos", "tan", "atan", "exp", "ln",
    "chartonum", "numtochar", "value", "log2", "exp2", "exp1", "ln1")}

_NOARG = {"date", "time", "seconds", "ticks", "secs", "version", "result", "target", "selection",
          "mouse", "mouseh", "mousev", "mouseloc", "clickh", "clickv", "clickloc", "screenrect",
          "stacksinuse", "menus", "windows", "tool", "foundtext", "foundline", "foundchunk", "foundfield",
          "clicktext", "clickline", "clickchunk", "selectedtext", "selectedline", "selectedchunk",
          "commandkey", "cmdkey", "optionkey", "shiftkey", "systemversion", "diskspace", "heapspace",
          "sound", "paramcount", "params", "platform"}

_THE_NOARG = _NOARG | {"short date", "long date", "abbr date", "abbrev date", "abbreviated date",
                       "short time", "long time", "long version", "short version", "short target",
                       "long target", "number", "freesize", "stackspace", "random", "mouseclick",
                       "address", "environment", "suspended", "menubar", "destination", "tempo"}

_THE_NOARG_STRICT = {"date", "time", "seconds", "ticks", "secs", "version", "result", "target",
                     "short date", "long date", "abbr date", "short time", "long time", "long version",
                     "mouse", "mouseh", "mousev", "mouseloc", "clickh", "clickv", "clickloc",
                     "screenrect", "stacksinuse", "menus", "windows", "tool", "sound", "paramcount",
                     "params", "commandkey", "optionkey", "shiftkey", "selection", "selectedtext",
                     "foundtext", "foundchunk", "foundline", "foundfield", "clicktext", "clickline",
                     "clickchunk", "systemversion", "platform", "diskspace", "heapspace"}

_SINGULAR_OBJ_WORDS = {"card", "cd", "bg", "bkgnd", "background", "button", "btn", "field", "fld", "part"}

_THE_PROPS_NO_OBJ = {"itemdelimiter", "numberformat", "lockscreen", "lockmessages", "userlevel", "cursor",
                     "blindtyping", "powerkeys", "textarrows", "editbkgnd", "dragspeed", "language",
                     "lockrecent", "usermodify", "lockerrordialogs", "longwindowtitles", "messagewatcher",
                     "variablewatcher", "scripteditor", "scripttextfont", "scripttextsize", "tracedelay",
                     "printmargins", "printtextalign", "printtextfont", "printtextheight", "printtextsize",
                     "printtextstyle", "dialingtime", "dialingvolume", "brush", "centered", "filled", "grid",
                     "linesize", "multiple", "multispace", "pattern", "polysides", "textalign", "textfont",
                     "textheight", "textsize", "textstyle"}

_MULTIWORD_PROPS = {"short name", "long name", "abbr name", "abbrev name", "abbreviated name", "short id",
                    "long id", "short date", "long date", "abbr date", "short time", "long time",
                    "long version", "short version", "short target", "long target"}

