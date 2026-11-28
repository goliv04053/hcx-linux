"""GTK parts of HyperTalk: modal dialogs (answer, ask, list selection,
pop-up menus) and the floating script windows (palettes, picture windows,
text windows, movies), which float over the card like HyperCard's."""

import os

from gi.repository import Adw, Gdk, GLib, Graphene, Gtk, Pango

from .i18n import _

CSS = """
row.hcx-sep, row.hcx-sep:hover { background: none; min-height: 0; padding: 3px 0; }
row.hcx-sep separator { min-height: 1px; background: alpha(currentColor, 0.25); }
.hcx-palette { background: white; border: 1px solid black; box-shadow: 2px 2px 0 rgba(0,0,0,0.6); }
.hcx-palette-title { min-height: 11px; padding: 0 4px; border-bottom: 1px solid black;
  background-image: repeating-linear-gradient(to bottom, white 0px, white 1px, black 1px, black 2px);
  background-size: 100% 10px; background-position: 0 1px; background-repeat: no-repeat; }
.hcx-palette-title label { background: white; color: black; font-family: "Chicago", "ChicagoFLF", sans-serif;
  font-size: 10px; font-weight: bold; padding: 0 4px; }
.hcx-palette-close { min-width: 9px; min-height: 9px; padding: 0; margin: 1px 0; border: 1px solid black;
  border-radius: 0; background: white; }
.hcx-textwin, .hcx-textwin text { background: white; color: black; }
.hcx-ditl { background: white; color: black; }
.hcx-ditl button.hcx-ditl-btn { min-height: 0; min-width: 0; padding: 0 6px; border-radius: 8px;
  border: 1px solid black; background: white; color: black; box-shadow: none; }
.hcx-ditl button.hcx-ditl-btn:active { background: black; color: white; }
.hcx-ditl button.hcx-ditl-btn:disabled { color: #888; border-color: #888; }
.hcx-ditl button.hcx-ditl-default { outline: 3px solid black; outline-offset: 1px; }
.hcx-ditl label, .hcx-ditl checkbutton label { color: black; }
.hcx-ditl entry, .hcx-ditl textview, .hcx-ditl textview text { background: white; color: black;
  border-radius: 0; min-height: 0; padding: 0 2px; }
.hcx-ditl entry { border: 1px solid black; box-shadow: none; }
.hcx-ditl list, .hcx-ditl list row { background: white; color: black; min-height: 0; padding: 0 3px; }
.hcx-ditl list row:selected { background: black; color: white; }
.hcx-ditl list row:selected label { color: white; }
.hcx-ditl scrolledwindow.hcx-ditl-list { border: 1px solid black; }
"""


def run_modal(present, connect):
    """Present a dialog and wait for its answer without blocking GTK."""
    loop = GLib.MainLoop()
    box = []

    def done(value):
        if not box:
            box.append(value)
        if loop.is_running():
            loop.quit()
    connect(done)
    present()
    if not box:
        loop.run()
    return box[0] if box else None


def answer(win, text, buttons):
    dlg = Adw.AlertDialog(body=str(text))
    buttons = [b for b in buttons[:3] if b is not None] or ["OK"]
    for k, b in enumerate(buttons):
        dlg.add_response("r%d" % k, str(b) or "OK")
    dlg.set_default_response("r%d" % (len(buttons) - 1))
    dlg.set_close_response("r%d" % (len(buttons) - 1))

    def connect(done):
        dlg.connect("response", lambda d, r: done(buttons[int(r[1:])] if r.startswith("r") else buttons[-1]))
    return run_modal(lambda: dlg.present(win), connect)


def ask(win, prompt, default="", password=False):
    dlg = Adw.AlertDialog(body=str(prompt))
    entry = Gtk.PasswordEntry(show_peek_icon=True) if password else Gtk.Entry()
    entry.set_text(str(default))
    entry.set_activates_default(True)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", _("Cancel"))
    dlg.add_response("ok", _("OK"))
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def connect(done):
        dlg.connect("response", lambda d, r: done(entry.get_text() if r == "ok" else None))
    return run_modal(lambda: (dlg.present(win), entry.grab_focus()), connect)


