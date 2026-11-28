"""Runs a stack's original XCMD/XFCN in the 68k emulator next to HCX's
native rewrite, with the same parameters, and prints both results and the
traps and callbacks the original used. A development tool for porting
externals; HCX itself never runs 68k code.

  python3 -m tools.xemu.compare FILE STACK --list
  python3 -m tools.xemu.compare FILE STACK NAME [PARAM ...]
  python3 -m tools.xemu.compare FILE STACK --batch CASES.json
  python3 -m tools.xemu.compare FILE STACK --dump NAME CODE.bin

FILE is anything HCX opens (a stack, an archive, a disk image); STACK is
the name of a stack inside it. CASES.json is a list of [NAME, [PARAM...]],
or {"files": {"HD:Folder:File": "text", "HD:Empty:": null}, "cases": [...]}
to put files in the sandbox first (both sides see the same files; each
case starts from a fresh copy). A file can also be {"text": "...",
"mtime": seconds since 1970, "type": "INIT", "creator": "abcd"}.
Run it from the top of the repository with the Python that has GTK (the
stack is imported to a temporary folder first). Files the externals read
or write go to a temporary sandbox, never to ~/.local/share/hcx. Globals the
externals set are compared too."""

import json
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="hcx-xemu-")
os.environ["XDG_DATA_HOME"] = _TMP

from hcx import macfiles, xcmds  # noqa: E402
from hcx import hypertalk as ht  # noqa: E402
from hcx.hthost import HostMixin  # noqa: E402
from hcx.model import read_xml  # noqa: E402
from hcx.resources import parse_resource_fork  # noqa: E402
from hcx.stackimport import import_stack  # noqa: E402

from . import mac68k, xcmdrun  # noqa: E402


class Host(HostMixin):
    """Just enough of a window for the interpreter: answers and asks take
    their defaults (or the next of .replies, None cancelling an ask; a file
    dialog takes a Mac path from .replies, or is cancelled), and log lines
    and dialogs are collected."""

    def __init__(self, st):
        self.stack = st
        self.index = 0
        self.history = []
        self.encoding = "auto"
        self.lines = []
        self.replies = []
        self.dialogs = []
        self.init_host()

    @property
    def card(self):
        return self.stack.cards[self.index] if self.stack.cards else None

    def bg_of(self, card):
        return self.stack.bg_by_id(card.bg_id) if card else None

    def go_index(self, i, effect=None):
        self.index = i

    def answer(self, text, buttons):
        self.dialogs.append(["answer", str(text)] + [str(b) for b in buttons])
        if self.replies:
            return self.replies.pop(0)
        return buttons[0] if buttons else "OK"

    def ask(self, prompt, default="", password=False):
        self.dialogs.append(["ask", str(prompt), str(default)])
        if self.replies:
            return self.replies.pop(0)
        return default

    def choose_file(self, kind, prompt="", default="", patterns=None):
        self.dialogs.append(["choose_file", kind, str(prompt), str(default),
                             patterns[0] if patterns else None])
        if self.replies:
            return self.replies.pop(0)
        return None

    def wait(self, secs):
        pass

    def log(self, text):
        self.lines.append(text)

    def script_error(self, text):
        self.lines.append("script error: " + text)


def open_stack(path, name):
    r = macfiles.open_path(path)
    for mf in r.stacks:
        if mf.name.lower() == name.lower():
            return mf, Host(read_xml(import_stack(mf, path, outdir=os.path.join(_TMP, "stack"))))
    names = ", ".join(mf.name for mf in r.stacks)
    raise SystemExit("no stack named %r here (there are: %s)" % (name, names))


def externals(mf):
    return [(t, i, n, d) for t, i, n, d in parse_resource_fork(mf.rsrc or b"") if t in ("XCMD", "XFCN")]


