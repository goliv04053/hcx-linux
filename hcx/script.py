"""A small HyperTalk runner. It is not a full interpreter: it follows the
HyperCard message path (button/field → card → background → stack), runs
the user's own handlers, and maps the common commands to Linux/GNOME
equivalents (dialogs, sound, transitions, search, menus). Lines it does
not understand are skipped; conditional and loop blocks are skipped as a
whole so that nothing runs by accident."""

import re

from . import talk

MAX_DEPTH = 12
MAX_STEPS = 400

_PART_RE = re.compile(
    r'^(?:(card|cd|bkgnd|background|bg)\s+)?(button|btn|field|fld)\s+'
    r'(?:id\s+(\d+)|(\d+)|"([^"]*)"|(\w+))', re.I)

_EFFECTS = ("dissolve", "wipe", "scroll", "iris", "barn door", "zoom", "venetian blinds",
            "checkerboard", "shrink", "stretch", "push", "plain", "cut")


class Stop(Exception):
    pass


class Runner:
    """`host` is the window. It provides: stack, card (current), bg_of(card),
    go_nav(tuple, effect), play_sound(name), beep(), answer(text, buttons),
    find(text), domenu(name), refresh()."""

    def __init__(self, host):
        self.host = host
        self.effect = None
        self.steps = 0

    # -- message path --------------------------------------------------------------
    def chain(self, target):
        h = self.host
        out = []
        if target is not None:
            out.append(target)
        card = h.card
        if card is not None:
            out.append(("card", card))
            bg = h.bg_of(card)
            if bg is not None:
                out.append(("bg", bg))
        out.append(("stack", h.stack))
        return out

    @staticmethod
    def script_of(obj):
        kind = obj[0]
        if kind == "part":
            return obj[2].script or ""
        return obj[1].script or ""

    def send(self, msg, target=None, depth=0):
        """Send `msg` along the message path. Returns True if some handler ran."""
        if depth == 0:
            self.steps = 0
            self.effect = None
        if depth > MAX_DEPTH:
            return False
        objs = self.chain(target)
        for i, obj in enumerate(objs):
            lines = talk.handler(self.script_of(obj), msg.lower())
            if lines is None:
                continue
            try:
                passed = self.run(lines, obj, depth)
            except Stop:
                return True
            if not passed:
                return True
            # `pass msg`: keep going up the path
        return False

    # -- statements -------------------------------------------------------------------
    def run(self, lines, me, depth):
        """Run handler lines. Returns True when the handler did `pass`."""
        i = 0
        n = len(lines)
        while i < n:
            self.steps += 1
            if self.steps > MAX_STEPS:
                raise Stop()
            ln = lines[i].strip()
            low = ln.lower()
            i += 1
            if not ln:
                continue
            first = low.split()[0]
            if first == "if":
                # single-line `if … then …` is skipped; block ifs are skipped to `end if`
                if re.search(r"\bthen\s*$", low):
                    i = self._skip_block(lines, i, "if")
                continue
            if first == "else":
                if low == "else" or re.search(r"\bthen\s*$", low):
                    i = self._skip_block(lines, i, "if")
                continue
            if first == "repeat":
                i = self._skip_block(lines, i, "repeat")
                continue
            if first == "end":
                continue
            if first == "pass":
                return True
            if first == "exit":
                raise Stop()
            try:
                self.statement(ln, low, first, me, depth)
            except Stop:
                raise
            except Exception:  # noqa: BLE001 - never let a script crash the viewer
                pass
        return False

    @staticmethod
    def _skip_block(lines, i, kind):
        """Skip to the end of an `if … then` / `repeat` block opened just before i."""
        level = 1
        n = len(lines)
        while i < n and level:
            low = lines[i].strip().lower()
            if kind == "if":
                if low.startswith("if ") and re.search(r"\bthen\s*$", low):
                    level += 1
                elif low == "end if":
                    level -= 1
                elif low.startswith("else") and not (low == "else" or re.search(r"\bthen\s*$", low)):
                    level -= 1  # single-line else closes the block
            else:
                if low.startswith("repeat"):
                    level += 1
                elif low == "end repeat":
                    level -= 1
            i += 1
        return i

    def statement(self, ln, low, first, me, depth):
        h = self.host
        me_name = me[2].name if me and me[0] == "part" else (me[1].name if me and me[0] != "stack" else "")
        if first == "go":
            g = talk.parse_go(talk._subst_me(ln, me_name))
            if g and g[0] != "other":
                h.go_nav(g, self.effect)
                self.effect = None
            return
        if first in ("visual", "vis"):
            m = re.match(r"(?:visual|vis)\s+(?:effect\s+)?(.*)$", low)
            eff = (m.group(1) if m else "").strip()
            for name in _EFFECTS:
                if eff.startswith(name):
                    rest = eff[len(name):]
                    direction = ""
                    for d in ("left", "right", "up", "down", "open", "close", "in", "out"):
                        if re.search(r"\b%s\b" % d, rest):
                            direction = d
                            break
                    speed = "normal"
                    for sp in ("very slowly", "very slow", "slowly", "slow", "very fast", "fast"):
                        if sp in rest:
                            speed = sp.replace("ly", "")
                            break
                    self.effect = (name, direction, speed)
                    break
            return
        if first == "play":
            m = re.match(r'play\s+(?:"([^"]*)"|(\w+))', ln, re.I)
            if m:
                name = m.group(1) or m.group(2)
                if name.lower() != "stop":
                    h.play_sound(name)
            return
        if first == "beep":
            h.beep()
            return
        if first == "answer":
            self._answer(ln, me, me_name)
            return
        if first in ("show", "hide"):
            self._show_hide(first == "show", ln, me)
            return
        if first == "set":
            self._set(ln, me)
            return
        if first == "put":
            self._put(ln, me, me_name)
            return
        if first == "push":
            h.push_card()
            return
        if first == "pop":
            h.go_nav(("back",), self.effect)
            self.effect = None
            return
        if first == "find":
            m = re.match(r'find\s+(?:whole\s+|string\s+|words?\s+|chars?\s+)?"([^"]*)"', ln, re.I)
            if m:
                h.find(m.group(1))
            return
        if first in ("domenu", "dmenu"):
            m = re.match(r'(?:domenu|dmenu)\s+"([^"]*)"', ln, re.I)
            if m:
                h.domenu(m.group(1))
            return
        if first in ("send",):
            m = re.match(r'send\s+"?(\w+)"?(?:\s+to\s+(.*))?$', ln, re.I)
            if m:
                tgt = None
                if m.group(2):
                    tgt = self.resolve_part(m.group(2).strip(), me)
                    tgt = ("part",) + tgt if tgt else None
                self.send(m.group(1), tgt, depth + 1)
            return
        # user-defined handler somewhere on the message path?
        word = re.match(r"(\w+)", ln)
        if word and first not in ("global", "get", "wait", "lock", "unlock", "click", "type", "select",
                                  "sort", "delete", "add", "subtract", "multiply", "divide", "do",
                                  "mark", "unmark", "start", "stop", "close", "open", "print", "create",
                                  "reset", "choose", "flash", "drag", "convert", "read", "write",
                                  "return", "next"):
            self.send(word.group(1), me if me and me[0] == "part" else None, depth + 1)

    # -- expressions (literals only) ------------------------------------------------------
    def value(self, expr, me_name):
        expr = expr.strip()
        parts = re.split(r"\s*(&&|&)\s*", expr)
        out = ""
        sep = ""
        for p in parts:
            if p == "&&":
                sep = " "
                continue
            if p == "&":
                sep = ""
                continue
            out += sep + self._atom(p, me_name)
            sep = ""
        return out

    def _atom(self, a, me_name):
        a = a.strip()
        if a.startswith('"') and a.endswith('"') and len(a) >= 2:
            return a[1:-1]
        low = a.lower()
        if low == "empty":
            return ""
        if low in ("return", "cr", "linefeed", "lf"):
            return "\n"
        if low in ("space",):
            return " "
        if low in ("tab",):
            return "\t"
        if low == "quote":
            return '"'
        if re.fullmatch(r"-?\d+(\.\d+)?", a):
            return a
        if re.fullmatch(r"(the\s+)?(short\s+)?name\s+of\s+me", low):
            return me_name
        ref = self.resolve_part(a, None)
        if ref and ref[1].type == "field":
            c = self.host.content_of(*ref)
            return c.text if c else ""
        raise ValueError("unsupported expression")

    # -- part references -------------------------------------------------------------------
    def resolve_part(self, ref, me):
        """'card button 3', 'fld "name"', 'bg field id 4', 'me' -> (kind, part, layer)"""
        ref = ref.strip()
        if ref.lower() in ("me", "the target", "target") and me and me[0] == "part":
            return me[1], me[2], me[3]
        m = _PART_RE.match(ref)
        if not m:
            return None
        layer_word, ptype_word, pid, num, qname, wname = m.groups()
        ptype = "button" if ptype_word.lower() in ("button", "btn") else "field"
        if layer_word:
            kind = "cd" if layer_word.lower() in ("card", "cd") else "bg"
        else:
            kind = "cd" if ptype == "button" else "bg"
        h = self.host
        layer = h.card if kind == "cd" else h.bg_of(h.card)
        if layer is None:
            return None
        cands = [p for p in layer.parts if p.type == ptype]
        p = None
        if pid:
            p = next((q for q in cands if q.id == int(pid)), None)
        elif num:
            k = int(num)
            p = cands[k - 1] if 1 <= k <= len(cands) else None
        else:
            name = (qname if qname is not None else wname or "").lower()
            p = next((q for q in cands if q.name.lower() == name), None)
        return (kind, p, layer) if p else None

    def _show_hide(self, show, ln, me):
        m = re.match(r"(?:show|hide)\s+(.*?)(?:\s+at\s+.*)?$", ln, re.I)
        if not m:
            return
        target = m.group(1)
        if target.lower() in ("menubar", "msg", "message box", "message", "tool window", "pattern window",
                              "card window", "picture", "card picture", "bg picture", "background picture",
                              "groups", "titlebar", "scroll window", "nav palette"):
            low = target.lower()
            if "picture" in low:
                h = self.host
                layer = h.bg_of(h.card) if low.startswith(("bg", "background")) else h.card
                if layer is not None:
                    layer.show_pict = show
                    h.refresh()
            return
        ref = self.resolve_part(target, me)
        if ref:
            ref[1].visible = show
            self.host.refresh()

    def _set(self, ln, me):
        m = re.match(r"set\s+(?:the\s+)?(\w+)\s+of\s+(.*?)\s+to\s+(.*)$", ln, re.I)
        if not m:
            return
        prop, target, val = m.group(1).lower(), m.group(2), m.group(3).strip().lower()
        ref = self.resolve_part(target, me)
        if not ref:
            return
        kind, p, layer = ref
        truth = val in ("true", '"true"')
        if prop in ("hilite", "highlight", "hilight", "highlite"):
            self.host.set_hilite(kind, p, truth)
        elif prop == "visible":
            p.visible = truth
        elif prop == "enabled":
            p.enabled = truth
        elif prop == "locktext":
            p.lock_text = truth
        elif prop == "icon" and re.fullmatch(r"-?\d+", val):
            p.icon = int(val)
        elif prop == "name":
            p.name = val.strip('"')
        else:
            return
        self.host.refresh()

    def _put(self, ln, me, me_name):
        m = re.match(r"put\s+(.*?)\s+(into|after|before)\s+(.*)$", ln, re.I)
        if not m:
            return
        src, prep, dest = m.group(1), m.group(2).lower(), m.group(3)
        ref = self.resolve_part(dest, me)
        if not ref or ref[1].type != "field":
            return
        text = self.value(src, me_name)
        c = self.host.content_of(*ref, create=True)
        if prep == "into":
            c.text = text
            c.runs = []
        elif prep == "after":
            c.text += text
        else:
            c.text = text + c.text
            c.runs = []
        self.host.refresh()

    def _answer(self, ln, me, me_name):
        m = re.match(r"answer\s+(?:file|folder|program)\b", ln, re.I)
        if m:
            return
        m = re.match(r"answer\s+(.*?)(?:\s+with\s+(.*))?$", ln, re.I)
        if not m:
            return
        try:
            text = self.value(m.group(1), me_name)
        except ValueError:
            return
        buttons = []
        if m.group(2):
            for b in re.split(r"\s+or\s+", m.group(2)):
                try:
                    buttons.append(self.value(b, me_name))
                except ValueError:
                    pass
        self.host.answer(text, buttons or ["OK"])