def list_select(win, prompt, lines, multiple=False):
    dlg = Adw.AlertDialog(body=str(prompt))
    lb = Gtk.ListBox(css_classes=["boxed-list"])
    lb.set_selection_mode(Gtk.SelectionMode.MULTIPLE if multiple else Gtk.SelectionMode.SINGLE)
    for ln in lines:
        lb.append(Gtk.Label(label=ln, xalign=0, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4))
    sw = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=320,
                            hscrollbar_policy=Gtk.PolicyType.NEVER)
    sw.set_child(lb)
    dlg.set_extra_child(sw)
    dlg.add_response("cancel", _("Cancel"))
    dlg.add_response("ok", _("OK"))
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def connect(done):
        dlg.connect("response", lambda d, r: done(
            [lines[row.get_index()] for row in lb.get_selected_rows()] if r == "ok" else None))
    return run_modal(lambda: dlg.present(win), connect)


DITL_SCALE = 1.25


class _DitlUI:
    """What an on_click callback of ditl_dialog can change while the dialog is open."""

    def __init__(self, widgets, items, close):
        self._w = widgets
        self._items = items
        self.close = close

    def set_text(self, n, text):
        w = self._w.get(n)
        if isinstance(w, Gtk.Label):
            w.set_label(str(text))
        elif isinstance(w, (Gtk.Button, Gtk.CheckButton)):
            w.set_label(str(text))
        elif isinstance(w, Gtk.Entry):
            w.set_text(str(text))

    def set_png(self, n, png):
        w = self._w.get(n)
        if isinstance(w, Gtk.Picture):
            w.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)) if png else None)

    def set_sensitive(self, n, on):
        w = self._w.get(n)
        if w is not None:
            w.set_sensitive(bool(on))


def _ditl_values(widgets, items):
    out = {}
    for it in items:
        w = widgets.get(it["n"])
        if w is None:
            continue
        k = it["kind"]
        if k == "edit":
            if isinstance(w, Gtk.Entry):
                out[it["n"]] = w.get_text()
            else:
                b = w.get_child().get_buffer()
                out[it["n"]] = b.get_text(b.get_start_iter(), b.get_end_iter(), False)
        elif k in ("check", "radio"):
            out[it["n"]] = w.get_active()
        elif k == "list":
            lb = w.get_child()
            if isinstance(lb, Gtk.Viewport):
                lb = lb.get_child()
            out[it["n"]] = sorted(r.get_index() + 1 for r in lb.get_selected_rows())
        elif k == "popup":
            out[it["n"]] = w.get_selected() + 1
    return out


