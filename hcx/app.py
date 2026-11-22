"""HCX application object."""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import i18n  # noqa: E402
from .i18n import _  # noqa: E402

APP_ID = "io.github.pgostation.HCX"
VERSION = "3.3.1"
RESTART = False

CSS = """
.hcx-card { box-shadow: 0 1px 4px 1px alpha(black, 0.35); background: white; }
"""


class HCXApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE)
        GLib.set_application_name("HCX")
        self.connect("startup", self._startup)

    def _startup(self, app):
        here = os.path.dirname(os.path.abspath(__file__))
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        theme.add_search_path(os.path.join(here, "data", "hicolor"))
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        from . import scriptui, macfonts
        scriptui.install_css()
        macfonts.register()          # the Mac fonts the user imported from a Mac disk
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Window.set_default_icon_name(APP_ID)

        def act(name, cb, accels=()):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action("app." + name, list(accels))

        act("new-window", lambda *a: self.new_window().present(), ["<Ctrl>n"])
        lang = Gio.SimpleAction.new_stateful("language", GLib.VariantType.new("s"),
                                             GLib.Variant.new_string(i18n.saved_language()))
        lang.connect("change-state", self._set_language)
        self.add_action(lang)
        act("restart", self._restart)
        act("about", self._about)
        act("quit", lambda *a: self.quit(), ["<Ctrl>q"])
        for action, accels in (("win.open", ["<Ctrl>o"]), ("win.close", ["<Ctrl>w"]),
                               ("win.first", ["<Ctrl>1"]), ("win.prev", ["<Ctrl>2", "<Alt>Left"]),
                               ("win.next", ["<Ctrl>3", "<Alt>Right"]), ("win.last", ["<Ctrl>4"]),
                               ("win.back", ["<Ctrl>BackSpace"]),
                               ("win.zoom-in", ["<Ctrl>plus", "<Ctrl>equal", "<Ctrl>KP_Add"]),
                               ("win.zoom-out", ["<Ctrl>minus", "<Ctrl>KP_Subtract"]),
                               ("win.zoom-reset", ["<Ctrl>0"]),
                               ("win.inspector", ["<Ctrl>i", "F9"]),
                               ("win.tool::browse", ["<Ctrl>Tab"]),
                               ("win.bg-edit", ["<Ctrl>b"]),
                               ("win.save", ["<Ctrl>s"]),
                               ("win.find", ["<Ctrl>f"]),
                               ("win.find-next", ["<Ctrl>g"]),
                               ("win.print", ["<Ctrl>p"]),
                               ("win.copy-image", ["<Ctrl><Shift>c"])):
            self.set_accels_for_action(action, accels)

    def new_window(self):
        from .window import HCXWindow
        return HCXWindow(self)

    def do_activate(self):
        self.new_window().present()

    def do_open(self, files, n_files, hint):
        for f in files:
            w = self.new_window()
            w.present()
            if f.get_path():
                w.open_path(f.get_path())

    def _set_language(self, action, value):
        code = value.get_string()
        action.set_state(value)
        i18n.save_language(code)
        win = self.get_active_window()
        if win is not None and hasattr(win, "toasts"):
            t = Adw.Toast(title=i18n.translate_to(code, i18n.N_("The new language is used after HCX restarts.")),
                          button_label=i18n.translate_to(code, i18n.N_("Restart Now")), action_name="app.restart",
                          timeout=8)
            win.toasts.add_toast(t)

    def _restart(self, *a):
        """Close every window (they ask about unsaved changes), then start again."""
        global RESTART
        RESTART = True
        for w in list(self.get_windows()):
            w.close()

        def check():
            global RESTART
            if self.get_windows():      # a window kept open (unsaved changes): no restart
                RESTART = False
            return False
        GLib.timeout_add(300, check)

    def _about(self, *a):
        win = self.get_active_window()
        kw = dict(application_name="HCX", application_icon=APP_ID, version=VERSION,
                  comments=_("A HyperCard stack reader for Linux"),
                  developer_name="pgostation",
                  developers=["pgostation (HCX, 2013)", "Linux/GTK4 port: Claude (Anthropic), 2026", "Requested by Gabriel, who also gathered documentation and test stacks"])
        if hasattr(Adw, "AboutDialog"):
            d = Adw.AboutDialog(**kw)
            d.add_credit_section("HyperCardPreview", ["Pierre Lorenzi (MIT) — built-in icons"])
            d.present(win)
        else:
            d = Adw.AboutWindow(transient_for=win, **kw)
            d.present()


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    if len(argv) > 1 and argv[1] in ("--import", "--stackimport"):
        from .stackimport import main as si
        return si(argv[2:])
    if len(argv) > 1 and argv[1] in ("--version", "-V"):
        print("HCX", VERSION)
        return 0
    if i18n.LANG != "en" or i18n.saved_language() == "en":
        # GTK's own texts (file chooser, About window…) follow the chosen language too
        base = i18n.LANG.split("_")[0]
        os.environ["LANGUAGE"] = i18n.LANG + (":" + base if base != i18n.LANG else "")
    rc = HCXApp().run(argv)
    if RESTART and getattr(sys, "orig_argv", None):
        os.execv(sys.executable, list(sys.orig_argv))
    return rc
