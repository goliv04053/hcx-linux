"""Main window: header bar with navigation, card area, tool bar and the
inspector sidebar."""

import os
import shutil
import threading
import traceback

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango

from . import addcolor, macfiles, macsound
from .cardview import CardView
from . import i18n
from .i18n import N_, _
from .inspector import Inspector
from . import scriptui
from .hthost import HostMixin
from . import hypertalk as ht
from .hypertalk import Obj
from .model import Content, read_xml, write_xml
from .stackimport import import_stack

ZOOMS = [1.0, 1.5, 2.0, 3.0, 4.0]
PROGRESS_LABELS = {"resources": N_("Reading resources…"), "bg": N_("Reading backgrounds…"),
                   "card": N_("Reading cards…"), "xml": N_("Writing package…"), "done": N_("Loading…")}


_MEDIA_OK = []


def _media_ok():
    """Can Gtk.MediaFile play sounds here? With GTK's GStreamer backend it needs the
    playbin element (gst-plugins-base); without it GTK stops the whole program."""
    if not _MEDIA_OK:
        ok = True
        try:
            gi_ = __import__("gi")
            gi_.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            ok = Gst.ElementFactory.find("playbin") is not None
        except (ValueError, ImportError):
            import glob
            dirs = glob.glob("/usr/lib*/gstreamer-1.0") + glob.glob("/usr/lib/*-linux-gnu*/gstreamer-1.0")
            gst_backend = glob.glob("/usr/lib*/gtk-4.0/4.0.0/media/libmedia-gstreamer.so") + \
                glob.glob("/usr/lib/*-linux-gnu*/gtk-4.0/4.0.0/media/libmedia-gstreamer.so")
            if gst_backend and dirs:
                ok = any(os.path.exists(os.path.join(d, "libgstplayback.so")) for d in dirs)
        _MEDIA_OK.append(ok)
    return _MEDIA_OK[0]


def _sound_from_data(path):
    """Packages imported by older versions kept compressed (MACE/IMA) sounds as raw
    'snd ' data: convert one to WAV next to it the first time it is played."""
    wav = os.path.join(os.path.dirname(path), "SND_" + os.path.basename(path)[4:-5] + ".wav")
    if os.path.exists(wav):
        return wav
    try:
        from .resources import snd_wav
        with open(path, "rb") as fh:
            blob = snd_wav(fh.read())
        if not blob:
            return path
        with open(wav, "wb") as fh:
            fh.write(blob)
        return wav
    except Exception:  # noqa: BLE001 - a bad sound must not stop the script
        return path