def ditl_dialog(win, spec, on_click=None):
    """Show a dialog described by macdialog (DITL items placed where the Mac
    put them). Returns (number of the item that closed it or None, values)
    where values maps item numbers to the text of edit fields, the state of
    check boxes and radio buttons, the selected lines of lists (1-based)
    and the chosen item of pop-up menus."""
    S = DITL_SCALE
    W, H = int(spec["width"] * S), int(spec["height"] * S)
    dlg = Adw.Window(modal=True, resizable=False, transient_for=win)
    dlg.set_title(spec.get("title") or "")
    fixed = Gtk.Fixed()
    fixed.set_size_request(W, H)
    font = spec.get("font")
    size = spec.get("size") or 12
    widgets = {}
    radios = []
    default = spec.get("default", 1)
    cancel = spec.get("cancel")
    state = {"done": None}

    def place(w, rect, minh=0):
        left, top, right, bottom = rect
        ww, hh = max(1, int((right - left) * S)), max(minh, int((bottom - top) * S))
        w.set_size_request(ww, hh)
        fixed.put(w, int(left * S), int(top * S))

    def text_label(text, it):
        lab = Gtk.Label(label=text, xalign=0, yalign=0, wrap=True, natural_wrap_mode=Gtk.NaturalWrapMode.NONE)
        lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        f = it.get("font") or font
        z = it.get("size") or size
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_size_new_absolute(int(z * S * Pango.SCALE)))
        if f:
            attrs.insert(Pango.attr_family_new(_linux_font(f)))
        lab.set_attributes(attrs)
        return lab

    def clicked(n):
        if state["done"] is not None:
            return
        if on_click is not None and on_click(n, ui) == "keep":
            return
        finish(n)

    for it in spec["items"]:
        k, n = it["kind"], it["n"]
        rect = it["rect"]
        if k == "button":
            b = Gtk.Button(label=it.get("text", ""), css_classes=["hcx-ditl-btn"])
            if n == default and spec.get("outline_default", True):
                b.add_css_class("hcx-ditl-default")
            if it.get("enabled", True):
                b.connect("clicked", lambda _b, n=n: clicked(n))
            place(b, rect)
            widgets[n] = b
        elif k in ("check", "radio"):
            c = Gtk.CheckButton(label=it.get("text", ""))
            c.set_active(bool(it.get("checked")))
            if k == "radio":
                if radios and spec.get("radio_group", True):
                    c.set_group(radios[0])
                radios.append(c)
            if on_click is not None:
                c.connect("toggled", lambda c, n=n: c.get_active() and on_click(n, ui))
            place(c, rect)
            widgets[n] = c
        elif k == "static":
            lab = text_label(it.get("text", ""), it)
            place(lab, rect)
            widgets[n] = lab
        elif k == "edit":
            left, top, right, bottom = rect
            if bottom - top > 24:
                tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
                tv.get_buffer().set_text(it.get("text", ""))
                sw = Gtk.ScrolledWindow(child=tv, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                        css_classes=["hcx-ditl-list"])
                place(sw, rect)
                widgets[n] = sw
            else:
                e = Gtk.Entry(text=it.get("text", ""))
                e.set_activates_default(False)
                e.connect("activate", lambda _e: clicked(default))
                place(e, (left - 3, top - 3, right + 3, bottom + 3))
                widgets[n] = e
        elif k in ("icon", "pict", "user") and (it.get("png") is not None or k != "user"):
            pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.SCALE_DOWN if k == "user" else Gtk.ContentFit.FILL)
            if it.get("png"):
                pic.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(it["png"])))
            place(pic, rect)
            widgets[n] = pic
        elif k == "user" and it.get("picture_area"):
            pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.SCALE_DOWN)
            place(pic, rect)
            widgets[n] = pic
        elif k == "list":
            lb = Gtk.ListBox()
            lb.set_selection_mode(Gtk.SelectionMode.MULTIPLE if it.get("multiple") else Gtk.SelectionMode.SINGLE)
            for ln in it.get("lines", []):
                lb.append(text_label(ln, it))
            for sel in it.get("selected", []):
                row = lb.get_row_at_index(sel - 1)
                if row is not None:
                    lb.select_row(row)
            lb.connect("row-activated", lambda *_: clicked(default))
            sw = Gtk.ScrolledWindow(child=lb, hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["hcx-ditl-list"])
            place(sw, rect)
            widgets[n] = sw
        elif k == "popup":
            dd = Gtk.DropDown.new_from_strings([str(x) for x in it.get("menu", [])] or [""])
            sel = int(it.get("selected_item") or 1) - 1
            if 0 <= sel < len(it.get("menu", [])):
                dd.set_selected(sel)
            place(dd, rect)
            widgets[n] = dd
        elif k == "line":
            place(Gtk.Separator(), rect, 1)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["hcx-ditl"])
    if spec.get("proc") in (0, 4, 8, 12, 16):
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        box.append(hb)
    else:
        box.append(Gtk.WindowHandle(child=Gtk.Box(height_request=6)))
    inner = Gtk.Box(margin_start=4, margin_end=4, margin_bottom=4)
    inner.append(fixed)
    box.append(inner)
    dlg.set_content(box)

    keys = Gtk.EventControllerKey()

    def key(_c, keyval, _code, mods):
        if keyval == Gdk.KEY_Escape or (keyval == Gdk.KEY_period and mods & Gdk.ModifierType.CONTROL_MASK):
            if cancel is not None and cancel in widgets:
                clicked(cancel)
            else:
                finish(None)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and default in widgets:
            focus = dlg.get_focus()
            if isinstance(focus, Gtk.TextView):
                return False
            if widgets[default].get_sensitive():
                clicked(default)
            return True
        return False
    keys.connect("key-pressed", key)
    dlg.add_controller(keys)

    box_done = []

    def finish(n):
        if state["done"] is not None:
            return
        state["done"] = n
        values = _ditl_values(widgets, spec["items"])
        box_done.append((n, values))
        dlg.close()

    ui = _DitlUI(widgets, spec["items"], finish)
    if spec.get("on_show") is not None:
        spec["on_show"](ui)

    def connect(done):
        def on_close(*_):
            if not box_done:
                state["done"] = None
                box_done.append((None, _ditl_values(widgets, spec["items"])))
            done(box_done[0])
            return False
        dlg.connect("close-request", on_close)

    r = run_modal(dlg.present, connect)
    return r if r else (None, {})


def _linux_font(name):
    try:
        from .render import linux_family
        return linux_family(name)
    except Exception:  # noqa: BLE001
        return name