def native(it, name, kind, params):
    f = (xcmds.function if kind == "XFCN" else xcmds.command)(name)
    if f is None:
        return {"error": "no native version"}
    it.result = ""
    try:
        v = f(it, list(params))
    except ht.HTError as e:
        return {"error": "script error: %s" % e}
    return {"result": v if kind == "XFCN" else (it.result or None)}


def _short(v):
    if v == 0:
        return "(missing)"
    if v is None:
        return "(folder)"
    return repr(v[:60]) + ("… %d bytes" % len(v) if len(v) > 60 else "")


def _show(v):
    if v is None:
        return "(none)"
    return json.dumps(v, ensure_ascii=False)


def _finder(path):
    """A file's type and creator, when an external set them, shown after
    its contents so that snapshots compare them too."""
    try:
        v = os.getxattr(path, "user.hcx.finderinfo")
    except OSError:
        return b""
    return b" {finder info %r}" % v[:8]


from hcx.resources import build_resource_fork as resource_fork  # noqa: E402


def sandbox(files):
    """Fresh sandbox contents. Returns a snapshot function. A file is text,
    or a dict with text, type, creator, mtime and rsrc ([type, id, name,
    text] items for a resource fork next to it, as "name.rsrc")."""
    from hcx.hthost import files_root, mac_to_local
    root = files_root()
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "HD"), exist_ok=True)
    for mac, text in files.items():
        p = mac_to_local(mac.rstrip(":"))
        mtime = finder = rsrc = None
        if isinstance(text, dict):
            rsrc = text.get("rsrc")
            finder = (text.get("type", "TEXT") + text.get("creator", "ttxt")).encode("mac_roman") \
                if "type" in text or "creator" in text else None
            text, mtime = text.get("text"), text.get("mtime")
        if text is None:
            os.makedirs(p, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(text.encode("mac_roman", "replace"))
        if rsrc is not None:
            with open(p + ".rsrc", "wb") as fh:
                fh.write(resource_fork([(t, rid, n, bytes.fromhex(d[4:]) if isinstance(d, str) and d.startswith("hex:")
                                        else d.encode("mac_roman") if isinstance(d, str) else bytes(d))
                                        for t, rid, n, d in rsrc]))
        if finder:
            os.setxattr(p, "user.hcx.finderinfo", finder)
        if mtime is not None:
            os.utime(p, (mtime, mtime))

    def snapshot():
        out = {}
        for d, dirs, fs in os.walk(root):
            for n in dirs + fs:
                p = os.path.join(d, n)
                rel = os.path.relpath(p, root).replace(os.sep, ":")
                if os.path.isdir(p):
                    out[rel] = None
                elif n.endswith(".rsrc"):       # a resource fork: its resources, in any order
                    from hcx.resources import parse_resource_fork
                    res = sorted(parse_resource_fork(open(p, "rb").read()))
                    out[rel] = ("rsrc " + repr(res)).encode("latin-1", "replace")
                else:
                    out[rel] = open(p, "rb").read() + _finder(p)
        return out
    return snapshot


def compare(h, name, kind, code, params, files=None, replies=None, clip=""):
    """Runs the original and the native with the same params, files,
    dialog replies and clipboard, and prints whether result, files,
    globals, clipboard and the dialogs shown are the same."""
    it = h.interp
    h.lines.clear()
    globs = dict(it.globals)
    snap = sandbox(files or {})
    h.replies, h.dialogs = list(replies or []), []
    h.set_clipboard(clip)
    emu = xcmdrun.emulate(it, name, kind, params, code)
    emu_files = snap()
    emu_globs = dict(it.globals)
    emu_log, emu_dialogs, emu_clip = list(h.lines), h.dialogs, h.clipboard_text()
    h.lines.clear()
    h.replies, h.dialogs = list(replies or []), []
    it.globals.clear()
    it.globals.update(globs)
    snap = sandbox(files or {})
    h.set_clipboard(clip)
    xcmds._EXT_OVERRIDE[name.lower()] = code
    try:
        nat = native(it, name, kind, params)
    finally:
        xcmds._EXT_OVERRIDE.clear()
    nat_files = snap()
    nat_globs = dict(it.globals)
    nat_dialogs, nat_clip = h.dialogs, h.clipboard_text()
    it.globals.clear()
    it.globals.update(globs)
    same = emu.get("error") is None and nat.get("error") is None and \
        (emu.get("result") or "") == (nat.get("result") or "") and emu_files == nat_files and \
        emu_globs == nat_globs and emu_dialogs == nat_dialogs and emu_clip == nat_clip
    print("%s %s %s%s" % ("=" if same else "≠", name, json.dumps(params, ensure_ascii=False),
                          "  replies " + json.dumps(replies, ensure_ascii=False) if replies else ""))
    print("   original: %s%s" % (_show(emu.get("result")), "   [%s]" % emu["error"] if emu.get("error") else ""))
    print("   native:   %s%s" % (_show(nat.get("result")), "   [%s]" % nat["error"] if nat.get("error") else ""))
    traps = ", ".join("%04X×%d" % (k, v) for k, v in sorted(emu.get("traps", {}).items()))
    cbs = ", ".join("%d×%d" % (k, v) for k, v in sorted(emu.get("callbacks", {}).items()))
    print("   traps: %s" % (traps or "-"))
    print("   callbacks: %s" % (cbs or "-"))
    if emu_files != nat_files:
        for k in sorted(set(emu_files) | set(nat_files)):
            if emu_files.get(k, 0) != nat_files.get(k, 0):
                print("   file %s: original %s, native %s" % (k, _short(emu_files.get(k, 0)), _short(nat_files.get(k, 0))))
    for k in sorted(set(emu_globs) | set(nat_globs)):
        if emu_globs.get(k) != nat_globs.get(k):
            print("   global %s: original %s, native %s" % (k, _show(emu_globs.get(k)), _show(nat_globs.get(k))))
    if emu_clip != nat_clip:
        print("   clipboard: original %s, native %s" % (_show(emu_clip), _show(nat_clip)))
    for d in emu_dialogs:
        print("   dialog: %s" % json.dumps(d, ensure_ascii=False))
    if emu_dialogs != nat_dialogs:
        for d in nat_dialogs:
            print("   native dialog: %s" % json.dumps(d, ensure_ascii=False))
    for line in emu_log + h.lines:
        print("   log: %s" % line)
    return same


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    if argv[2] == "--dump" and len(argv) == 5:     # the 68k code, for tools/ghidra
        mf, _h = open_stack(argv[0], argv[1])
        hits = [d for t, i, n, d in externals(mf) if n.lower() == argv[3].lower()]
        if not hits:
            print("? %s: not in this stack" % argv[3])
            return 1
        open(argv[4], "wb").write(hits[0])
        return 0
    if not mac68k.available():
        print("build the emulator first: sh tools/xemu/cpu/build.sh")
        return 1
    mf, h = open_stack(argv[0], argv[1])
    ext = externals(mf)
    if argv[2] == "--list":
        for t, i, n, d in sorted(ext, key=lambda r: r[2].lower()):
            f = (xcmds.function if t == "XFCN" else xcmds.command)(n)
            print("%s %6d %-28s %6d bytes  %s" % (t, i, n, len(d), "native" if f else "-"))
        return 0
    files = {}
    if argv[2] == "--batch":
        cases = json.load(open(argv[3], encoding="utf-8"))
        if isinstance(cases, dict):
            files, cases = cases.get("files", {}), cases.get("cases", [])
    else:
        cases = [(argv[2], argv[3:])]
    bad = 0
    for name, params in cases:
        hits = [r for r in ext if r[2].lower() == name.lower()]
        if not hits:
            print("? %s: not in this stack" % name)
            bad += 1
            continue
        t, _i, n, d = hits[0]
        bad += not compare(h, n, t, d, [str(p) for p in params], files)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