class HCXWindow(Adw.ApplicationWindow, HostMixin):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(820, 640)
        self.set_title("HCX")
        self.stack = None
        self.index = 0
        self.history = []
        self.source = None          # (MacFile, path) to reimport
        self.encoding = "auto"
        self._told_script = False
        self._told_modified = False
        self._loading = False
        self.dirty = False
        self.init_host()
        GLib.timeout_add(300, self._idle_tick)
        self._in_open_card = False
        self._pending_effect = None
        self._script_wins = {}
        self._script_dirty = {}
        self._script_t0 = None
        self._stop_hint_shown = False
        self._abort_script = False
        self._menu_gen = 0
        self._menus_queued = False
        self._sounds = []
        self._search_pos = None

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        self.toasts.set_child(tv)
        self.set_content(self.toasts)

        # header -----------------------------------------------------------------
        hb = Adw.HeaderBar()
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open Stack…"))
        open_btn.set_action_name("win.open")
        hb.pack_start(open_btn)
        nav = Gtk.Box(css_classes=["linked"])
        for icon, act, tip in (("go-first-symbolic", "win.first", _("First Card")),
                               ("go-previous-symbolic", "win.prev", _("Previous Card")),
                               ("go-next-symbolic", "win.next", _("Next Card")),
                               ("go-last-symbolic", "win.last", _("Last Card"))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.set_action_name(act)
            nav.append(b)
        hb.pack_start(nav)
        self.menubox = Gtk.Box(spacing=2)
        hb.pack_start(self.menubox)
        self.wtitle = Adw.WindowTitle(title="HCX")
        hb.set_title_widget(self.wtitle)

        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append(_("Open Stack…"), "win.open")
        s1.append(_("New Window"), "app.new-window")
        s1.append(_("Save Changes"), "win.save")
        menu.append_section(None, s1)
        s15 = Gio.Menu()
        s15.append(_("Find…"), "win.find")
        s15.append(_("Print Card…"), "win.print")
        s15.append(_("Copy Card as Image"), "win.copy-image")
        s15.append(_("Export Card as PNG…"), "win.export-png")
        menu.append_section(None, s15)
        s2 = Gio.Menu()
        enc = Gio.Menu()
        enc.append(_("Automatic"), "win.encoding::auto")
        enc.append(_("Western (Mac Roman)"), "win.encoding::mac_roman")
        enc.append(_("Japanese (Shift-JIS)"), "win.encoding::shift_jis")
        s2.append_submenu(_("Text Encoding"), enc)
        s2.append(_("Reimport Stack"), "win.reimport")
        s2.append(_("Export Package…"), "win.export")
        menu.append_section(None, s2)
        langs = Gio.Menu()
        langs.append(_("System Default"), "app.language::")
        for code in i18n.available_languages():
            langs.append(i18n.language_name(code), "app.language::" + code)
        s25 = Gio.Menu()
        s25.append_submenu(_("Language"), langs)
        menu.append_section(None, s25)
        s3 = Gio.Menu()
        s3.append(_("About HCX"), "app.about")
        s3.append(_("Quit"), "app.quit")
        menu.append_section(None, s3)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=_("Menu"))
        mb.set_primary(True)
        hb.pack_end(mb)
        self.insp_btn = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic", tooltip_text=_("Inspector"))
        self.insp_btn.set_action_name("win.inspector")
        hb.pack_end(self.insp_btn)
        tv.add_top_bar(hb)

        # bottom tool bar -------------------------------------------------------------
        bar = Gtk.ActionBar()
        tools = Gtk.Box(css_classes=["linked"])
        for name, label in (("browse", _("Browse")), ("button", _("Buttons")), ("field", _("Fields"))):
            b = Gtk.ToggleButton(label=label)
            b.set_action_name("win.tool")
            b.set_action_target_value(GLib.Variant.new_string(name))
            tools.append(b)
        bar.pack_start(tools)
        addm = Gio.Menu()
        addm.append(_("New Button"), "win.new-button")
        addm.append(_("New Field"), "win.new-field")
        self.add_btn = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=addm,
                                      tooltip_text=_("New Button") + " / " + _("New Field"))
        bar.pack_start(self.add_btn)
        delb = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text=_("Delete"))
        delb.set_action_name("win.delete-part")
        bar.pack_start(delb)
        bgb = Gtk.ToggleButton(label=_("Edit Background"))
        bgb.set_action_name("win.bg-edit")
        bar.pack_start(bgb)
        zoom = Gtk.Box(css_classes=["linked"])
        zo = Gtk.Button(icon_name="zoom-out-symbolic", tooltip_text=_("Zoom") + " −")
        zo.set_action_name("win.zoom-out")
        self.zoom_lbl = Gtk.Button(label="100%", tooltip_text=_("Zoom") + " 100%")
        self.zoom_lbl.set_action_name("win.zoom-reset")
        zi = Gtk.Button(icon_name="zoom-in-symbolic", tooltip_text=_("Zoom") + " +")
        zi.set_action_name("win.zoom-in")
        for w in (zo, self.zoom_lbl, zi):
            zoom.append(w)
        bar.pack_end(zoom)
        self.bottom_bar = bar
        tv.add_bottom_bar(bar)
        self.toolbar_view = tv

        # content ------------------------------------------------------------------------
        self.view = CardView()
        self.view.more_stacks = lambda: list(getattr(self, "stacks_using", []))
        self.view.connect("message", self._on_message)
        self.view.busy = self.script_busy
        self.view.connect("selection-changed", lambda *_: self._sync_selection())
        self.view.connect("modified", lambda *_: self._on_modified())
        frame = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        frame.add_css_class("hcx-card")
        frame.set_margin_top(18)
        frame.set_margin_bottom(18)
        frame.set_margin_start(18)
        frame.set_margin_end(18)
        frame.append(self.view)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_child(frame)
        self.scroller.set_vexpand(True)
        self.card_overlay = Gtk.Overlay(child=self.scroller)
        self.card_overlay.connect("get-child-position", self._palette_position)

        welcome = Adw.StatusPage(icon_name="io.github.pgostation.HCX", title=_("Open a HyperCard Stack"),
                                 description=_("Drop a stack here, or open a stack file, a MacBinary/BinHex/AppleDouble file or an HFS disk image (.img, .dsk, .image)."))
        wb = Gtk.Button(label=_("Open Stack…"), halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        wb.set_action_name("win.open")
        welcome.set_child(wb)

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                          halign=Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner(width_request=32, height_request=32)
        self.load_title = Gtk.Label(css_classes=["title-2"])
        self.load_label = Gtk.Label(css_classes=["dim-label"])
        self.progress = Gtk.ProgressBar(width_request=280)
        for w in (self.spinner, self.load_title, self.load_label, self.progress):
            loading.append(w)

        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pages.add_named(welcome, "welcome")
        self.pages.add_named(loading, "loading")
        self.pages.add_named(self.card_overlay, "card")

        self.inspector = Inspector(self._on_inspector_change, self.play_sound)
        self.inspector.bind(self.view)
        self.split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END, show_sidebar=False)
        self.split.set_max_sidebar_width(400)
        self.split.set_min_sidebar_width(300)
        self.split.set_content(self.pages)
        self.split.set_sidebar(self.inspector)
        self.split.connect("notify::show-sidebar", self._sidebar_toggled)
        # search bar (HyperCard's Find)
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Find in all cards"), hexpand=True)
        self.search_entry.connect("activate", lambda e: self.find_next(e.get_text()))
        self.search_entry.connect("search-changed", lambda e: setattr(self, "_search_pos", None))
        self.search_entry.connect("stop-search", lambda e: self._close_search())
        sbox = Gtk.Box(spacing=6)
        sbox.append(self.search_entry)
        nb = Gtk.Button(icon_name="go-down-symbolic", tooltip_text=_("Find Next"))
        nb.connect("clicked", lambda b: self.find_next(self.search_entry.get_text()))
        sbox.append(nb)
        cl = Adw.Clamp(maximum_size=520, child=sbox)
        self.search_bar = Gtk.SearchBar(child=cl, show_close_button=True)
        self.search_bar.connect_entry(self.search_entry)
        tv.add_top_bar(self.search_bar)
        tv.set_content(self.split)
        self.connect("close-request", self._on_close_request)

        # drag and drop
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.toasts.add_controller(drop)

        # keys (capture, so arrows work even when a header button has focus)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self._install_actions()
        self._update_actions()

    # -- actions -----------------------------------------------------------------------
    def _install_actions(self):
        def add(name, cb, param=None, state=None):
            if state is not None:
                a = Gio.SimpleAction.new_stateful(name, param, state)
                a.connect("change-state", lambda *x: None if self.script_busy() else cb(*x))
            else:
                a = Gio.SimpleAction.new(name, param)
                a.connect("activate", lambda *x: None if self.script_busy() else cb(*x))
            self.add_action(a)
            return a

        add("open", lambda *a: self.choose_stack_file())
        add("close", lambda *a: self.close())
        add("first", lambda *a: self.go(0))
        add("prev", lambda *a: self.go(self.index - 1))
        add("next", lambda *a: self.go(self.index + 1))
        add("last", lambda *a: self.go(len(self.stack.cards) - 1 if self.stack else 0))
        add("back", lambda *a: self.go_back())
        add("zoom-in", lambda *a: self.step_zoom(1))
        add("zoom-out", lambda *a: self.step_zoom(-1))
        add("zoom-reset", lambda *a: self.set_zoom(1.0))
        add("new-button", lambda *a: self._new_part("button"))
        add("new-field", lambda *a: self._new_part("field"))
        add("delete-part", lambda *a: self.view.delete_selected())
        add("export", lambda *a: self.export_package())
        add("save", lambda *a: self.save())
        add("find", lambda *a: self.open_search())
        add("find-next", lambda *a: self.find_next(self.search_entry.get_text()))
        add("print", lambda *a: self.print_card())
        add("copy-image", lambda *a: self.copy_image())
        add("export-png", lambda *a: self.export_png())
        add("reimport", lambda *a: self.reimport(self.encoding))
        add("tool", self._on_tool, GLib.VariantType.new("s"), GLib.Variant.new_string("browse"))
        add("bg-edit", self._on_bg_edit, None, GLib.Variant.new_boolean(False))
        add("inspector", self._on_inspector, None, GLib.Variant.new_boolean(False))
        add("encoding", self._on_encoding, GLib.VariantType.new("s"), GLib.Variant.new_string("auto"))

    def _update_actions(self):
        has = self.stack is not None and not self._loading
        n = len(self.stack.cards) if self.stack else 0
        for name in ("first", "prev", "next", "last", "zoom-in", "zoom-out", "zoom-reset", "tool",
                     "bg-edit", "new-button", "new-field", "export", "inspector", "find", "find-next",
                     "print", "copy-image", "export-png"):
            self.lookup_action(name).set_enabled(has and (n > 0 or name in ("export", "inspector")))
        self.lookup_action("back").set_enabled(has and bool(self.history))
        self.lookup_action("save").set_enabled(has and self.dirty)
        self.add_btn.set_sensitive(has and n > 0)
        self.toolbar_view.set_reveal_bottom_bars(self.stack is not None)
        self.lookup_action("delete-part").set_enabled(has and self.view.selected is not None)
        self.lookup_action("reimport").set_enabled(self.source is not None and not self._loading)
        self.lookup_action("encoding").set_enabled(self.source is not None and not self._loading)

    def _on_tool(self, action, value):
        action.set_state(value)
        self.view.set_tool(value.get_string())
        self._update_actions()

    def _on_bg_edit(self, action, value):
        action.set_state(value)
        self.view.set_bg_only(value.get_boolean())
        self._update_actions()

    def _on_inspector(self, action, value):
        action.set_state(value)
        self.split.set_show_sidebar(value.get_boolean())
        if value.get_boolean():
            self.inspector.refresh()

    def _sidebar_toggled(self, split, _p):
        a = self.lookup_action("inspector")
        if a.get_state().get_boolean() != split.get_show_sidebar():
            a.set_state(GLib.Variant.new_boolean(split.get_show_sidebar()))

    def _on_encoding(self, action, value):
        action.set_state(value)
        enc = value.get_string()
        if enc != self.encoding:
            self.encoding = enc
            self.reimport(enc)

    def _new_part(self, ptype):
        self.lookup_action("tool").change_state(GLib.Variant.new_string(ptype))
        self.view.new_part(ptype)
        self._update_actions()

    def set_tool_browse(self):
        self.lookup_action("tool").change_state(GLib.Variant.new_string("browse"))

    # -- keyboard -------------------------------------------------------------------------
    def _on_key(self, ctl, keyval, keycode, state):
        if self.script_busy():
            if keyval == Gdk.KEY_Escape or (keyval == Gdk.KEY_period and state & Gdk.ModifierType.CONTROL_MASK):
                self._abort_script = True
            return True
        if not self.stack or self._loading:
            return False
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.TextView, Gtk.Editable)) or self.view.editing:
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        tool = self.view.tool
        if tool != "browse" and self.view.selected:
            d = 10 if state & Gdk.ModifierType.SHIFT_MASK else 1
            moves = {Gdk.KEY_Left: (-d, 0), Gdk.KEY_Right: (d, 0), Gdk.KEY_Up: (0, -d), Gdk.KEY_Down: (0, d)}
            if keyval in moves:
                self.view.nudge(*moves[keyval])
                self._on_modified()
                return True
            if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
                self.view.delete_selected()
                return True
        if keyval == Gdk.KEY_Escape and tool != "browse":
            self.set_tool_browse()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Page_Up):
            self.go(self.index - 1)
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_Page_Down, Gdk.KEY_space):
            self.go(self.index + 1)
            return True
        if keyval == Gdk.KEY_Home:
            self.go(0)
            return True
        if keyval == Gdk.KEY_End:
            self.go(len(self.stack.cards) - 1)
            return True
        if keyval == Gdk.KEY_BackSpace or keyval == Gdk.KEY_asciitilde:
            self.go_back()
            return True
        return False

    # -- navigation ----------------------------------------------------------------------------
    def go(self, index, record=True, effect=None, messages=True):
        if not self.stack or not self.stack.cards:
            return
        n = len(self.stack.cards)
        index %= n  # HyperCard wraps around
        old_card = self.card if self.index >= 0 else None
        new_card = self.stack.cards[index]
        if messages and old_card is not None and old_card is not new_card:
            self.dispatch("closeCard")
            if self.bg_of(old_card) is not self.bg_of(new_card):
                self.dispatch("closeBackground")
            if self.stack is None or new_card not in self.stack.cards:
                return
            index = self.stack.cards.index(new_card)
        if record and index != self.index and self.index >= 0 and \
                self.interp.props.get("lockrecent") != "true":
            self.history.append(self.index)
            del self.history[:-200]
            self.forth = []
        self.index = index
        if old_card is not new_card:
            self.addcolor_state["extra"] = []     # colors drawn by AddColor commands last until the card changes
        if index != getattr(self, "_found_index", None):
            self.view.found = None
        if self.view.locked is not None and effect:
            self._pending_effect = effect
        self.view.set_card(self.stack.cards[index], effect)
        self._update_title()
        self._update_actions()
        if self.split.get_show_sidebar():
            self.inspector.refresh()
        if messages and not self._in_open_card:
            self._in_open_card = True
            try:
                if old_card is not None and self.bg_of(old_card) is not self.bg_of(new_card):
                    self.dispatch("openBackground")
                self.dispatch("openCard")
            finally:
                self._in_open_card = False

    def go_back(self, effect=None):
        if self.history:
            self.forth = getattr(self, "forth", []) + [self.index]
            self.go(self.history.pop(), record=False, effect=effect)

    def go_forth(self, effect=None):
        forth = getattr(self, "forth", [])
        if forth:
            self.history.append(self.index)
            self.go(forth.pop(), record=False, effect=effect)

    def _update_title(self):
        st = self.stack
        card = st.cards[self.index]
        sub = _("Card %d of %d") % (self.index + 1, len(st.cards))
        if card.name:
            sub += " — " + card.name
        if self.dirty:
            sub = "• " + sub
        self.wtitle.set_title(st.name)
        self.wtitle.set_subtitle(sub)
        self.set_title("%s — HCX" % st.name)

    # -- HyperTalk host (see hthost.HostMixin) --------------------------------------------------
    @property
    def card(self):
        if self.stack and 0 <= self.index < len(self.stack.cards):
            return self.stack.cards[self.index]
        return None

    def bg_of(self, card):
        return self.stack.bg_by_id(card.bg_id) if card and self.stack else None

    def _on_message(self, view, msg, target):
        if self.script_busy():
            return
        view.pending_click = False      # this click is the one being handled, not a later mouseClick
        if view.click:
            cx, cy, kind, p, layer = view.click
            self._click = (cx, cy)
            self._click_text = ""
            self._click_chunk = None
            if p is not None and p.type == "field":
                i = view.char_at(kind, p, layer, cx, cy)
                c = self.content_of(kind, p, layer)
                if i is not None and c is not None and c.text:
                    import re as _re
                    for m in _re.finditer(r"[^\s]+", c.text):
                        if m.start() <= i < m.end():
                            self._click_text = m.group(0)
                            self._click_chunk = (kind, p, layer, m.start(), m.end())
                            break
                    ls = c.text.rfind("\n", 0, i) + 1
                    self._click_line = (kind, p, layer, c.text[:ls].count("\n") + 1)
        obj = Obj("part", target[1], target[2], target[3], self.card) if target else None
        self.dispatch(msg, obj)
        self._update_actions()

    def click_chunk(self, which):
        if which == "clickline":
            cl = getattr(self, "_click_line", None)
            if cl and cl[0:3] == (self._click_chunk or cl)[0:3] or cl:
                kind, p, layer, n = cl
                return "line %d of %s" % (n, self._field_desc(kind, p, layer))
            return ""
        cc = getattr(self, "_click_chunk", None)
        if not cc:
            return ""
        kind, p, layer, s0, s1 = cc
        return "char %d to %d of %s" % (s0 + 1, s1, self._field_desc(kind, p, layer))

    def _field_desc(self, kind, p, layer):
        num = [q for q in layer.parts if q.type == "field"].index(p) + 1
        return "%s field %d" % ("bkgnd" if kind == "bg" else "card", num)

    def go_index(self, index, effect=None):
        self.go(index, effect=effect)

    def show_stack_now(self, st, go_first=True):
        if st is self.stack:
            return
        self.dispatch("closeCard")
        self.dispatch("closeBackground")
        self.dispatch("closeStack")
        if self.stack is not None and self.stack not in self.stacks_using:
            self._stack_cache[self.stack.name.lower()] = self.stack
        src = self.stack_sources.get(id(st))
        self._show_stack(st, (src[0], src[1], False) if src else None, go_first=go_first, from_script=True)

    def push_card(self):
        HostMixin.push_card(self)

    def refresh(self):
        # changes made by scripts are not treated as user edits
        self.view.refresh()

    def color_refresh(self, effect=None):
        if effect and self.view.locked is None:
            self.view.lock()
            self.view.unlock(effect)
        else:
            self.view.refresh()

    def changed(self, obj):
        # HyperCard saves what scripts change; HCX does it when the stack closes
        if self.stack is not None:
            self._script_dirty[id(self.stack)] = self.stack
        self.view.refresh()
        if self.split.get_show_sidebar():
            self.inspector.refresh()

    def set_hilite(self, kind, part, value, card=None):
        HostMixin.set_hilite(self, kind, part, value, card)
        self.view.refresh()

    def field_scroll(self, kind, part):
        return int(self.view.scroll.get((kind, part.id), 0))

    def set_field_scroll(self, kind, part, value):
        self.view.scroll[(kind, part.id)] = max(0, int(value))
        self.view.refresh()

    def lock_screen(self, on, effect=None):
        if on:
            self.view.lock()
        else:
            eff = effect or self._pending_effect
            self._pending_effect = None
            self.view.unlock(eff)

    def after_script(self):
        HostMixin.after_script(self)
        self._script_t0 = None
        self._stop_hint_shown = False
        self._abort_script = False
        if self.view.locked is not None:
            self.lock_screen(False)

    def beep(self):
        Gdk.Display.get_default().beep()

    def play_sound(self, name):
        st = self.stack
        if not st:
            return
        low = str(name).lower()
        path = None
        for s_ in [st] + self.stacks_in_use():
            for m in s_.media:
                if m["type"] != "sound" or not m.get("file"):
                    continue
                if m["name"].lower() == low or str(m["id"]) == low:
                    path = os.path.join(s_.dir_path, m["file"])
                    if path.endswith(".data"):
                        path = _sound_from_data(path)
                    break
            if path:
                break
        if not path or not path.endswith(".wav") or not os.path.exists(path):
            if low not in ("boing", "harpsichord", "silence"):
                self.beep()
            return
        self.play_file(path)

    def play_file(self, path):
        path = macsound.playable(path)
        if not _media_ok():
            # GTK's GStreamer backend aborts the program when GStreamer's playback plugins are missing
            for tool in ("pw-play", "paplay", "aplay"):
                exe = shutil.which(tool)
                if exe:
                    import subprocess
                    subprocess.Popen([exe, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
            self.beep()
            return
        media = Gtk.MediaFile.new_for_filename(path)
        self._sounds = [m for m in self._sounds if not m.get_ended()][-4:] + [media]
        media.play()

    def stop_sound(self):
        for m in self._sounds:
            m.pause()
        self._sounds = []

    def sound_state(self):
        return "done" if all(m.get_ended() for m in self._sounds) else "playing"

    def _idle_tick(self):
        """HyperCard sends `idle` to the current card whenever nothing else runs."""
        if self.get_application() is None:
            return False
        st = self.stack
        if st is None or self.interp.depth > 0 or not self.is_active() or self.view.tool != "browse" \
                or getattr(self, "_idle_off", None) is st:
            return True
        t = GLib.get_monotonic_time()
        self.dispatch("idle")
        if GLib.get_monotonic_time() - t > 200000:
            self._idle_off = st     # a slow idle handler would make the window sluggish
        return True

    # -- a script is running ---------------------------------------------------------------------
    # The window keeps drawing and taking clicks while a script runs (games loop on `the mouseClick`),
    # but clicks and keys don't start other handlers. Esc or Ctrl+. stops the script, as Cmd-. did.
    max_steps = 0        # no step limit: the user can stop a runaway script

    def script_busy(self):
        return self.interp.depth > 0

    def _pump(self, n=20):
        ctx = GLib.MainContext.default()
        for _ in range(n):
            if not ctx.iteration(False):
                break
        if self._abort_script:
            self._abort_script = False
            raise ht._Exit("hypercard")

    def poll(self):
        now = GLib.get_monotonic_time()
        if self._script_t0 is None:
            self._script_t0 = now
        elif not self._stop_hint_shown and now - self._script_t0 > 4_000_000:
            self._stop_hint_shown = True
            self.toast(_("A script is running. Press Esc to stop it."), timeout=5)
        self._pump()

    def mouse_is_down(self):
        # Scripts poll "the mouse" in loops: let GTK deliver the button release.
        self._pump()
        return HostMixin.mouse_is_down(self)

    def mouse_clicked(self):
        self._pump()
        clicked = self.view.pending_click
        self.view.pending_click = False
        if clicked:
            self._click = self.view.script_click
        return clicked

    def mouse_loc(self):
        xy = getattr(self.view, "_last_xy", None)
        if not xy or not self.view.card:
            return self._click
        cx, cy = self.view.to_card(*xy)
        return int(cx), int(cy)

    def wait(self, secs):
        end = GLib.get_monotonic_time() + int(secs * 1e6)
        ctx = GLib.MainContext.default()
        while GLib.get_monotonic_time() < end:
            ctx.iteration(False)
            if self._abort_script:
                self._abort_script = False
                raise ht._Exit("hypercard")
            GLib.usleep(10000)

    def answer(self, text, buttons):
        r = scriptui.answer(self, text, buttons)
        self._script_t0 = None      # time spent in a dialog doesn't count as a busy script
        return r

    def ask(self, prompt, default="", password=False):
        r = scriptui.ask(self, prompt, default, password)
        self._script_t0 = None
        return r

    def list_select(self, prompt, lines, multiple=False):
        return scriptui.list_select(self, prompt, lines, multiple)

    def choose_file(self, kind, prompt="", default="", patterns=None):
        """File dialog for scripts: answer file, ask file and the file externals.
        Starts in HCX's files folder and answers with a Mac-style path."""
        from .hthost import files_root, local_to_mac
        folder = os.path.join(files_root(), "HD")
        r = scriptui.choose_file(self, "save" if kind == "save" else ("folder" if kind == "folder" else "file"),
                                 prompt, default, folder, patterns)
        self._script_t0 = None
        return local_to_mac(r) if r else None

    def ditl_dialog(self, spec, on_click=None):
        r = scriptui.ditl_dialog(self, spec, on_click)
        self._script_t0 = None
        return r

    def popup_menu(self, items, h, v):
        return scriptui.popup_menu(self.view, items, h, v)

    def show_msg_text(self, text):
        if str(text).strip():
            self.toast(str(text).split("\n")[0][:200], timeout=3)

    def set_cursor(self, name):
        low = str(name).lower()
        cur = {"busy": "wait", "watch": "wait", "hand": "pointer", "arrow": "default", "ibeam": "text",
               "cross": "crosshair", "plus": "cell", "none": "none"}.get(low)
        self.view.set_cursor_from_name(cur or "default")

    def set_clipboard(self, text):
        self._clip = text
        self.get_clipboard().set(str(text))

    def screen_rect(self):
        mon = None
        try:
            surf = self.get_surface()
            mon = Gdk.Display.get_default().get_monitor_at_surface(surf) if surf else None
        except Exception:  # noqa: BLE001
            mon = None
        if mon is None:
            return "0,0,1920,1080"
        g = mon.get_geometry()
        return "%d,%d,%d,%d" % (g.x, g.y, g.x + g.width, g.y + g.height)

    def text_width(self, text, font="Geneva", size=12):
        from .render import linux_family
        lay = self.create_pango_layout(str(text))
        fd = Pango.FontDescription.from_string("%s %dpx" % (linux_family(font, size), size))
        lay.set_font_description(fd)
        return lay.get_pixel_size()[0]

    def type_text(self, text):
        ed = getattr(self.view, "editing", None)
        tv = ed[3] if ed and len(ed) > 3 and isinstance(ed[3], Gtk.TextView) else None
        if tv is not None and text:
            tv.get_buffer().insert_at_cursor(str(text).replace("\r", "\n"))

    def close_field(self):
        if getattr(self.view, "editing", None):
            self.view.finish_editing()

    def selection_text(self):
        ed = getattr(self.view, "editing", None)
        tv = ed[3] if ed and len(ed) > 3 and isinstance(ed[3], Gtk.TextView) else None
        if tv is not None:
            buf = tv.get_buffer()
            b = buf.get_selection_bounds()
            if b:
                return buf.get_text(b[0], b[1], False)
        return ""

    def builtin_menu(self, low):
        low = low.rstrip("…").rstrip(".").strip()
        acts = {"next": "next", "next card": "next", "prev": "prev", "previous": "prev",
                "previous card": "prev", "first": "first", "first card": "first", "last": "last",
                "last card": "last", "back": "back", "recent": "back", "find": "find",
                "print card": "print", "open stack": "open", "copy card": "copy-image",
                "save a copy": "export", "browse": None}
        a = acts.get(low)
        if a:
            self.activate_action("win." + a, None)
            return True
        if low == "home":
            self.go_home()
            return True
        if low in ("quit hypercard", "quit", "close stack"):
            # scripts don't close the window; the user decides
            return True
        if low == "copy":
            self.set_clipboard(self.selection_text())
            return True
        return False

    def show_found(self, kind, part, layer, span):
        if kind is None:
            self.view.found = None
        else:
            self._found_index = self.index
            self.view.found = ((kind, part.id), span[0], span[1])
            c = self.content_of(kind, part, layer)
            if c is not None:
                self._scroll_to_found((kind, part.id), c.text, span[0])
        self.view.refresh()

    def find_ui(self, text):
        self.search_bar.set_search_mode(True)
        self.search_entry.set_text(text)

    # -- script menus (header bar) -------------------------------------------------------------
    def menus_changed(self):
        if not self._menus_queued:
            self._menus_queued = True
            GLib.idle_add(self._rebuild_menus)

    def _rebuild_menus(self):
        self._menus_queued = False
        child = self.menubox.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.menubox.remove(child)
            child = nxt
        for name in getattr(self, "_menu_actions", []):
            self.remove_action(name)
        self._menu_actions = []
        self._menu_gen += 1
        g = self._menu_gen
        for mi, m in enumerate(self.script_menus):
            if m[3] or not m[1]:
                continue        # HyperCard's own menus are in the main menu
            model = Gio.Menu()
            section = Gio.Menu()
            for ii, item in enumerate(m[1]):
                if item[0].startswith("-") or item[0] == "":
                    if section.get_n_items():
                        model.append_section(None, section)
                    section = Gio.Menu()
                    continue
                an = "sm%d_%d_%d" % (g, mi, ii)
                if item[3]:
                    act = Gio.SimpleAction.new_stateful(an, None, GLib.Variant.new_boolean(True))
                else:
                    act = Gio.SimpleAction.new(an, None)
                act.set_enabled(bool(item[2]) and bool(m[2]))
                act.connect("activate", lambda *x: None if self.script_busy() else self._script_menu_item(*x),
                            m[0], item[0])
                self.add_action(act)
                self._menu_actions.append(an)
                section.append(item[0], "win." + an)
            if section.get_n_items():
                model.append_section(None, section)
            btn = Gtk.MenuButton(label=m[0], menu_model=model, css_classes=["flat"])
            btn.set_sensitive(bool(m[2]))
            self.menubox.append(btn)
        return False

    def _script_menu_item(self, action, param, menu, item):
        GLib.idle_add(lambda: (self.dispatch("doMenu", None, [item, menu]), self._update_actions(), False)[2])

    # -- script windows -----------------------------------------------------------------------
    def window_ui(self, w, what):
        key = id(w)
        sw = self._script_wins.get(key)
        if what == "close":
            if sw is not None:
                self.card_overlay.remove_overlay(sw)
                del self._script_wins[key]
            return
        if sw is None:
            sw = scriptui.ScriptWindow(self, w)
            self._script_wins[key] = sw
            self.card_overlay.add_overlay(sw)
            self.card_overlay.set_measure_overlay(sw, False)
        else:
            sw.update()

    def place_script_window(self, sw):
        self.card_overlay.queue_allocate()

    def _palette_position(self, overlay, widget, rect):
        if not isinstance(widget, scriptui.ScriptWindow):
            return False
        ok, pt = self.view.compute_point(overlay, Graphene.Point().init(0, 0))
        ox, oy = (pt.x, pt.y) if ok else (0, 0)
        z = self.view.zoom
        l, t, r, b = widget.w["rect"]
        _, nat = widget.get_preferred_size()
        W, H = overlay.get_width(), overlay.get_height()
        x = int(ox + l * z)
        y = int(oy + t * z) - 14
        x = max(0, min(x, W - nat.width))
        y = max(0, min(y, H - nat.height))
        rect.x, rect.y, rect.width, rect.height = x, y, nat.width, nat.height
        return True

    def _close_script_windows(self):
        for name in list(self.windows):
            w = self.windows.pop(name)
            self.window_ui(w, "close")

    # -- find -------------------------------------------------------------------------------------
    def open_search(self):
        if not self.stack:
            return
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    def _close_search(self):
        self.search_bar.set_search_mode(False)
        self.view.found = None
        self.view.refresh()
        self.view.grab_focus()


    def _card_fields(self, card):
        """[(key, text)] for the searchable fields of a card, in HyperCard order."""
        out = []
        bg = self.bg_of(card)
        if bg:
            for p in bg.parts:
                if p.type != "field" or p.dont_search:
                    continue
                c = bg.contents.get(p.id) if p.shared_text else card.bg_contents.get(p.id)
                if c and c.text:
                    out.append((("bg", p.id), c.text))
        for p in card.parts:
            if p.type == "field" and not p.dont_search:
                c = card.contents.get(p.id)
                if c and c.text:
                    out.append((("cd", p.id), c.text))
        return out

    def find_next(self, text):
        st = self.stack
        if not st or not text:
            return
        q = text.lower()
        n = len(st.cards)
        ci, fi, off = self._search_pos if self._search_pos else (self.index, 0, -1)
        for step in range(n + 1):
            k = (ci + step) % n
            card = st.cards[k]
            if card.dont_search:
                continue
            fields = self._card_fields(card)
            for j, (key, t) in enumerate(fields):
                if step == 0 and j < fi:
                    continue
                start = off + 1 if (step == 0 and j == fi) else 0
                pos = t.lower().find(q, start)
                if pos >= 0:
                    self._search_pos = (k, j, pos)
                    self._found_index = k
                    self.view.found = (key, pos, pos + len(text))
                    if k != self.index:
                        self.go(k)
                    else:
                        self.view.refresh()
                    self._scroll_to_found(key, t, pos)
                    return
        self.view.found = None
        self.view.refresh()
        self._search_pos = None
        self.beep()
        self.toast(_("“%s” was not found.") % text)

    def _scroll_to_found(self, key, text, pos):
        p, layer = self.view.part_of(key)
        if not p or p.style != "scrolling":
            return
        r = self.view.renderer
        c = self.view.content(key[0], p, layer)
        _, (tx, ty, tw, th) = r.field_rects(p)
        lay = r.field_layout(p, c, tw)
        rects = r._range_rects(lay, text, pos, pos + 1)
        if rects:
            y = rects[0][1]
            cur = self.view.scroll.get(key, 0)
            if y < cur or y > cur + th - rects[0][3]:
                self.view.scroll[key] = max(0, min(r.max_scroll(p, layer, key[0]), y - th // 3))
                self.view.refresh()

    # -- card image: print, copy, export --------------------------------------------------------
    def card_texture(self, scale=1):
        node = self.view.renderer.render(self.card, {"scroll": self.view.scroll, "bg_only": self.view.bg_only})
        snap = Gtk.Snapshot()
        snap.scale(scale, scale)
        if node:
            snap.append_node(node)
        node = snap.to_node()
        from gi.repository import Graphene
        rect = Graphene.Rect().init(0, 0, self.stack.width * scale, self.stack.height * scale)
        return self.get_native().get_renderer().render_texture(node, rect)

    def copy_image(self):
        if not self.stack:
            return
        self.get_clipboard().set_texture(self.card_texture(1))
        self.toast(_("Card copied as image."))

    def export_png(self):
        if not self.stack:
            return
        dlg = Gtk.FileDialog(title=_("Export Card as PNG…"))
        dlg.set_initial_name("%s - %d.png" % (self.stack.name.replace("/", "_"), self.index + 1))

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.card_texture(1).save_to_png(f.get_path())
                self.toast(_("Saved %s") % os.path.basename(f.get_path()))
        dlg.save(self, None, done)

    def print_card(self):
        if not self.stack:
            return
        from gi.repository import GdkPixbuf
        tex = self.card_texture(4)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write_bytes(tex.save_to_png_bytes())
        loader.close()
        pb = loader.get_pixbuf()
        op = Gtk.PrintOperation()
        op.set_n_pages(1)
        op.set_job_name("%s - %d" % (self.stack.name, self.index + 1))
        op.set_embed_page_setup(True)

        def draw(op, ctx, nr):
            try:
                cr = ctx.get_cairo_context()
            except TypeError:  # PyGObject built without cairo support
                GLib.idle_add(lambda: self._fail(_("Printing needs PyGObject's cairo support (sudo dnf install python3-gobject).")) and False)
                return
            pw, ph = ctx.get_width(), ctx.get_height()
            f = min(pw / pb.get_width(), ph / pb.get_height())
            cr.scale(f, f)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
        op.connect("draw-page", draw)
        export = os.environ.get("HCX_PRINT_EXPORT")  # used by the test suite
        if export:
            op.set_export_filename(export)
        try:
            op.run(Gtk.PrintOperationAction.EXPORT if export else Gtk.PrintOperationAction.PRINT_DIALOG, self)
        except GLib.Error as e:
            self.toast(str(e))

    # -- saving -------------------------------------------------------------------------------------
    def _mark_dirty(self):
        if self.stack and not self.dirty:
            self.dirty = True
            self._update_title()
            self._update_actions()

    def save(self):
        if not self.stack:
            return False
        self.view.finish_editing()
        try:
            write_xml(self.stack, os.path.join(self.stack.dir_path, "_stack.xml"))
        except OSError as e:
            self._fail(str(e))
            return False
        self.dirty = False
        self._update_title()
        self._update_actions()
        self.toast(_("Changes saved. They stay when you open this stack again."))
        return True

    def save_script_changes(self, keep=None):
        for key, st in list(self._script_dirty.items()):
            if st is keep:
                continue
            if st is self.stack and self.dirty:
                continue        # the user decides about their own edits
            try:
                write_xml(st, os.path.join(st.dir_path, "_stack.xml"))
            except OSError as e:
                self.log("saving %s: %s" % (st.name, e))
            self._script_dirty.pop(key, None)

    def _on_close_request(self, win):
        from . import speech
        if speech._SPEECH is not None:
            speech._SPEECH.stop()
        self.view.finish_editing()
        self.save_script_changes()
        if not self.dirty:
            return False
        dlg = Adw.AlertDialog(heading=_("Save changes?"),
                              body=_("This stack has changes that were not saved."))
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("discard", _("Discard"))
        dlg.add_response("save", _("Save"))
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def resp(d, r):
            if r == "save" and self.save():
                self.destroy()
            elif r == "discard":
                self.dirty = False
                self.destroy()
        dlg.connect("response", resp)
        dlg.present(self)
        return True

    # -- zoom --------------------------------------------------------------------------------
    def set_zoom(self, z):
        self.view.set_zoom(z)
        self.zoom_lbl.set_label("%d%%" % round(z * 100))

    def step_zoom(self, d):
        cur = self.view.zoom
        if d > 0:
            nxt = [z for z in ZOOMS if z > cur + 1e-6]
            self.set_zoom(nxt[0] if nxt else ZOOMS[-1])
        else:
            prv = [z for z in ZOOMS if z < cur - 1e-6]
            self.set_zoom(prv[-1] if prv else ZOOMS[0])

    def _initial_zoom(self, st):
        try:
            mons = Gdk.Display.get_default().get_monitors()
            g = mons.get_item(0).get_geometry()
            if g.height >= st.height * 2 + 260 and g.width >= st.width * 2 + 120:
                return 2.0
            if g.height >= st.height * 1.5 + 260 and g.width >= st.width * 1.5 + 120:
                return 1.5
        except Exception:
            pass
        return 1.0

    # -- inspector / editing ----------------------------------------------------------------
    def _sync_selection(self):
        self._update_actions()
        if self.view.selected and not self.split.get_show_sidebar() and self.view.tool != "browse":
            pass
        if self.split.get_show_sidebar():
            self.inspector.refresh()
            if self.view.selected:
                self.inspector.show_object()

    def _on_inspector_change(self):
        self.view.refresh()
        self._on_modified()

    def _on_modified(self):
        self._mark_dirty()
        self._update_title()
        if not self._told_modified:
            self._told_modified = True
            self.toast(_("Press Ctrl+S to keep your changes."), 5)

    def toast(self, text, timeout=4):
        t = Adw.Toast(title=GLib.markup_escape_text(text))
        t.set_timeout(timeout)
        self.toasts.add_toast(t)

    # -- opening ---------------------------------------------------------------------------------
    def choose_stack_file(self):
        dlg = Gtk.FileDialog(title=_("Open Stack…"))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f_all = Gtk.FileFilter(name=_("All files"))
        f_all.add_pattern("*")
        filters.append(f_all)
        dlg.set_filters(filters)
        dlg.set_default_filter(f_all)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.app_open(f.get_path())
        dlg.open(self, None, done)

    def app_open(self, path):
        """Open in this window if it is empty, else in a new one."""
        if self.stack is None and not self._loading:
            self.open_path(path)
        else:
            win = HCXWindow(self.get_application())
            win.present()
            win.open_path(path)

    def _on_drop(self, target, value, x, y):
        files = value.get_files()
        for i, f in enumerate(files):
            p = f.get_path()
            if p:
                self.app_open(p)
        return True

    def _show_loading(self, title):
        self._loading = True
        self.load_title.set_label(title)
        self.load_label.set_label(_("Reading file…"))
        self.progress.set_fraction(0)
        self.spinner.start()
        self.pages.set_visible_child_name("loading")
        self._update_actions()

    def _fail(self, msg):
        self._loading = False
        self.spinner.stop()
        self.pages.set_visible_child_name("card" if self.stack else "welcome")
        self._update_actions()
        dlg = Adw.AlertDialog(heading="HCX", body=msg)
        dlg.add_response("ok", _("OK"))
        dlg.present(self)

    def open_path(self, path):
        path = os.path.abspath(path)
        name = os.path.basename(path.rstrip("/"))
        self._show_loading(name)
        if os.path.isdir(path):
            if os.path.exists(os.path.join(path, "_stack.xml")):
                self._run(lambda: ("stack", read_xml(path), None))
            else:
                self._fail(_("No HyperCard stack was found in this file."))
            return

        def work():
            res = macfiles.open_path(path)
            res.mac_fonts = []
            res.mac_home = None
            if res.kind == "hfs":
                from . import macfonts
                from .hthost import find_home_stack, user_home_stack
                res.mac_fonts = macfonts.importable(res.files)
                if not os.path.exists(user_home_stack()):
                    res.mac_home = find_home_stack(res.files)
            return ("opened", res, path)
        self._run(work)

    def _run(self, fn):
        def target():
            try:
                out = fn()
            except ValueError as e:
                if str(e) == "hfsplus":
                    out = ("error", _("HFS+ (Mac OS Extended) disk images are not supported yet. Copy the stack out of the image first (for example with hfsplus or a Mac)."), None)
                else:
                    traceback.print_exc()
                    out = ("error", _("Could not open the stack: %s") % e, None)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                out = ("error", _("Could not open the stack: %s") % e, None)
            GLib.idle_add(self._finished, out)
        threading.Thread(target=target, daemon=True).start()

    def _finished(self, out):
        kind, val, extra = out
        if kind == "error":
            self._fail(val)
        elif kind == "opened":
            if getattr(val, "mac_fonts", None) or getattr(val, "mac_home", None):
                self._offer_mac_fonts(val)
            stacks = val.stacks
            if not stacks and getattr(val, "errors", None):
                self._fail(_("No HyperCard stack could be extracted from this archive. Some files in it could not be read (%s).")
                           % ", ".join(sorted({r for _p, r in val.errors})))
            elif not stacks:
                self._fail(_("No HyperCard stack was found in this file."))
            elif len(stacks) == 1:
                self.import_stack(stacks[0], extra)
            else:
                self._choose_stack(stacks, extra, val.kind)
        elif kind == "stack":
            self._show_stack(val, extra)
        return False

    def _offer_mac_fonts(self, res):
        """A Mac disk with fonts was opened: offer to use them (they stay on this computer)."""
        names = res.mac_fonts
        home = getattr(res, "mac_home", None)
        shown = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
        if names and home is not None:
            text = _("This disk has Mac fonts (%s) and a Home stack.") % shown
        elif names:
            text = _("This disk has Mac fonts (%s).") % shown
        else:
            text = _("This disk has a Home stack.")
        t = Adw.Toast(title=GLib.markup_escape_text(text))
        t.set_button_label(_("Use Them") if names else _("Use It"))
        t.set_timeout(0)
        files = res.files

        def use(_t):
            from . import macfonts
            from .hthost import save_home_stack
            n = macfonts.import_fonts(files) if names else 0
            if home is not None:
                try:
                    save_home_stack(home)
                    self._stack_cache.pop("home", None)
                except OSError:
                    pass
            if self.stack is not None:
                self.view.refresh()
            if n:
                self.toast(_("%d Mac fonts copied to HCX. Stacks now use them.") % n)
            elif home is not None:
                self.toast(_("HCX will use this Home stack for “go home”."))
            else:
                self.toast(_("No fonts could be read."))
        t.connect("button-clicked", use)
        self.toasts.add_toast(t)

    def _choose_stack(self, stacks, path, kind="hfs"):
        body = _("This disk image contains several stacks.") if kind == "hfs" else \
            _("This archive contains several stacks.")
        dlg = Adw.AlertDialog(heading=_("Choose a Stack"), body=body)
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for mf in stacks:
            row = Adw.ActionRow(title=GLib.markup_escape_text(mf.name), activatable=True)
            folder = mf.path.rsplit(":", 1)[0].replace(":", " ▸ ") if ":" in (mf.path or "") else ""
            if folder:
                row.set_subtitle(GLib.markup_escape_text(folder))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row._mf = mf
            lb.append(row)
        sw = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=360,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(lb)
        dlg.set_extra_child(sw)
        dlg.add_response("cancel", _("Cancel"))
        dlg.set_close_response("cancel")
        chosen = []

        def activated(_lb, row):
            chosen.append(row)
            dlg.close()
            self.import_stack(row._mf, path)
        lb.connect("row-activated", activated)

        def response(d, r):
            if chosen:
                return
            if r == "cancel" and self._loading and self.stack is None:
                self._loading = False
                self.spinner.stop()
                self.pages.set_visible_child_name("welcome")
                self._update_actions()
            elif r == "cancel" and self._loading:
                self._loading = False
                self.spinner.stop()
                self.pages.set_visible_child_name("card")
                self._update_actions()
        dlg.connect("response", response)
        dlg.present(self)

    def import_stack(self, mf, path, encoding=None, force=False, keep_index=False):
        self._show_loading(mf.name)
        self.load_title.set_label(_("Importing…") + " " + mf.name)
        enc = encoding or self.encoding

        def progress(frac, msg):
            GLib.idle_add(self._progress, frac, msg)

        def work():
            pkg = import_stack(mf, path, encoding=enc, progress=progress, force=force)
            st = read_xml(pkg)
            return ("stack", st, (mf, path, keep_index))
        self._run(work)

    def _progress(self, frac, msg):
        self.progress.set_fraction(max(0.0, min(1.0, frac)))
        self.load_label.set_label(_(PROGRESS_LABELS.get(msg, "Loading…")))
        return False

    def reimport(self, enc):
        if self.source:
            mf, path = self.source
            self.import_stack(mf, path, encoding=enc, force=True, keep_index=True)

    def _show_stack(self, st, extra, go_first=True, from_script=False):
        self._loading = False
        self.spinner.stop()
        keep = False
        first = self.stack is None
        if self.stack is not None:
            self.save_script_changes(keep=st)
        if not from_script and not (extra and extra[2]):
            # a stack the user opened: start clean
            self._close_script_windows()
            self.stacks_using = []
            self._stack_cache = {}
            self._rsrc_cache = {}
            self.menu_reset()
            self.card_stack = []
        if extra and extra[0] is not None and not getattr(st, "mac_path", ""):
            st.mac_path = (extra[0].path or extra[0].name).replace("/", ":")
        if extra:
            mf, path, keep = extra
            self.source = (mf, path)
        old_index = self.index
        self.stack = st
        self.history = []
        self.forth = []
        self._told_modified = False
        self.dirty = False
        self._search_pos = None
        self.addcolor_state["enabled"] = True
        self.addcolor_state["extra"] = []
        if extra and extra[0] is not None:
            try:
                addcolor.ensure(st, extra[1], extra[0].path)
            except Exception:  # noqa: BLE001 - color is optional
                traceback.print_exc()
        self.view.color_state = self.addcolor_state
        self.view.set_stack(st)
        if first:
            z = self._initial_zoom(st)
            self.set_zoom(z)
            self.set_default_size(max(640, int(st.width * z) + 80), max(480, int(st.height * z) + 190))
        else:
            self.set_zoom(self.view.zoom)
        self.pages.set_visible_child_name("card")
        if extra and extra[0] is not None:
            self.stack_sources[id(st)] = (extra[0], extra[1])
        self.index = -1
        self._in_open_card = True
        try:
            self.go(old_index if keep and old_index < len(st.cards) else 0, record=False, messages=False)
            start = self.index
            self.dispatch("openStack")
            if self.index == start and go_first:
                self.dispatch("openBackground")
                self.dispatch("openCard")
        finally:
            self._in_open_card = False
        self.set_tool_browse()
        self.view.grab_focus()
        self._update_actions()
        return False

    # -- export ----------------------------------------------------------------------------------
    def export_package(self):
        if not self.stack:
            return
        dlg = Gtk.FileDialog(title=_("Export Package…"))

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            if not folder or not folder.get_path():
                return
            base = "".join(c if c.isalnum() or c in " -_.()" else "_" for c in self.stack.name) or "stack"
            dest = os.path.join(folder.get_path(), base + ".hcxs")
            try:
                if os.path.abspath(dest) != os.path.abspath(self.stack.dir_path):
                    shutil.copytree(self.stack.dir_path, dest, dirs_exist_ok=True)
                write_xml(self.stack, os.path.join(dest, "_stack.xml"))
                self.toast(_("Package exported to %s") % dest)
            except OSError as e:
                self._fail(str(e))
        dlg.select_folder(self, None, done)