def choose_file(win, kind, prompt="", default="", folder=None, patterns=None):
    """A GTK file chooser for scripts (answer file, ask file and the file
    externals). kind is "file", "folder" or "save". Returns a Linux path or None."""
    from gi.repository import Gio
    dlg = Gtk.FileDialog(title=str(prompt) or (_("Save") if kind == "save" else _("Open")), modal=True)
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
            dlg.set_initial_folder(Gio.File.new_for_path(folder))
        except OSError:
            pass
    if kind == "save" and default:
        dlg.set_initial_name(str(default))
    if patterns and kind != "folder":
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f1 = Gtk.FileFilter(name=patterns[0])
        for p in patterns[1]:
            f1.add_pattern(p)
        f_all = Gtk.FileFilter(name=_("All files"))
        f_all.add_pattern("*")
        filters.append(f_all)
        filters.append(f1)
        dlg.set_filters(filters)
        dlg.set_default_filter(f_all)

    def connect(done):
        def finished(d, res):
            try:
                if kind == "save":
                    f = d.save_finish(res)
                elif kind == "folder":
                    f = d.select_folder_finish(res)
                else:
                    f = d.open_finish(res)
            except GLib.Error:
                f = None
            done(f.get_path() if f is not None else None)
        connect.cb = finished

    def present():
        cb = connect.cb
        if kind == "save":
            dlg.save(win, None, cb)
        elif kind == "folder":
            dlg.select_folder(win, None, cb)
        else:
            dlg.open(win, None, cb)
    return run_modal(present, connect)


def popup_menu(view, items, h, v):
    """Pop-up menu at card point (h, v). Returns (line, subitem) 1-based or None."""
    # One popover per view, reused: unparenting a popover right after it
    # closes makes GTK crash on the next pointer motion (destroyed surface).
    pop = getattr(view, "_hcx_popover", None)
    if pop is None:
        pop = Gtk.Popover(has_arrow=False)
        pop.set_parent(view)
        view._hcx_popover = pop
    z = getattr(view, "zoom", 1.0)
    r = Gdk.Rectangle()
    r.x, r.y, r.width, r.height = int(h * z), int(v * z), 1, 1
    pop.set_pointing_to(r)
    pop.set_position(Gtk.PositionType.BOTTOM)
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
    rows = []
    for k, it in enumerate(items):
        if it["sep"]:
            sep = Gtk.Separator()
            row = Gtk.ListBoxRow(child=sep, activatable=False, selectable=False, can_target=False, can_focus=False, css_classes=["hcx-sep"])
            lb.append(row)
            continue
        subs = it["sub"] or [None]
        for j, sub in enumerate(subs):
            text = it["text"] if sub is None else ("%s  ›  %s" % (it["text"], sub) if j == 0 or True else sub)
            lab = Gtk.Label(label=("✓ " if it["checked"] else "") + text, xalign=0)
            if it["title"]:
                lab.add_css_class("heading")
            row = Gtk.ListBoxRow(child=lab, activatable=it["enabled"] and not it["title"])
            row._pick = (k + 1, j + 1)
            lb.append(row)
            rows.append(row)
    sw = Gtk.ScrolledWindow(propagate_natural_height=True, propagate_natural_width=True,
                            max_content_height=420, hscrollbar_policy=Gtk.PolicyType.NEVER)
    sw.set_child(lb)
    pop.set_child(sw)
    result = []

    def connect(done):
        def activated(_lb, row):
            result.append(row._pick)
            pop.popdown()
        lb.connect("row-activated", activated)
        handler.append(pop.connect("closed", lambda *_: done(result[0] if result else None)))
    handler = []
    try:
        return run_modal(pop.popup, connect)
    finally:
        for hid in handler:
            pop.disconnect(hid)


# -- floating script windows ------------------------------------------------------------------

