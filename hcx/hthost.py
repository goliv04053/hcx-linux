"""The part of the window that HyperTalk talks to.

HostMixin keeps the interpreter's view of the world (current stack and
card, stacks in use, script menus, the message box, open files) and has
no GTK code of its own, so the interpreter can be tested headless. The
window subclasses it and overrides the hooks that need a UI."""

import os
import re
import sys
import time

from . import hypertalk as ht
from .hypertalk import HTError, Obj, Parser, ORDINALS, OBJ_WORDS


def files_root():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "hcx", "files")


def mac_to_local(path):
    """Map a Mac path ("Disk:Folder:File") to a file in HCX's sandbox
    folder. Plain absolute Linux paths are used as they are."""
    path = str(path).strip()
    if path.startswith("/") or path.startswith("~"):
        return os.path.expanduser(path)
    parts = [p for p in path.split(":") if p not in ("", ".", "..")]
    parts = [re.sub(r"[/\x00]", "_", p) for p in parts]
    if not parts:
        parts = ["untitled"]
    return os.path.join(files_root(), *parts)


def local_to_mac(path):
    """The inverse of mac_to_local: a file inside HCX's sandbox folder gets
    a Mac path ("HD:Folder:File"); other files keep their Linux path."""
    root = files_root()
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path
    if rel.startswith("..") or os.path.isabs(rel):
        return path
    parts = [p for p in rel.split(os.sep) if p not in ("", ".")]
    if not parts:
        return path
    return ":".join(parts)


