"""Runs a stack's original XCMD/XFCN in the 68k emulator next to HCX's
native rewrite, with the same parameters, and prints both results and the
traps and callbacks the original used. A development tool for porting
externals; HCX itself never runs 68k code.

  python3 -m tools.xemu.compare FILE STACK --list
  python3 -m tools.xemu.compare FILE STACK NAME [PARAM ...]
  python3 -m tools.xemu.compare FILE STACK --batch CASES.json

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
    their defaults, and log lines are collected."""

    def __init__(self, st):
        self.stack = st
        self.index = 0
        self.history = []
        self.encoding = "auto"
        self.lines = []
        self.init_host()

    @property
    def card(self):
        return self.stack.cards[self.index] if self.stack.cards else None

    def bg_of(self, card):
        return self.stack.bg_by_id(card.bg_id) if card else None

    def go_index(self, i, effect=None):
        self.index = i

    def answer(self, text, buttons):
        return buttons[0] if buttons else "OK"

    def ask(self, prompt, default="", password=False):
        return default

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


def sandbox(files):
    """Fresh sandbox contents. Returns a snapshot function."""
    from hcx.hthost import files_root, mac_to_local
    root = files_root()
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "HD"), exist_ok=True)
    for mac, text in files.items():
        p = mac_to_local(mac.rstrip(":"))
        mtime = finder = None
        if isinstance(text, dict):
            finder = (text.get("type", "TEXT") + text.get("creator", "ttxt")).encode("mac_roman") \
                if "type" in text or "creator" in text else None
            text, mtime = text.get("text"), text.get("mtime")
        if text is None:
            os.makedirs(p, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(text.encode("mac_roman", "replace"))
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
                out[rel] = None if os.path.isdir(p) else open(p, "rb").read() + _finder(p)
        return out
    return snapshot


def compare(h, name, kind, code, params, files=None):
    it = h.interp
    h.lines.clear()
    globs = dict(it.globals)
    snap = sandbox(files or {})
    emu = xcmdrun.emulate(it, name, kind, params, code)
    emu_files = snap()
    emu_globs = dict(it.globals)
    emu_log = list(h.lines)
    h.lines.clear()
    it.globals.clear()
    it.globals.update(globs)
    snap = sandbox(files or {})
    xcmds._EXT_OVERRIDE[name.lower()] = code
    try:
        nat = native(it, name, kind, params)
    finally:
        xcmds._EXT_OVERRIDE.clear()
    nat_files = snap()
    nat_globs = dict(it.globals)
    it.globals.clear()
    it.globals.update(globs)
    same = emu.get("error") is None and nat.get("error") is None and \
        (emu.get("result") or "") == (nat.get("result") or "") and emu_files == nat_files and \
        emu_globs == nat_globs
    print("%s %s %s" % ("=" if same else "≠", name, json.dumps(params, ensure_ascii=False)))
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
    for line in emu_log + h.lines:
        print("   log: %s" % line)
    return same


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
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