class ScriptWindow(Gtk.Box):
    """One HyperCard external window shown as a palette over the card."""

    def __init__(self, host, w):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START,
                         valign=Gtk.Align.START, css_classes=["hcx-palette"])
        self.host = host
        self.w = w
        title = Gtk.Box(css_classes=["hcx-palette-title"])
        close = Gtk.Button(css_classes=["hcx-palette-close"], valign=Gtk.Align.CENTER,
                           tooltip_text=_("Close"))
        close.connect("clicked", lambda b: host.window_close(self.w["name"]))
        title.append(close)
        self.title_label = Gtk.Label(hexpand=True, halign=Gtk.Align.CENTER)
        title.append(self.title_label)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        title.add_controller(drag)
        self.append(title)
        style = str(w.get("style", "")).lower()
        if w["kind"] == "picture" and style in ("rect", "plain", "shadow", "dialog", "roundrect"):
            title.set_visible(False)
        self.title_bar = title
        self.body = None
        self.kind = w["kind"]
        if self.kind == "picture":
            self._build_picture()
        elif self.kind == "movie":
            vid = Gtk.Video(autoplay=True)
            vid.set_filename(w.get("file"))
            self.body = vid
            self.append(vid)
        else:
            self._build_text()
        self.update()

    def _build_picture(self):
        self.pic = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.FILL)
        self.pic.set_halign(Gtk.Align.START)
        self.pic.set_valign(Gtk.Align.START)
        png = self.w.get("png")
        self.size = (1, 1)
        if png:
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            self.size = (tex.get_width(), tex.get_height())
            self.pic.set_paintable(tex)
        self.vp = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                     vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.vp.set_child(self.pic)
        click = Gtk.GestureClick()
        click.connect("pressed", self._picture_pressed)
        click.connect("released", self._picture_released)
        click.connect("stopped", self._picture_released)
        self.pic.add_controller(click)
        self.body = self.vp
        self.append(self.vp)

    def _build_text(self):
        self.tv = Gtk.TextView(editable=False, cursor_visible=False, css_classes=["hcx-textwin"],
                               left_margin=6, right_margin=6, top_margin=4, bottom_margin=4)
        self.sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.sw.set_child(self.tv)
        click = Gtk.GestureClick()
        click.connect("released", self._text_clicked)
        self.tv.add_controller(click)
        self.body = self.sw
        self.append(self.sw)
        self._css = Gtk.CssProvider()
        self.tv.get_style_context().add_provider(self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._text = None

    def zoom(self):
        return getattr(self.host.view, "zoom", 1.0)

    def update(self):
        w = self.w
        self.title_label.set_label(w["name"])
        l, t, r, b = w["rect"]
        z = self.zoom()
        width, height = max(20, int((r - l) * z)), max(12, int((b - t) * z))
        if self.kind == "picture":
            pw, ph = self.size
            self.pic.set_size_request(int(pw * z), int(ph * z))
            self.vp.set_size_request(width, height)
            sc = str(w.get("scroll", "0,0")).split(",")
            try:
                sx, sy = int(float(sc[0])), int(float(sc[1])) if len(sc) > 1 else 0
            except ValueError:
                sx = sy = 0
            GLib.idle_add(self._apply_scroll, sx * z, sy * z)
        elif self.kind == "text":
            self.sw.set_size_request(width, height)
            text = str(w.get("text", ""))
            if text != self._text:
                self._text = text
                self.tv.get_buffer().set_text(text, -1)
            wrap = str(w.get("neverwrap", w.get("dontwrap", "false"))).lower() == "true"
            self.tv.set_wrap_mode(Gtk.WrapMode.NONE if wrap else Gtk.WrapMode.WORD_CHAR)
            from .render import linux_family
            fam = linux_family(str(w.get("textfont", "Geneva")))
            try:
                size = int(float(w.get("textsize", 12) or 12))
            except ValueError:
                size = 12
            self._css.load_from_string("textview { font-family: \"%s\"; font-size: %dpx; }" %
                                       (fam, max(6, int(size * z))))
        elif self.body is not None:
            self.body.set_size_request(width, height)
        self.set_visible(bool(w.get("visible")))
        self.host.place_script_window(self)

    def _apply_scroll(self, x, y):
        self.vp.get_hadjustment().set_value(x)
        self.vp.get_vadjustment().set_value(y)
        return False

    def _picture_pressed(self, g, n, x, y):
        z = self.zoom()
        card = None
        view = getattr(self.host, "view", None)
        if view is not None:
            ok, pt = g.get_widget().compute_point(view, Graphene.Point().init(x, y))
            if ok:
                card = (int(pt.x / z), int(pt.y / z))
        self.host._mouse_down = True
        # Run the script after this event returns, so that releasing the
        # button is seen while it runs ("if the mouse is down" = click-and-hold).
        GLib.idle_add(lambda: (self.host.picture_click(self.w["name"], int(x / z), int(y / z), card), False)[1])

    def _picture_released(self, *a):
        self.host._mouse_down = False

    def _text_clicked(self, g, n, x, y):
        bx, by = self.tv.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.tv.get_iter_at_location(bx, by)
        if not ok:
            return
        line = it.get_line() + 1
        char = it.get_offset() + 1
        GLib.idle_add(lambda: (self.host.window_click(self.w["name"], char, line), False)[1])

    def _drag_begin(self, g, x, y):
        self._start = self.w["rect"]

    def _drag_update(self, g, dx, dy):
        z = self.zoom()
        l, t, r, b = self._start
        ddx, ddy = int(dx / z), int(dy / z)
        self.w["rect"] = (l + ddx, t + ddy, r + ddx, b + ddy)
        self.host.place_script_window(self)


def install_css():
    css = Gtk.CssProvider()
    css.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def font_family(name):
    return Pango.FontDescription.from_string(name).get_family()