class HostMixin:
    def init_host(self):
        self.interp = ht.Interp(self)
        self.addcolor_state = {"enabled": True, "extra": []}   # see addcolor.py and the AddColor XCMD
        self.stacks_using = []
        self._stack_cache = {}          # lower name -> Stack
        self.script_menus = []          # [name, [[item, msg, enabled, checked, cmdchar], ...], enabled, standard]
        self.menu_reset(notify=False)
        self.msg_box = ""
        self._open_files = {}
        self._log_seen = set()
        self._click = (0, 0)
        self._click_text = ""
        self.stack_sources = {}         # id(stack) -> (MacFile, path)
        self.str_overrides = {}
        self._rsrc_cache = {}
        self.windows = {}               # lower name -> dict of properties

    # -- logging / errors ---------------------------------------------------------
    def log(self, text):
        if os.environ.get("HCX_DEBUG") or text not in self._log_seen:
            self._log_seen.add(text)
            print("hcx:", text, file=sys.stderr)

    def script_error(self, text):
        self.log("script error: " + text)

    # -- dispatch -------------------------------------------------------------------
    LOCKABLE = {"opencard", "closecard", "openbackground", "closebackground", "openstack", "closestack",
                "resumestack", "suspendstack", "newcard", "deletecard"}

    def dispatch(self, msg, target=None, params=()):
        """Send a system message (mouseUp, openCard, …) from HyperCard."""
        it = self.interp
        if msg.lower() in self.LOCKABLE and it.props.get("lockmessages") == ht.TRUE:
            return True
        if it.depth > 0:
            # a message raised while a script runs (e.g. openCard during go)
            try:
                return it.send(msg, target, list(params), set_target=True)[0]
            except (ht._Exit, ht._Return):
                return True
        try:
            handled, _ = it.send(msg, target, list(params), set_target=True)
            return handled
        except HTError as e:
            self.script_error(str(e) + (" (%s)" % e.where if e.where else ""))
            return True
        except (ht._Exit, ht._Return):
            return True
        except RecursionError:
            self.script_error("Too much recursion")
            return True
        except Exception as e:  # noqa: BLE001 - a bug in HCX must not take the app down
            import traceback
            traceback.print_exc()
            where = it.where() if it.frame is not None else ""
            self.script_error("internal error %s: %s (%s)" % (type(e).__name__, e, where))
            it.depth = 0
            it.frame = None
            return True
        finally:
            if it.depth == 0:
                self.after_script()

    def after_script(self):
        """HyperCard resets these when all handlers have finished."""
        it = self.interp
        it.props["lockmessages"] = ht.FALSE
        it.props["lockrecent"] = ht.FALSE
        if it.props.get("lockscreen") == ht.TRUE:
            it.props["lockscreen"] = ht.FALSE
            self.lock_screen(False)

    def part_obj(self, kind, part, layer):
        return Obj("part", kind, part, layer)

    # -- basic state (window provides stack/card) ---------------------------------
    def stacks_in_use(self):
        return [s for s in self.stacks_using if s is not self.stack]

    def stack_path(self, st):
        """The Mac path of a stack, as in its long name. A stack that was a plain
        file gets a disk name in front: scripts find their folder by looking for
        the last colon (`repeat until last char of it is colon`)."""
        p = getattr(st, "mac_path", "") or st.name
        return p if ":" in p else "HD:" + p

    def changed(self, obj):
        self.refresh()

    def refresh(self):
        pass

    def content_of(self, kind, part, layer, create=False, card=None):
        from .model import Content
        card = card or self.card
        if kind == "cd":
            d = layer.contents
        elif (part.type == "field" and part.shared_text) or (part.type == "button" and part.shared_hilite):
            d = layer.contents
        else:
            d = card.bg_contents if card is not None else layer.contents
        c = d.get(part.id)
        if c is None and create:
            c = d[part.id] = Content()
        return c

    def get_hilite(self, kind, part, card=None):
        if kind == "bg" and not part.shared_hilite:
            c = self.content_of(kind, part, self.bg_of(card or self.card), card=card)
            if c is not None:
                return bool(c.hilite) if c.hilite is not None else c.text == "1"
            return False
        return part.hilite

    def set_hilite(self, kind, part, value, card=None):
        if kind == "bg" and not part.shared_hilite:
            c = self.content_of(kind, part, self.bg_of(card or self.card), create=True, card=card)
            c.hilite = value
            c.text = "1" if value else ""
        else:
            part.hilite = value

    def field_scroll(self, kind, part):
        return 0

    def set_field_scroll(self, kind, part, value):
        pass

    def font_name(self, fid):
        return (self.stack.fonts.get(fid) if self.stack else None) or "Geneva"

    def font_id(self, name):
        low = str(name).lower()
        for k, v in (self.stack.fonts.items() if self.stack else ()):
            if v.lower() == low:
                return k
        from .render import MAC_FONT_IDS
        for k, v in MAC_FONT_IDS.items():
            if v.lower() == low:
                return k
        return 3

    def icon_id(self, value):
        v = str(value).strip()
        if ht.is_number(v):
            return int(ht.to_num(v))
        low = v.lower()
        for m in self.stack.media if self.stack else ():
            if m.get("type") == "icon" and (m.get("name") or "").lower() == low:
                return int(m["id"])
        return 0

    # -- navigation --------------------------------------------------------------------
    def go_index(self, index, effect=None):
        raise NotImplementedError

    def go_back(self, effect=None):
        pass

    def go_forth(self, effect=None):
        pass

    def go_home(self, effect=None):
        st = self.find_stack("Home", load=True)
        if st is not None and st is not self.stack:
            self.show_stack_now(st)
        else:
            self.go_index(0, effect)

    def go_rel(self, which, effect=None):
        cards = self.stack.cards
        i = cards.index(self.card) if self.card in cards else 0
        n = len(cards)
        target = {"next": i + 1, "prev": i - 1, "previous": i - 1, "first": 0, "last": n - 1,
                  "this": i, "mid": n // 2, "middle": n // 2}.get(which)
        if which == "any":
            import random
            target = random.randrange(n)
        if target is None:
            return
        self.go_index(target % n, effect)

    def go_dest(self, p, effect):
        """`go [to] <destination>` with the parser positioned after go/to."""
        it = self.interp
        toks = list(p.toks[p.i:])
        low = [t.low for t in toks]
        new_window = False
        if low[-3:] == ["in", "new", "window"]:
            toks, low, new_window = toks[:-3], low[:-3], True
        if "without" in low:
            k = low.index("without")
            toks, low = toks[:k], low[:k]
        if not toks:
            return
        w = low[0]
        it.result = ""
        if w == "back" or w == "recent":
            self.go_back(effect)
            return
        if w in ("forth", "forward"):
            self.go_forth(effect)
            return
        if w == "home":
            self.go_home(effect)
            return
        if w == "help":
            return
        # split off "of stack …" / "stack …"
        k = next((j for j, t in enumerate(toks) if t.kind == "word" and t.low == "stack"), -1)
        st = None
        card_toks = toks
        if k >= 0:
            name_toks = toks[k + 1:]
            card_toks = toks[:k]
            if card_toks and card_toks[-1].low in ("of", "in"):
                card_toks = card_toks[:-1]
            if card_toks and card_toks[-1].low == "this":
                card_toks = card_toks[:-1]
                if card_toks and card_toks[-1].low in ("of", "in"):
                    card_toks = card_toks[:-1]
            elif name_toks:
                name = self._stack_name(name_toks)
                st = self.find_stack(name, load=True)
                if st is None:
                    it.result = "No such stack."
                    self.log("go: no such stack " + name)
                    return
        elif w not in OBJ_WORDS and w not in ORDINALS and w not in ("this", "next", "prev", "previous",
                                                                     "marked", "card", "cd"):
            # `go "Name"` means a stack
            name = self._stack_name(toks)
            st = self.find_stack(name, load=True)
            if st is None:
                it.result = "No such stack."
                return
            card_toks = []
        if st is not None and st is not self.stack:
            if new_window and hasattr(self, "open_stack_window"):
                self.open_stack_window(st)
                return
            self.show_stack_now(st, go_first=not card_toks)
            if not card_toks:
                return
        if not card_toks:
            return
        self._go_card(card_toks, effect)

    def _stack_name(self, toks):
        try:
            v = Parser(self.interp, toks, 0).expr()
        except HTError:
            v = " ".join(t.val for t in toks)
        if len(toks) > 1 and all(t.kind == "word" for t in toks):
            joined = " ".join(t.val for t in toks)
            if self.find_stack(joined, load=False) is not None or v.split()[0:1] != [toks[0].val]:
                return joined
        return v

    def _go_card(self, toks, effect):
        it = self.interp
        if toks and toks[0].kind == "word" and toks[0].low not in OBJ_WORDS and toks[0].low not in ORDINALS \
                and toks[0].low not in ("this", "next", "prev", "previous", "marked"):
            v = it.get_var(toks[0].low)
            if v is not None:
                toks = ht.tokenize(v) + list(toks[1:])
        low = [t.low for t in toks]
        cards = self.stack.cards
        # [ordinal] [marked] card
        if "marked" in low:
            marked = [c for c in cards if c.marked]
            if not marked:
                it.result = "No such card."
                return
            w = low[0]
            cur = cards.index(self.card) if self.card in cards else 0
            if w == "next":
                c = next((c for c in cards[cur + 1:] if c.marked), marked[0])
            elif w in ("prev", "previous"):
                c = next((c for c in reversed(cards[:cur]) if c.marked), marked[-1])
            elif w in ORDINALS and isinstance(ORDINALS[w], int):
                n = ORDINALS[w]
                c = marked[n - 1] if n > 0 and n <= len(marked) else (marked[n] if n < 0 else None)
            else:
                c = marked[0]
            if c is not None:
                self.go_index(cards.index(c), effect)
            return
        if len(low) <= 2 and low[0] in ORDINALS and (len(low) == 1 or low[1] in ("card", "cd")):
            self.go_rel(low[0] if low[0] != "previous" else "prev", effect)
            return
        if len(low) <= 2 and low[0] in ("next", "prev", "previous", "this") and \
                (len(low) == 1 or low[1] in ("card", "cd")):
            self.go_rel(low[0], effect)
            return
        if low[0] in ("this",) and len(low) == 2 and low[1] in ("card", "cd"):
            return
        try:
            pp = Parser(it, toks, 0)
            o = pp.object_ref()
        except HTError:
            o = None
        if o is None or o.t == "missing":
            it.result = "No such card."
            self.log("go: no such card " + " ".join(t.val for t in toks))
            return
        if o.t == "card" and o.a is not None:
            self.go_index(cards.index(o.a), effect)
        elif o.t == "bg":
            cur = cards.index(self.card) if self.card in cards else 0
            order = cards[cur + 1:] + cards[:cur + 1]
            c = next((c for c in order if c.bg_id == o.a.id), None)
            if c is not None:
                self.go_index(cards.index(c), effect)
        else:
            it.result = "No such card."

    def push_card(self):
        if not hasattr(self, "card_stack"):
            self.card_stack = []
        self.card_stack.append((self.stack, self.card))
        del self.card_stack[:-20]

    def pop_card(self, effect=None):
        cs = getattr(self, "card_stack", [])
        if not cs:
            return
        st, card = cs.pop()
        if st is not self.stack:
            self.show_stack_now(st, go_first=False)
        if card in self.stack.cards:
            self.go_index(self.stack.cards.index(card), effect)

    # -- stacks ---------------------------------------------------------------------------
    def find_stack(self, name, load=True):
        name = str(name).strip()
        if not name:
            return self.stack
        short = re.split(r"[:/]", name.rstrip(":"))[-1].strip().lower()
        for st in [self.stack] + self.stacks_using:
            if st is not None and st.name.lower() == short:
                return st
        if short in self._stack_cache:
            # False: looked for it before and it is not there
            return self._stack_cache[short] or None
        if not load:
            return None
        st = self.load_stack_named(short)
        self._stack_cache[short] = st if st is not None else False
        return st

    def load_stack_named(self, short):
        """Find and import a sibling stack: another stack on the same disk
        image, a stack file next to the current one, or an HCX package in
        the same folder. Returns a Stack or None."""
        from . import macfiles
        from .model import read_xml
        from .stackimport import import_stack
        src = getattr(self, "source", None)
        cands = []
        if src:
            mf0, path = src
            try:
                res = self._open_cache.get(path) if hasattr(self, "_open_cache") else None
                if res is None:
                    res = macfiles.open_path(path)
                    if not hasattr(self, "_open_cache"):
                        self._open_cache = {}
                    self._open_cache[path] = res
                cands = [mf for mf in res.stacks if mf.name.lower() == short]
                if not cands and res.kind != "hfs":
                    folder = os.path.dirname(path)
                    for fn in sorted(os.listdir(folder)):
                        base = os.path.splitext(fn)[0].lower()
                        if base == short or fn.lower() == short:
                            r = macfiles.open_path(os.path.join(folder, fn))
                            cands = [(mf, os.path.join(folder, fn)) for mf in r.stacks]
                            if cands:
                                break
                else:
                    cands = [(mf, path) for mf in cands]
            except Exception as e:  # noqa: BLE001
                self.log("looking for stack %s: %s" % (short, e))
                cands = []
        for mf, path in cands:
            try:
                pkg = import_stack(mf, path, encoding=getattr(self, "encoding", "auto"))
                st = read_xml(pkg)
                st.mac_path = (mf.path or mf.name).replace("/", ":")
                self.stack_sources[id(st)] = (mf, path)
                return st
            except Exception as e:  # noqa: BLE001
                self.log("importing stack %s: %s" % (short, e))
        # HCX packages next to the current one
        cur = getattr(self.stack, "dir_path", "")
        if cur:
            folder = os.path.dirname(cur.rstrip("/"))
            try:
                for fn in os.listdir(folder):
                    d = os.path.join(folder, fn)
                    if d != cur and os.path.exists(os.path.join(d, "_stack.xml")):
                        if fn.lower().startswith(short):
                            st = read_xml(d)
                            if st.name.lower() == short:
                                return st
            except OSError:
                pass
        return None

    def start_using(self, name):
        st = self.find_stack(name, load=True)
        if st is None:
            self.interp.result = "No such stack."
            return
        if st not in self.stacks_using and st is not self.stack:
            self.stacks_using.append(st)
            del self.stacks_using[:-16]

    def stop_using(self, name):
        st = self.find_stack(name, load=False)
        if st in self.stacks_using:
            self.stacks_using.remove(st)

    def show_stack_now(self, st, go_first=True):
        raise NotImplementedError

    def open_stack(self, name, new_window=False):
        st = self.find_stack(name, load=True)
        if st is not None:
            self.show_stack_now(st)

    # -- message box --------------------------------------------------------------------------
    def msg_text(self):
        return self.msg_box

    def set_msg(self, text):
        self.msg_box = str(text)
        self.show_msg_text(self.msg_box)

    def show_msg_text(self, text):
        pass

    def show_msg(self, show):
        pass

    def selection_text(self):
        return ""

    def type_text(self, text):
        """`type`/keyDown: insert text where the insertion point is."""
        pass

    def close_field(self):
        """enterInField/tabKey: stop editing the field."""
        pass

    # -- dialogs ------------------------------------------------------------------------------
    def answer(self, text, buttons):
        return buttons[0] if buttons else "OK"

    def ask(self, prompt, default="", password=False):
        return default

    def choose_file(self, kind, prompt="", default="", patterns=None):
        """A file chosen by the user (Mac-style path), or None if cancelled."""
        return None

    def ditl_dialog(self, spec, on_click=None):
        """Show a dialog made from DITL items (see macdialog). Without a
        screen, answers as if the default button had been clicked."""
        values = {}
        for it in spec["items"]:
            k = it["kind"]
            if k == "edit":
                values[it["n"]] = it.get("text", "")
            elif k in ("check", "radio"):
                values[it["n"]] = bool(it.get("checked"))
            elif k == "list":
                values[it["n"]] = list(it.get("selected", []))
            elif k == "popup":
                values[it["n"]] = int(it.get("selected_item") or 1)
        return spec.get("default", 1), values

    def beep(self):
        pass

    def play_sound(self, name):
        pass

    def stop_sound(self):
        pass

    def sound_state(self):
        return "done"

    def wait(self, secs):
        time.sleep(min(secs, 0.2))

    def lock_screen(self, on, effect=None):
        pass

    def set_cursor(self, name):
        pass

    # -- find ----------------------------------------------------------------------------------
    def card_fields(self, card):
        """[(kind, part, layer, text)] of the searchable fields of a card, in HyperCard's order."""
        out = []
        bg = self.bg_of(card)
        if bg is not None and not bg.dont_search:
            for p in bg.parts:
                if p.type == "field" and not p.dont_search and not p.shared_text:
                    c = self.content_of("bg", p, bg, card=card)
                    if c is not None and c.text:
                        out.append(("bg", p, bg, c.text))
        for p in card.parts:
            if p.type == "field" and not p.dont_search:
                c = card.contents.get(p.id)
                if c is not None and c.text:
                    out.append(("cd", p, card, c.text))
        return out

    @staticmethod
    def match_text(text, query, mode):
        """(start, end) of the first match of query in text, or None."""
        low = text.lower()
        q = query.lower().strip()
        if not q:
            return None
        if mode in ("string",):
            k = low.find(q)
            return (k, k + len(q)) if k >= 0 else None
        if mode == "whole":
            for m in re.finditer(re.escape(q), low):
                s0, s1 = m.start(), m.end()
                if (s0 == 0 or not low[s0 - 1].isalnum()) and (s1 == len(low) or not low[s1].isalnum()):
                    return (s0, s1)
            return None
        words = q.split()
        first = None
        for w in words:
            if mode in ("word", "words"):
                m = re.search(r"(?<![\w])%s(?![\w])" % re.escape(w), low)
            elif mode in ("char", "chars"):
                m = re.search(re.escape(w), low)
            else:   # normal: each word must begin a word
                m = re.search(r"(?<![\w])%s" % re.escape(w), low)
            if m is None:
                return None
            if first is None:
                first = (m.start(), m.end())
        return first

    def find_cards(self, text, mode="normal"):
        return [c for c in self.stack.cards
                if not c.dont_search and any(self.match_text(t, text, mode) for _, _, _, t in self.card_fields(c))]

    def find(self, text, mode="normal", field=None, marked=False):
        """HyperTalk find: searches from the current position, goes to the card
        and shows the match. Sets the result to "not found" when nothing matches."""
        it = self.interp
        st = self.stack
        cards = st.cards
        n = len(cards)
        cur = cards.index(self.card) if self.card in cards else 0
        last = getattr(self, "_found", None)
        if last is not None and (last[0] is not self.card or last[5] != (text, mode)):
            last = None
        for step in range(n + 1):
            card = cards[(cur + step) % n]
            if card.dont_search or (marked and not card.marked):
                continue
            fields = self.card_fields(card)
            for j, (kind, p, layer, t) in enumerate(fields):
                if field is not None and p is not field.b:
                    continue
                offset = 0
                if step == 0 and last is not None:
                    if j < last[4]:
                        continue
                    if j == last[4]:
                        offset = last[3][1]
                if step == n and (last is None or j > last[4] or (j == last[4] and offset >= last[3][0])):
                    break
                m = self.match_text(t[offset:], text, mode)
                if m is not None:
                    span = (m[0] + offset, m[1] + offset)
                    self._found = (card, kind, p, span, j, (text, mode), layer)
                    it.result = ""
                    if card is not self.card:
                        self.go_index(cards.index(card))
                    self.show_found(kind, p, layer, span)
                    return True
        self._found = None
        it.result = "not found"
        self.show_found(None, None, None, None)
        return False

    def show_found(self, kind, part, layer, span):
        pass

    def found(self, what):
        f = getattr(self, "_found", None)
        if f is None or f[0] is not self.card:
            return ""
        card, kind, p, (s0, s1), j, _, layer = f
        num = layer.parts.index(p) + 1 if False else \
            [q for q in layer.parts if q.type == "field"].index(p) + 1
        fdesc = "%s field %d" % ("bkgnd" if kind == "bg" else "card", num)
        if what == "foundchunk":
            return "char %d to %d of %s" % (s0 + 1, s1, fdesc)
        if what == "foundfield":
            return fdesc
        c = self.content_of(kind, p, layer)
        text = c.text if c else ""
        if what == "foundtext":
            return text[s0:s1]
        if what == "foundline":
            return "line %d of %s" % (text[:s0].count("\n") + 1, fdesc)
        return ""

    def click_at(self, x, y):
        pass

    def click_text(self):
        return self._click_text

    def click_chunk(self, which):
        return ""

    def click_loc(self):
        return self._click

    def color_refresh(self, effect=None):
        """AddColor changed what the card looks like."""
        self.refresh()

    def poll(self):
        """Called every so often while a script runs."""

    def mouse_clicked(self):
        """`the mouseClick`: has the user clicked since the script started (or since the last check)?"""
        return False

    def mouse_is_down(self):
        return bool(getattr(self, "_mouse_down", False) or getattr(getattr(self, "view", None), "mouse_down", False))

    def mouse_loc(self):
        return self._click

    def screen_rect(self):
        return "0,0,1920,1080"

    def domenu(self, name, menu=None):
        """doMenu: run a script menu item's message, or the built-in action."""
        low = str(name).lower().strip()
        for m in self.script_menus:
            if menu and m[0].lower() != str(menu).lower():
                continue
            for item in m[1]:
                if item[0].lower() == low and item[1]:
                    self.run_menu_message(item[1])
                    return True
        return self.builtin_menu(low)

    def builtin_menu(self, low):
        return False

    def run_menu_message(self, msg):
        it = self.interp
        words = msg.split(None, 1)
        if words:
            try:
                if it.depth == 0:
                    self.dispatch(words[0], None, it._msg_params(msg))
                else:
                    it.send(words[0], None, it._msg_params(msg))
            except HTError as e:
                self.script_error(str(e))

    # -- menus ----------------------------------------------------------------------------------
    STANDARD_MENUS = {
        "Apple": ["About HyperCard…", "-"],
        "File": ["New Stack…", "Open Stack…", "Close Stack", "Save a Copy…", "-", "Compact Stack",
                 "Protect Stack…", "Delete Stack…", "-", "Page Setup…", "Print Field…", "Print Card",
                 "Print Stack…", "Print Report…", "-", "Quit HyperCard"],
        "Edit": ["Undo", "-", "Cut", "Copy", "Paste", "Clear", "-", "New Card", "Delete Card", "Cut Card",
                 "Copy Card", "-", "Text Style…", "Background"],
        "Go": ["Back", "Home", "Help", "Recent", "-", "First", "Prev", "Next", "Last", "-", "Find…",
               "Message", "Scroll", "Next Window"],
        "Tools": ["Browse", "Button", "Field"],
        "Objects": ["Button Info…", "Field Info…", "Card Info…", "Bkgnd Info…", "Stack Info…", "-",
                    "Bring Closer", "Send Farther", "-", "New Button", "New Field", "New Background"],
        "Font": ["Chicago", "Geneva", "Helvetica", "Monaco", "New York", "Palatino", "Times"],
        "Style": ["Plain", "Bold", "Italic", "Underline", "Outline", "Shadow", "Condense", "Extend", "Group"],
    }

    def _menu(self, name):
        low = str(name).strip().lower()
        if ht.is_number(low):
            n = int(ht.to_num(low))
            if 1 <= n <= len(self.script_menus):
                return self.script_menus[n - 1]
            return None
        return next((m for m in self.script_menus if m[0].lower() == low), None)

    def menu_names(self):
        return [m[0] for m in self.script_menus]

    def has_menu(self, name):
        return self._menu(name) is not None

    def has_menu_item(self, menu, item):
        m = self._menu(menu)
        return m is not None and self._item_index(m, item) is not None

    def menu_items(self, name):
        m = self._menu(name)
        return [tuple(i) for i in m[1]] if m else []

    def menu_create(self, name):
        if self._menu(name) is None:
            self.script_menus.append([str(name), [], True, False])
            self.menus_changed()

    def menu_delete(self, name):
        m = self._menu(name)
        if m is not None:
            self.script_menus.remove(m)
            self.menus_changed()

    def menu_reset(self, notify=True):
        self.script_menus = [[n, [[i, "", not i.startswith("-"), False, ""] for i in items], True, True]
                             for n, items in self.STANDARD_MENUS.items()]
        if notify:
            self.menus_changed()

    def menu_put(self, name, value, prep="into", msgs=None):
        m = self._menu(name)
        if m is None:
            return
        items = [x for x in str(value).split("\n")]
        if len(items) == 1 and "," in items[0]:
            items = items[0].split(",")
        while len(items) > 1 and items[-1] == "":
            items.pop()
        mlist = []
        if msgs is not None:
            mlist = str(msgs).split("\n")
            if len(mlist) == 1 and "," in mlist[0]:
                mlist = mlist[0].split(",")
        new = []
        for k, text in enumerate(items):
            text = text.strip()
            msg = mlist[k].strip() if k < len(mlist) else ""
            new.append([text.lstrip("("), msg, not text.startswith(("(", "-")), False, ""])
        if prep == "into":
            m[1] = new
        elif prep == "before":
            m[1] = new + m[1]
        else:
            m[1] = m[1] + new
        self.menus_changed()

    def menu_item_put(self, menu, item, value, prep="into", msgs=None):
        """put … into/before/after menuItem x of menu y"""
        m = self._menu(menu)
        if m is None:
            return
        i = self._item_index(m, item)
        if i is None:
            if prep == "into":
                return
            i = len(m[1]) if prep == "after" else 0
        items = [x for x in str(value).split("\n")]
        if len(items) == 1 and "," in items[0]:
            items = items[0].split(",")
        mlist = str(msgs).split("\n") if msgs is not None else []
        new = []
        for k, text in enumerate(items):
            text = text.strip()
            msg = mlist[k].strip() if k < len(mlist) else ""
            new.append([text.lstrip("("), msg, not text.startswith(("(", "-")), False, ""])
        if prep == "into":
            old = m[1][i]
            if len(new) == 1:
                old[0], old[2] = new[0][0], new[0][2]
                if msgs is not None:
                    old[1] = new[0][1]
            else:
                m[1][i:i + 1] = new
        elif prep == "before":
            m[1][i:i] = new
        else:
            m[1][i + 1:i + 1] = new
        self.menus_changed()

    def menu_delete_item(self, menu, item):
        m = self._menu(menu)
        if m is None:
            return
        i = self._item_index(m, item)
        if i is not None:
            del m[1][i]
            self.menus_changed()

    def _item_index(self, m, item):
        v = str(item).strip()
        if ht.is_number(v):
            i = int(ht.to_num(v)) - 1
            return i if 0 <= i < len(m[1]) else None
        low = v.lower().replace("...", "…")
        return next((k for k, x in enumerate(m[1]) if x[0].lower().replace("...", "…") == low), None)

    def menu_item_prop(self, menu, item, prop):
        m = self._menu(menu)
        if m is None:
            return ""
        i = self._item_index(m, item)
        if i is None:
            return ""
        x = m[1][i]
        if prop in ("name",):
            return x[0]
        if prop in ("menumsg", "menumessage"):
            return x[1]
        if prop == "enabled":
            return ht.b(x[2])
        if prop in ("checkmark",):
            return ht.b(x[3])
        if prop in ("commandchar", "cmdchar"):
            return x[4]
        if prop == "markchar":
            return "✓" if x[3] else ""
        if prop == "textstyle":
            return "plain"
        return ""

    def set_menu_item_prop(self, menu, item, prop, value):
        m = self._menu(menu)
        if m is None:
            return
        i = self._item_index(m, item)
        if i is None:
            return
        x = m[1][i]
        if prop == "name":
            x[0] = value
        elif prop in ("menumsg", "menumessage"):
            x[1] = value
        elif prop == "enabled":
            x[2] = ht.to_bool(value)
        elif prop in ("checkmark",):
            x[3] = ht.to_bool(value)
        elif prop in ("markchar",):
            x[3] = bool(value)
        elif prop in ("commandchar", "cmdchar"):
            x[4] = str(value).strip()[:1]
        self.menus_changed()

    def menu_prop(self, menu, prop):
        m = self._menu(menu)
        if m is None:
            return ""
        if prop in ("name", "short name", "long name", "abbr name"):
            return m[0] if prop != "long name" else 'menu "%s"' % m[0]
        if prop == "enabled":
            return ht.b(m[2])
        return ""

    def set_menu_prop(self, menu, prop, value):
        m = self._menu(menu)
        if m is not None and prop == "enabled":
            m[2] = ht.to_bool(value)
            self.menus_changed()

    def menus_changed(self):
        pass

    # -- files (sandboxed) ---------------------------------------------------------------------------
    def file_open(self, path):
        local = mac_to_local(path)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        if not os.path.exists(local):
            open(local, "a").close()
        self._open_files[str(path).lower()] = [local, 0]

    def file_close(self, path):
        self._open_files.pop(str(path).lower(), None)

    def _file(self, path):
        f = self._open_files.get(str(path).lower())
        if f is None:
            self.file_open(path)
            f = self._open_files[str(path).lower()]
        return f

    def file_read(self, path, until=None, count=None):
        f = self._file(path)
        try:
            with open(f[0], "rb") as fh:
                data = fh.read().replace(b"\r\n", b"\r").replace(b"\n", b"\r")
        except OSError:
            return ""
        text = data.decode("mac_roman", "replace")
        pos = f[1]
        rest = text[pos:]
        if count is not None:
            out = rest[:count]
        elif until is not None:
            u = {"return": "\r", "eof": None, "end": None}.get(str(until).lower(), str(until).replace("\n", "\r"))
            if u is None:
                out = rest
            else:
                k = rest.find(u)
                out = rest if k < 0 else rest[:k + len(u)]
        else:
            out = rest
        f[1] = pos + len(out)
        return out.replace("\r", "\n")

    def file_write(self, path, text):
        f = self._file(path)
        data = str(text).replace("\n", "\r").encode("mac_roman", "replace")
        mode = "r+b" if os.path.exists(f[0]) else "wb"
        with open(f[0], mode) as fh:
            fh.seek(f[1] if f[1] else 0)
            fh.write(data)
            if f[1] == 0:
                fh.truncate()
        f[1] += len(data)

    # -- resources -------------------------------------------------------------------------------
    def resources_of(self, st):
        key = id(st)
        r = self._rsrc_cache.get(key)
        if r is None:
            r = []
            path = os.path.join(getattr(st, "dir_path", "") or "", "_rsrc.bin")
            try:
                with open(path, "rb") as fh:
                    from .resources import parse_resource_fork
                    r = parse_resource_fork(fh.read())
            except OSError:
                src = self.stack_sources.get(id(st)) or (getattr(self, "source", None) if st is self.stack else None)
                if src and src[0].rsrc:
                    from .resources import parse_resource_fork
                    r = parse_resource_fork(src[0].rsrc)
            self._rsrc_cache[key] = r
        return r

    def resource_chain(self, only_current=False):
        sts = [self.stack] if only_current else [self.stack] + self.stacks_in_use()
        return [self.resources_of(st) for st in sts if st is not None]

    def picture_png(self, source, what):
        from . import pict
        if source.startswith("res"):
            low = str(what).lower()
            num = int(ht.to_num(what)) if ht.is_number(what) else None
            for res in self.resource_chain():
                for r in res:
                    if r[0] == "PICT" and ((num is not None and r[1] == num) or r[2].lower() == low):
                        return pict.to_png(r[3])
            return None
        p = mac_to_local(what)
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError:
            return None
        if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
            return data
        return pict.to_png(data[512:]) if len(data) > 522 else None

    # -- text helpers --------------------------------------------------------------------------------
    def text_width(self, text, font="Geneva", size=12):
        return int(len(text) * size * 0.55)

    def word_at(self, num, layer, loc):
        return ""

    def set_clipboard(self, text):
        self._clip = text

    def clipboard_text(self):
        return getattr(self, "_clip", "")

    def popup_menu(self, items, h, v):
        return None

    def list_select(self, prompt, lines, multiple=False):
        return None

    def play_file(self, path):
        pass

    # -- styled text -----------------------------------------------------------------------------------
    def _style_list(self, c, n, part):
        """Per-character style ids of a field's content."""
        out = [None] * n
        runs = sorted(c.runs or [])
        for k, (off, sid) in enumerate(runs):
            end = runs[k + 1][0] if k + 1 < len(runs) else n
            for i in range(max(0, off), min(end, n)):
                out[i] = sid
        return out

    def _style_id(self, font, size, style):
        st = self.stack
        key = (font, size, frozenset(style) if style is not None else None)
        for sid, d in st.styles.items():
            if (d.get("font"), d.get("size"), frozenset(d["style"]) if d.get("style") is not None else None) == key:
                return sid
        sid = max(list(st.styles) + [st.next_style_id - 1, 0]) + 1
        st.next_style_id = sid + 1
        st.styles[sid] = {"font": font, "size": size, "style": set(style) if style is not None else None}
        return sid

    def _restyle(self, ref, start, end, fn):
        """Apply fn(font, size, style) -> (font, size, style) to chars [start, end)."""
        if not (isinstance(ref, Obj) and ref.t == "part"):
            return
        p = ref.b
        c = self.content_of(ref.a, p, ref.c, card=ref.d)
        if c is None or not c.text:
            return
        n = len(c.text)
        start, end = max(0, start), min(n, end if end is not None else n)
        ids = self._style_list(c, n, p)
        cache = {}
        for i in range(start, end):
            sid = ids[i]
            if sid in cache:
                ids[i] = cache[sid]
                continue
            d = self.stack.styles.get(sid, {}) if sid is not None else {}
            font = d.get("font") if d.get("font") is not None else p.text_font_id
            size = d.get("size") if d.get("size") is not None else p.text_size
            style = d.get("style") if d.get("style") is not None else set(p.text_style)
            nf, ns, nst = fn(font, size, set(style))
            new = self._style_id(nf, ns, nst)
            cache[sid] = new
            ids[i] = new
        runs = []
        prev = object()
        for i, sid in enumerate(ids):
            if sid != prev:
                if sid is not None:
                    runs.append((i, sid))
                prev = sid
        c.runs = runs
        self.changed(ref)

    def map_field_sizes(self, ref, pairs):
        if isinstance(ref, Obj) and ref.t == "part" and ref.b.text_size in pairs:
            ref.b.text_size = pairs[ref.b.text_size]
        self._restyle(ref, 0, None, lambda f, s, st: (f, pairs.get(s, s), st))

    def remove_field_style(self, ref, style, s0=None, s1=None):
        start = (s0 - 1) if s0 else 0
        end = s1 if s1 else None
        self._restyle(ref, start, end, lambda f, s, st: (f, s, st - {style}))

    def chunk_prop(self, base, ref, prop):
        it = self.interp
        _, start, end = it.chunk_range(ref)
        p = base.b
        c = self.content_of(base.a, p, base.c, card=base.d)
        d = {}
        if c is not None and c.runs and c.text:
            ids = self._style_list(c, len(c.text), p)
            if start < len(ids) and ids[start] is not None:
                d = self.stack.styles.get(ids[start], {})
        if prop == "textsize":
            return str(d.get("size") or p.text_size)
        if prop == "textfont":
            return self.font_name(d["font"]) if d.get("font") is not None else self.font_name(p.text_font_id)
        if prop == "textstyle":
            stl = d.get("style") if d.get("style") is not None else p.text_style
            return ",".join(sorted(stl)) or "plain"
        if prop == "textheight":
            return str(p.text_height)
        return it.get_prop(prop, base)

    def set_chunk_prop(self, base, ref, prop, value):
        it = self.interp
        _, start, end = it.chunk_range(ref)
        if prop == "textsize":
            n = int(ht.to_num(value))
            self._restyle(base, start, end, lambda f, s, st: (f, n, st))
        elif prop == "textfont":
            fid = self.font_id(value)
            self._restyle(base, start, end, lambda f, s, st: (fid, s, st))
        elif prop == "textstyle":
            new = {x.strip().lower() for x in str(value).split(",") if x.strip().lower() not in ("plain", "")}
            self._restyle(base, start, end, lambda f, s, st: (f, s, set(new)))

    # -- script windows (palettes, text windows, picture windows) ------------------------------------
    def _win(self, name):
        return self.windows.get(str(name).strip().lower())

    def window_create(self, name, kind, props):
        old = self._win(name)
        if old is not None:
            self.window_close(name)
        st = self.stack
        w = {"name": str(name), "kind": kind, "visible": False, "rect": None, "text": "",
             "textfont": "Geneva", "textsize": "12", "alternatetext": "", "savefilename": "",
             "saverezname": "", "scroll": "0,0", "dontwrap": "false"}
        w.update(props)
        if w["rect"] is None:
            if kind == "picture" and w.get("png"):
                import struct
                pw, ph = struct.unpack(">II", w["png"][16:24])
                w["rect"] = (40, 40, 40 + pw, 40 + ph)
            else:
                w["rect"] = (60, 60, 60 + 300, 60 + 200)
        self.windows[str(name).strip().lower()] = w
        self.window_ui(w, "create")

    def window_close(self, name):
        w = self.windows.pop(str(name).strip().lower(), None)
        if w is not None:
            self.window_ui(w, "close")
            if w["kind"] == "picture":
                self.dispatch("closePicture", None, [w["name"]])

    def has_window(self, name):
        low = str(name).strip().lower()
        if low in ("card", "card window", "cd window", "message", "msg", "message box", "message window"):
            return True
        return low in self.windows

    def window_visible(self, name, show):
        w = self._win(name)
        if w is not None:
            w["visible"] = bool(show)
            self.window_ui(w, "update")

    def window_ui(self, w, what):
        pass

    def window_prop(self, name, prop):
        low = str(name).strip().lower()
        w = self._win(name)
        if w is not None:
            r = w["rect"]
            if prop in ("rect", "rectangle"):
                return "%d,%d,%d,%d" % r
            if prop in ("loc", "location", "topleft", "globalloc"):
                return "%d,%d" % (r[0], r[1])
            if prop in ("left", "top", "right", "bottom"):
                return str(r[("left", "top", "right", "bottom").index(prop)])
            if prop == "width":
                return str(r[2] - r[0])
            if prop == "height":
                return str(r[3] - r[1])
            if prop in ("botright", "bottomright"):
                return "%d,%d" % (r[2], r[3])
            if prop == "visible":
                return ht.b(w["visible"])
            if prop == "name":
                return w["name"]
            if prop == "titlewidth":
                return str(self.text_width(w["name"], "Chicago", 12) + 30)
            if prop == "maxlinewidth":
                lines = str(w.get("text", "")).split("\n") or [""]
                size = int(ht.to_num(w.get("textsize") or 12))
                return str(max(self.text_width(x, w.get("textfont", "Geneva"), size) for x in lines) + 16)
            if prop in ("textselection",):
                return w.get("textselection", "0,0")
            v = w.get(prop)
            if isinstance(v, bool):
                return ht.b(v)
            return "" if v is None else str(v)
        if low in ("card", "card window", "cd window"):
            st = self.stack
            r = (0, 0, st.width, st.height)
            if prop in ("rect", "rectangle"):
                return "%d,%d,%d,%d" % r
            if prop in ("loc", "location", "topleft"):
                return "0,0"
            if prop in ("left", "top"):
                return "0"
            if prop in ("right", "width"):
                return str(st.width)
            if prop in ("bottom", "height"):
                return str(st.height)
            if prop in ("bottomright", "botright"):
                return "%d,%d" % (r[2], r[3])
            if prop == "visible":
                return ht.TRUE
            if prop == "name":
                return "card window"
            return ""
        if prop == "visible":
            return ht.FALSE
        return ""

    def set_window_prop(self, name, prop, value):
        w = self._win(name)
        if w is None:
            return
        value = str(value)
        if prop in ("rect", "rectangle"):
            v = [int(round(ht.to_num(x))) for x in value.split(",")[:4]]
            if len(v) == 4:
                w["rect"] = tuple(v)
        elif prop in ("loc", "location", "topleft", "globalloc"):
            v = [int(round(ht.to_num(x))) for x in value.split(",")[:2]]
            if len(v) == 2:
                r = w["rect"]
                w["rect"] = (v[0], v[1], v[0] + r[2] - r[0], v[1] + r[3] - r[1])
        elif prop == "visible":
            w["visible"] = ht.to_bool(value)
        elif prop == "name":
            self.windows.pop(w["name"].lower(), None)
            w["name"] = value
            self.windows[value.lower()] = w
        else:
            w[prop] = value
        self.window_ui(w, "update")

    def window_message(self, name, msg, params):
        """Messages scripts send to external windows."""
        w = self._win(name)
        if w is None:
            return
        m = msg.lower()
        if m == "readrez":
            from .xcmds import twtx_text
            key = w.get("saverezname") or w["name"]
            for res in self.resource_chain():
                r = next((r for r in res if r[0] in ("twtx", "TEXT") and r[2].lower() == str(key).lower()), None)
                if r is not None:
                    w["text"] = twtx_text(r[3]) if r[0] == "twtx" else r[3].decode("mac_roman", "replace").replace("\r", "\n")
                    break
        elif m == "readsavedfile":
            try:
                with open(mac_to_local(w.get("savefilename", "")), "rb") as fh:
                    data = fh.read().decode("mac_roman", "replace").replace("\r", "\n")
                lines = data.split("\n")
                # first line is the alternate text, then the text (HCX's own format)
                if lines and lines[0].startswith("\x01"):
                    w["alternatetext"] = lines[0][1:].replace("\x02", "\n")
                    data = "\n".join(lines[1:])
                w["text"] = data
            except OSError:
                pass
        elif m in ("savefile", "save"):
            try:
                p = mac_to_local(w.get("savefilename", ""))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    head = "\x01" + str(w.get("alternatetext", "")).replace("\n", "\x02") + "\n"
                    fh.write((head + str(w.get("text", ""))).replace("\n", "\r").encode("mac_roman", "replace"))
            except OSError:
                pass
        elif m == "close":
            self.window_close(name)
            return
        self.window_ui(w, "update")

    def window_click(self, name, char, line):
        """A click in a text window: textWindowClick char, line, window."""
        self.dispatch("textWindowClick", None, [str(char), str(line), name])

    def picture_click(self, name, x, y, card=None):
        if card is not None:
            self._click = card
        self.dispatch("mouseDownInPicture", None, [name, "%d,%d" % (x, y)])
