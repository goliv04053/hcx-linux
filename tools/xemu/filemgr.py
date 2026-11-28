"""The File Manager for externals run by mac68k, over HCX's sandbox folder.

Mac volumes are the folders at the top of the sandbox (~/.local/share/hcx/
files); "HD" always exists and is the default volume. Paths never leave
the sandbox, so an external can read and write its own files but nothing
else on the computer. Finder types and creators are kept in an extended
attribute when the file system has them (TEXT/ttxt otherwise); resource
forks are not kept (a file opened for its resource fork reads empty and
writes are discarded).

The traps take a parameter block in A0 and return the result code in D0
and in ioResult, as on the Mac. Only the synchronous forms are useful
here, and asynchronous calls finish at once without calling ioCompletion.
"""

import calendar
import os
import shutil
import struct
import time

from .mac68k import Unimplemented

MAC_EPOCH = 2082844800

noErr = 0
dirFulErr = -33
dskFulErr = -34
nsvErr = -35
ioErr = -36
bdNamErr = -37
fnOpnErr = -38
eofErr = -39
posErr = -40
tmfoErr = -42
fnfErr = -43
wPrErr = -44
fLckdErr = -45
fBsyErr = -47
dupFNErr = -48
opWrErr = -49
paramErr = -50
rfNumErr = -51
permErr = -54
wrPermErr = -61
dirNFErr = -120
afpAccessDenied = -5000

_ROOT_ID = 2
_XATTR = "user.hcx.finderinfo"


def _root():
    from hcx.hthost import files_root
    return files_root()


def _mac_time(t):
    """A Mac date: seconds from 1904 in local time."""
    return int(calendar.timegm(time.localtime(t)) + MAC_EPOCH) & 0xFFFFFFFF


def _to_local_name(raw):
    s = raw.decode("mac_roman", "replace")
    return s.replace("/", "_").replace("\0", "_") or "_"


def _to_mac_name(s):
    return s.encode("mac_roman", "replace")[:31]


def _visible(name):
    return not name.startswith(".")


class _Open:
    def __init__(self, path, rsrc, write):
        self.path = path
        self.rsrc = rsrc
        self.write = write
        self.pos = 0
        self.dirty = False
        if rsrc:
            self.data = bytearray()
        else:
            with open(path, "rb") as fh:
                self.data = bytearray(fh.read())

    def flush(self):
        if self.dirty and self.write and not self.rsrc:
            with open(self.path, "wb") as fh:
                fh.write(self.data)
        self.dirty = False


class FileManager:
    def __init__(self, tb):
        self.tb = tb
        self.m = tb.m
        self.log = tb.log
        root = _root()
        os.makedirs(os.path.join(root, "HD"), exist_ok=True)
        self.root = os.path.realpath(root)
        self.vols = {}                  # vRefNum -> folder name
        self._scan_volumes()
        self.default_vref = -1
        self.default_dir = _ROOT_ID
        self.wds = {}                   # WD refNum -> (vref, dirID)
        self.next_wd = -32000
        self.fcbs = {}                  # refNum -> _Open
        self.next_ref = 2
        self.finfo = {}                 # path -> (type, creator) when there is no xattr support
        self.m.w16(0x210, 0xFFFF)       # BootDrive
        self.m.w16(0x214, 1)            # SFSaveDisk = -vRefNum
        self.m.w32(0x398, _ROOT_ID)     # CurDirStore

    # -- volumes and folders --------------------------------------------------------------------
    def _scan_volumes(self):
        names = sorted((n for n in os.listdir(self.root)
                        if _visible(n) and os.path.isdir(os.path.join(self.root, n))),
                       key=lambda n: (n.lower() != "hd", n.lower()))
        self.vols = {-1 - i: n for i, n in enumerate(names)}

    def vol_path(self, vref):
        return os.path.join(self.root, self.vols[vref])

    def vol_by_name(self, name):
        name = name.lower()
        for v, n in self.vols.items():
            if n.lower() == name:
                return v
        return None

    def dir_id(self, vref, path):
        """Folder numbers are shared with HCX's native externals (xcmds),
        so a dirID that one gives the other can use, as on the Mac."""
        from hcx import xcmds
        if os.path.realpath(path) == os.path.realpath(self.vol_path(vref)):
            return _ROOT_ID
        return xcmds._dir_id(path)

    def dir_path(self, vref, dirid):
        if vref not in self.vols:
            return None
        if dirid in (0, _ROOT_ID):
            return self.vol_path(vref)
        from hcx import xcmds
        for p, i in xcmds._DIR_IDS.items():
            if i == dirid:
                return p
        return None

    def inside(self, path):
        p = os.path.realpath(path)
        return p == self.root or p.startswith(self.root + os.sep)

    def vol_of(self, path):
        rel = os.path.relpath(os.path.realpath(path), self.root)
        top = rel.split(os.sep)[0]
        for v, n in self.vols.items():
            if n == top:
                return v
        return self.default_vref

    def _vref_dir(self, vref, dirid):
        """The (volume, folder) that a vRefNum (volume, working directory
        or 0) and a dirID name."""
        if vref == 0:
            vref = self.default_vref
            if dirid == 0:
                dirid = self.default_dir
        elif vref in self.wds:
            v, d = self.wds[vref]
            vref = v
            if dirid == 0:
                dirid = d
        elif vref > 0:      # a drive number
            vref = -vref if -vref in self.vols else self.default_vref
        return vref, dirid

    @staticmethod
    def _match(folder, name):
        """The real name of `name` in folder, ignoring case like HFS."""
        p = os.path.join(folder, name)
        if os.path.lexists(p):
            return p
        low = name.lower()
        try:
            for n in os.listdir(folder):
                if n.lower() == low:
                    return os.path.join(folder, n)
        except OSError:
            pass
        return p

    def resolve(self, vref, dirid, raw_name):
        """(error, local path, vref) for a file or folder named by a
        volume/folder pair and a (partial or full) Mac path."""
        name = raw_name or b""
        if b":" in name and not name.startswith(b":"):
            vol, _, rest = name.partition(b":")
            v = self.vol_by_name(_to_local_name(vol))
            if v is None:
                self._scan_volumes()
                v = self.vol_by_name(_to_local_name(vol))
            if v is None:
                return nsvErr, None, None
            base = self.vol_path(v)
            parts = rest.split(b":")
            vref = v
        else:
            vref, dirid = self._vref_dir(vref, dirid)
            if vref not in self.vols:
                return nsvErr, None, None
            base = self.dir_path(vref, dirid)
            if base is None:
                return dirNFErr, None, None
            parts = name[1:].split(b":") if name.startswith(b":") else ([name] if name else [])
        path = base
        for i, part in enumerate(parts):
            if part == b"":
                if i < len(parts) - 1:      # "::" goes up a folder
                    if os.path.realpath(path) != os.path.realpath(self.vol_path(vref)):
                        path = os.path.dirname(path)
                continue
            if i < len(parts) - 1 and not os.path.isdir(self._match(path, _to_local_name(part))):
                return dirNFErr, None, None
            path = self._match(path, _to_local_name(part))
        if not self.inside(path):
            return bdNamErr, None, None
        return noErr, path, vref

    # -- finder info ----------------------------------------------------------------------------
    def get_finfo(self, path):
        try:
            v = os.getxattr(path, _XATTR)
            if len(v) >= 8:
                return v[:4], v[4:8]
        except (OSError, AttributeError):
            pass
        if path in self.finfo:
            return self.finfo[path]
        low = path.lower()
        if low.endswith((".txt", ".text")) or "." not in os.path.basename(path):
            return b"TEXT", b"ttxt"
        if low.endswith((".pict", ".pct")):
            return b"PICT", b"ttxt"
        return b"BINA", b"????"

    def set_finfo(self, path, ftype, creator):
        self.finfo[path] = (ftype, creator)
        try:
            os.setxattr(path, _XATTR, ftype + creator)
        except (OSError, AttributeError):
            pass

    # -- parameter blocks -----------------------------------------------------------------------
    def name_at(self, pb):
        p = self.m.r32(pb + 18)
        return self.m.pstr(p) if p else b""

    def put_name(self, pb, name):
        p = self.m.r32(pb + 18)
        if p:
            self.m.wpstr(p, name, 63)

    def done(self, pb, err):
        self.m.w16(pb + 16, err)
        self.m.set_d(0, err)

    def close_all(self):
        for f in self.fcbs.values():
            try:
                f.flush()
            except OSError:
                pass
        self.fcbs.clear()


# -- traps ------------------------------------------------------------------------------------------

def _fm(tb):
    if getattr(tb, "fm", None) is None:
        tb.fm = FileManager(tb)
    return tb.fm


def _hfs(op):
    return bool(op & 0x0200)


def _open(tb, op, rsrc, deny=False):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    dirid = m.r32(pb + 48) if _hfs(op) else 0
    err, path, _v = fm.resolve(m.s16(pb + 22), dirid, fm.name_at(pb))
    if err:
        return fm.done(pb, err)
    if not os.path.isfile(path):
        return fm.done(pb, fnfErr)
    perm = m.r8(pb + 27)
    if deny:        # ioDenyModes: 1 read, 2 write (and deny bits we don't model)
        perm = {0: 0, 1: 1, 2: 2, 3: 3}[perm & 3]
    write = perm in (0, 2, 3, 4)
    for ref, f in fm.fcbs.items():
        if f.path == path and f.rsrc == rsrc and f.write and perm in (2, 3):
            m.w16(pb + 24, ref)
            return fm.done(pb, opWrErr)
    if len(fm.fcbs) >= 40:
        return fm.done(pb, tmfoErr)
    try:
        f = _Open(path, rsrc, write and os.access(path, os.W_OK))
    except OSError:
        return fm.done(pb, ioErr)
    ref = fm.next_ref
    fm.next_ref += 2
    fm.fcbs[ref] = f
    m.w16(pb + 24, ref)
    fm.done(pb, noErr)


def _file(tb, pb):
    fm = _fm(tb)
    f = fm.fcbs.get(tb.m.s16(pb + 24))
    if f is None:
        fm.done(pb, rfNumErr)
    return f


def _seek(m, f, pb, mode, off):
    mode &= 3
    if mode == 1:
        pos = off
    elif mode == 2:
        pos = len(f.data) + off
    elif mode == 3:
        pos = f.pos + off
    else:
        pos = f.pos
    if pos < 0:
        return posErr
    f.pos = pos
    return noErr


def _read(tb, op):
    m = tb.m
    pb = m.a(0)
    f = _file(tb, pb)
    if f is None:
        return
    m.w32(pb + 40, 0)
    err = _seek(m, f, pb, m.r16(pb + 44), m.s32(pb + 46))
    if err:
        return _fm(tb).done(pb, err)
    want = m.r32(pb + 36)
    mode = m.r16(pb + 44)
    chunk = bytes(f.data[f.pos:f.pos + want])
    if mode & 0x80:         # newline mode: stop after the newline character
        nl = (mode >> 8) & 0xFF
        i = chunk.find(bytes([nl]))
        if i >= 0:
            chunk = chunk[:i + 1]
    m.wbytes(m.r32(pb + 32), chunk)
    f.pos += len(chunk)
    m.w32(pb + 40, len(chunk))
    m.w32(pb + 46, f.pos)
    _fm(tb).done(pb, eofErr if len(chunk) < want and not (mode & 0x80 and chunk.endswith(bytes([(mode >> 8) & 0xFF]))) else noErr)


def _write(tb, op):
    m = tb.m
    pb = m.a(0)
    f = _file(tb, pb)
    if f is None:
        return
    m.w32(pb + 40, 0)
    if not f.write:
        return _fm(tb).done(pb, wrPermErr)
    err = _seek(m, f, pb, m.r16(pb + 44), m.s32(pb + 46))
    if err:
        return _fm(tb).done(pb, err)
    n = m.r32(pb + 36)
    data = m.rbytes(m.r32(pb + 32), n)
    if f.pos > len(f.data):
        f.data.extend(b"\0" * (f.pos - len(f.data)))
    f.data[f.pos:f.pos + n] = data
    f.pos += n
    f.dirty = True
    m.w32(pb + 40, n)
    m.w32(pb + 46, f.pos)
    _fm(tb).done(pb, noErr)


def _close(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    f = fm.fcbs.pop(m.s16(pb + 24), None)
    if f is None:
        return fm.done(pb, rfNumErr)
    try:
        f.flush()
    except OSError:
        return fm.done(pb, ioErr)
    fm.done(pb, noErr)


def _create(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    dirid = m.r32(pb + 48) if _hfs(op) else 0
    err, path, _v = fm.resolve(m.s16(pb + 22), dirid, fm.name_at(pb))
    if err:
        return fm.done(pb, err)
    if os.path.lexists(path):
        return fm.done(pb, dupFNErr)
    if not os.path.isdir(os.path.dirname(path)):
        return fm.done(pb, dirNFErr)
    try:
        open(path, "xb").close()
    except OSError:
        return fm.done(pb, ioErr)
    fm.set_finfo(path, b"\0\0\0\0", b"\0\0\0\0")
    fm.done(pb, noErr)


def _delete(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    dirid = m.r32(pb + 48) if _hfs(op) else 0
    err, path, v = fm.resolve(m.s16(pb + 22), dirid, fm.name_at(pb))
    if os.environ.get("XEMU_TRACE"):
        fm.log("Delete %04X vref=%d dir=%d name=%r -> %s %s" % (op, m.s16(pb + 22), dirid, fm.name_at(pb), err, path))
    if err:
        return fm.done(pb, err)
    if not os.path.lexists(path):
        return fm.done(pb, fnfErr)
    if any(f.path == path for f in fm.fcbs.values()):
        return fm.done(pb, fBsyErr)
    if os.path.realpath(path) == os.path.realpath(fm.vol_path(v)):
        return fm.done(pb, fBsyErr)
    try:
        if os.path.isdir(path):
            if any(_visible(n) for n in os.listdir(path)):
                return fm.done(pb, fBsyErr)
            shutil.rmtree(path)
        else:
            os.remove(path)
            if os.path.isfile(path + ".rsrc"):
                os.remove(path + ".rsrc")      # the resource fork goes with it
    except OSError:
        return fm.done(pb, ioErr)
    fm.done(pb, noErr)


def _rename(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    dirid = m.r32(pb + 48) if _hfs(op) else 0
    vref = m.s16(pb + 22)
    err, path, _v = fm.resolve(vref, dirid, fm.name_at(pb))
    if err:
        return fm.done(pb, err)
    if not os.path.lexists(path):
        return fm.done(pb, fnfErr)
    new = m.pstr(m.r32(pb + 28))
    new = new.rsplit(b":", 1)[-1]
    dest = os.path.join(os.path.dirname(path), _to_local_name(new))
    if os.path.lexists(dest) and dest.lower() != path.lower():
        return fm.done(pb, dupFNErr)
    try:
        os.rename(path, dest)
    except OSError:
        return fm.done(pb, ioErr)
    fm.done(pb, noErr)


def _file_info(fm, m, pb, path, cat):
    """Fills a (H)FileParam or CInfoPBRec for a file or folder."""
    st = os.stat(path)
    vref = fm.vol_of(path)
    parent = fm.dir_id(vref, os.path.dirname(path)) if os.path.realpath(path) != os.path.realpath(fm.vol_path(vref)) else 1
    if os.path.isdir(path):
        if not cat:
            return fnfErr
        m.w8(pb + 30, 0x10)                     # ioFlAttrib: a folder
        m.wbytes(pb + 32, b"\0" * 16)           # ioDrUsrWds
        m.w32(pb + 48, fm.dir_id(vref, path))   # ioDrDirID
        m.w16(pb + 52, sum(1 for n in os.listdir(path) if _visible(n)))
        m.w32(pb + 72, _mac_time(st.st_mtime))  # ioDrCrDat
        m.w32(pb + 76, _mac_time(st.st_mtime))  # ioDrMdDat
        m.w32(pb + 80, 0)
        m.wbytes(pb + 84, b"\0" * 16)
        m.w32(pb + 100, parent)                 # ioDrParID
        return noErr
    ftype, creator = fm.get_finfo(path)
    locked = not os.access(path, os.W_OK)
    is_open = any(f.path == path for f in fm.fcbs.values())
    m.w8(pb + 30, (0x01 if locked else 0) | (0x80 if is_open else 0) | (0x04 if is_open else 0))
    m.w8(pb + 31, 0)
    m.wbytes(pb + 32, ftype + creator + b"\0" * 8)      # ioFlFndrInfo
    if cat:
        m.w32(pb + 48, 0)                       # ioDirID: the file number
    else:
        m.w32(pb + 48, 0)
    size = st.st_size
    m.w16(pb + 52, 0)
    m.w32(pb + 54, size)                        # ioFlLgLen
    m.w32(pb + 58, (size + 511) // 512 * 512)   # ioFlPyLen
    m.w16(pb + 62, 0)
    m.w32(pb + 64, 0)                           # ioFlRLgLen
    m.w32(pb + 68, 0)
    m.w32(pb + 72, _mac_time(st.st_mtime))      # ioFlCrDat
    m.w32(pb + 76, _mac_time(st.st_mtime))      # ioFlMdDat
    if cat:
        m.w32(pb + 80, 0)
        m.wbytes(pb + 84, b"\0" * 16)
        m.w32(pb + 100, parent)                 # ioFlParID
        m.w32(pb + 104, 0)
    return noErr


def _listing(path):
    try:
        names = [n for n in os.listdir(path) if _visible(n)]
    except OSError:
        return []
    return sorted(names, key=lambda n: n.lower())


def _get_file_info(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    idx = m.s16(pb + 28)
    dirid = m.r32(pb + 48) if _hfs(op) else 0
    vref = m.s16(pb + 22)
    if idx > 0:
        err, folder, _v = fm.resolve(vref, dirid, b"")
        if err:
            return fm.done(pb, err)
        files = [n for n in _listing(folder) if os.path.isfile(os.path.join(folder, n))]
        if idx > len(files):
            return fm.done(pb, fnfErr)
        path = os.path.join(folder, files[idx - 1])
        fm.put_name(pb, _to_mac_name(files[idx - 1]))
    else:
        err, path, _v = fm.resolve(vref, dirid, fm.name_at(pb))
        if err:
            return fm.done(pb, err)
        if not os.path.isfile(path):
            return fm.done(pb, fnfErr)
    fm.done(pb, _file_info(fm, m, pb, path, False))


def _set_file_info(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    dirid = m.r32(pb + 48) if _hfs(op) else 0
    err, path, _v = fm.resolve(m.s16(pb + 22), dirid, fm.name_at(pb))
    if err:
        return fm.done(pb, err)
    if not os.path.isfile(path):
        return fm.done(pb, fnfErr)
    fm.set_finfo(path, m.rbytes(pb + 32, 4), m.rbytes(pb + 36, 4))
    fm.done(pb, noErr)


def _get_cat_info(tb, pb):
    fm, m = _fm(tb), tb.m
    idx = m.s16(pb + 28)
    vref = m.s16(pb + 22)
    dirid = m.r32(pb + 48)
    if idx > 0:
        err, folder, _v = fm.resolve(vref, dirid, b"")
        if err:
            return fm.done(pb, err)
        names = _listing(folder)
        if idx > len(names):
            return fm.done(pb, fnfErr)
        path = os.path.join(folder, names[idx - 1])
        fm.put_name(pb, _to_mac_name(names[idx - 1]))
    else:
        name = b"" if idx < 0 else fm.name_at(pb)
        err, path, v = fm.resolve(vref, dirid, name)
        if err:
            return fm.done(pb, err)
        if not os.path.exists(path):
            return fm.done(pb, fnfErr)
        if idx < 0 or not name:
            real = os.path.realpath(path)
            mac = fm.vols[v] if real == os.path.realpath(fm.vol_path(v)) else os.path.basename(path)
            fm.put_name(pb, _to_mac_name(mac))
    if idx <= 0:            # an indexed call leaves ioVRefNum alone
        m.w16(pb + 22, fm.vol_of(path))
    fm.done(pb, _file_info(fm, m, pb, path, True))


def _count_tree(path):
    """(files, folders) inside a folder and all its folders."""
    files = dirs = 0
    for d, ds, fs in os.walk(path):
        ds[:] = [n for n in ds if _visible(n)]
        dirs += len(ds)
        files += sum(1 for n in fs if _visible(n))
    return files, dirs


def _vol_info(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    fm._scan_volumes()
    idx = m.s16(pb + 28)
    if idx > 0:
        vols = sorted(fm.vols, reverse=True)
        if idx > len(vols):
            return fm.done(pb, nsvErr)
        vref = vols[idx - 1]
    elif idx < 0 and fm.name_at(pb):
        name = fm.name_at(pb).split(b":")[0]
        vref = fm.vol_by_name(_to_local_name(name))
        if vref is None:
            return fm.done(pb, nsvErr)
    else:
        vref, _d = fm._vref_dir(m.s16(pb + 22), 0)
        if vref not in fm.vols:
            return fm.done(pb, nsvErr)
    path = fm.vol_path(vref)
    fm.put_name(pb, _to_mac_name(fm.vols[vref]))
    try:
        st = os.stat(path)
        du = shutil.disk_usage(path)
    except OSError:
        return fm.done(pb, ioErr)
    blk = 32768
    nfiles = sum(1 for n in _listing(path) if os.path.isfile(os.path.join(path, n)))
    vfiles, vdirs = _count_tree(path)
    m.w16(pb + 22, vref & 0xFFFF)
    m.w32(pb + 30, _mac_time(st.st_mtime))      # ioVCrDate
    m.w32(pb + 34, _mac_time(st.st_mtime))      # ioVLsMod
    m.w16(pb + 38, 0)                           # ioVAtrb
    m.w16(pb + 40, nfiles)                      # ioVNmFls
    m.w16(pb + 42, 0)                           # ioVBitMap
    m.w16(pb + 44, 0)                           # ioAllocPtr
    m.w16(pb + 46, min(du.total // blk, 0xFFFF))  # ioVNmAlBlks
    m.w32(pb + 48, blk)                         # ioVAlBlkSiz
    m.w32(pb + 52, blk)                         # ioVClpSiz
    m.w16(pb + 56, 0)                           # ioAlBlSt
    m.w32(pb + 58, 1000)                        # ioVNxtCNID
    m.w16(pb + 62, min(du.free // blk, 0x7FFF))  # ioVFrBlk: old code reads it as signed
    if _hfs(op):
        m.w16(pb + 64, 0x4244)                  # ioVSigWord 'BD'
        m.w16(pb + 66, 1)                       # ioVDrvInfo
        m.w16(pb + 68, -33 & 0xFFFF)            # ioVDRefNum
        m.w16(pb + 70, 0)                       # ioVFSID
        m.w32(pb + 72, 0)
        m.w16(pb + 76, 0)
        m.w32(pb + 78, 0)
        m.w32(pb + 82, vfiles)                  # ioVFilCnt: files on the volume
        m.w32(pb + 86, vdirs)                   # ioVDirCnt: folders on the volume
        m.wbytes(pb + 90, b"\0" * 32)
    fm.done(pb, noErr)


def _get_vol(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    fm.put_name(pb, _to_mac_name(fm.vols.get(fm.default_vref, "HD")))
    if _hfs(op):
        m.w16(pb + 22, fm.default_vref & 0xFFFF)
        m.w32(pb + 48, fm.default_dir)          # ioWDDirID
    else:
        # without HFS, a working directory for the default folder
        if fm.default_dir == _ROOT_ID:
            m.w16(pb + 22, fm.default_vref & 0xFFFF)
        else:
            m.w16(pb + 22, _new_wd(fm, fm.default_vref, fm.default_dir) & 0xFFFF)
    fm.done(pb, noErr)


def _set_vol(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    name = fm.name_at(pb)
    dirid = m.r32(pb + 48) if _hfs(op) else 0
    err, path, v = fm.resolve(m.s16(pb + 22), dirid, name)
    if err:
        return fm.done(pb, err)
    if not os.path.isdir(path):
        return fm.done(pb, dirNFErr if name else nsvErr)
    fm.default_vref = v
    fm.default_dir = fm.dir_id(v, path)
    m.w16(0x214, -v & 0xFFFF)
    m.w32(0x398, fm.default_dir)
    fm.done(pb, noErr)


def _new_wd(fm, vref, dirid):
    for wd, vd in fm.wds.items():
        if vd == (vref, dirid):
            return wd
    wd = fm.next_wd
    fm.next_wd += 1
    fm.wds[wd] = (vref, dirid)
    return wd


def _get_eof(tb, op):
    m = tb.m
    pb = m.a(0)
    f = _file(tb, pb)
    if f is None:
        return
    m.w32(pb + 28, len(f.data))
    _fm(tb).done(pb, noErr)


def _set_eof(tb, op):
    m = tb.m
    pb = m.a(0)
    f = _file(tb, pb)
    if f is None:
        return
    if not f.write:
        return _fm(tb).done(pb, wrPermErr)
    n = m.r32(pb + 28)
    if n < len(f.data):
        del f.data[n:]
    else:
        f.data.extend(b"\0" * (n - len(f.data)))
    f.pos = min(f.pos, n)
    f.dirty = True
    _fm(tb).done(pb, noErr)


def _get_fpos(tb, op):
    m = tb.m
    pb = m.a(0)
    f = _file(tb, pb)
    if f is None:
        return
    m.w32(pb + 46, f.pos)
    m.w16(pb + 44, 0)
    m.w32(pb + 36, 0)
    m.w32(pb + 40, 0)
    _fm(tb).done(pb, noErr)


def _set_fpos(tb, op):
    m = tb.m
    pb = m.a(0)
    f = _file(tb, pb)
    if f is None:
        return
    err = _seek(m, f, pb, m.r16(pb + 44), m.s32(pb + 46))
    if not err and f.pos > len(f.data):
        f.pos = len(f.data)
        err = eofErr
    m.w32(pb + 46, f.pos)
    _fm(tb).done(pb, err)


def _flush_file(tb, op):
    m = tb.m
    pb = m.a(0)
    f = _file(tb, pb)
    if f is None:
        return
    try:
        f.flush()
    except OSError:
        return _fm(tb).done(pb, ioErr)
    _fm(tb).done(pb, noErr)


def _ok(tb, op):
    pb = tb.m.a(0)
    if pb:
        tb.m.w16(pb + 16, 0)
    tb.m.set_d(0, 0)


def _hfs_dispatch(tb, op):
    fm, m = _fm(tb), tb.m
    pb = m.a(0)
    sel = m.d(0) & 0xFFFF
    if sel == 1:            # OpenWD
        err, path, v = fm.resolve(m.s16(pb + 22), m.r32(pb + 48), fm.name_at(pb))
        if err:
            return fm.done(pb, err)
        if not os.path.isdir(path):
            return fm.done(pb, dirNFErr)
        m.w16(pb + 22, _new_wd(fm, v, fm.dir_id(v, path)) & 0xFFFF)
        return fm.done(pb, noErr)
    if sel == 2:            # CloseWD
        return fm.done(pb, noErr)
    if sel == 6:            # DirCreate
        err, path, v = fm.resolve(m.s16(pb + 22), m.r32(pb + 48), fm.name_at(pb))
        if err:
            return fm.done(pb, err)
        if os.path.lexists(path):
            return fm.done(pb, dupFNErr)
        try:
            os.mkdir(path)
        except OSError:
            return fm.done(pb, dirNFErr)
        m.w32(pb + 48, fm.dir_id(v, path))
        return fm.done(pb, noErr)
    if sel == 7:            # GetWDInfo
        wd = m.s16(pb + 22)
        idx = m.s16(pb + 26)
        if idx > 0:
            wds = sorted(fm.wds)
            if idx > len(wds):
                return fm.done(pb, nsvErr)
            wd = wds[idx - 1]
        v, d = fm._vref_dir(wd, 0)
        if v not in fm.vols:
            return fm.done(pb, nsvErr)
        fm.put_name(pb, _to_mac_name(fm.vols[v]))
        m.w16(pb + 22, wd & 0xFFFF)
        m.w32(pb + 28, 0)                   # ioWDProcID
        m.w16(pb + 32, v & 0xFFFF)          # ioWDVRefNum
        m.w32(pb + 48, d or _ROOT_ID)       # ioWDDirID
        return fm.done(pb, noErr)
    if sel == 8:            # GetFCBInfo
        idx = m.s16(pb + 28)
        if idx > 0:
            refs = sorted(fm.fcbs)
            if idx > len(refs):
                return fm.done(pb, fnfErr)
            ref = refs[idx - 1]
        else:
            ref = m.s16(pb + 24)
        f = fm.fcbs.get(ref)
        if f is None:
            return fm.done(pb, rfNumErr)
        v = fm.vol_of(f.path)
        fm.put_name(pb, _to_mac_name(os.path.basename(f.path)))
        m.w16(pb + 24, ref)
        m.w32(pb + 32, 0)                   # ioFCBFlNm
        m.w16(pb + 36, (0x0100 if f.write else 0) | (0x0200 if f.rsrc else 0))
        m.w16(pb + 38, 0)
        m.w32(pb + 40, len(f.data))         # ioFCBEOF
        m.w32(pb + 44, len(f.data))         # ioFCBPLen
        m.w32(pb + 48, f.pos)               # ioFCBCrPs
        m.w16(pb + 52, v & 0xFFFF)          # ioFCBVRefNum
        m.w32(pb + 54, 0)
        m.w32(pb + 58, fm.dir_id(v, os.path.dirname(f.path)))  # ioFCBParID
        return fm.done(pb, noErr)
    if sel == 9:            # GetCatInfo
        return _get_cat_info(tb, pb)
    if sel == 10:           # SetCatInfo
        err, path, _v = fm.resolve(m.s16(pb + 22), m.r32(pb + 48), fm.name_at(pb))
        if err:
            return fm.done(pb, err)
        if not os.path.exists(path):
            return fm.done(pb, fnfErr)
        if os.path.isfile(path):
            fm.set_finfo(path, m.rbytes(pb + 32, 4), m.rbytes(pb + 36, 4))
        return fm.done(pb, noErr)
    if sel == 0x1A:         # OpenDF
        return _open(tb, op | 0x200, False)
    if sel in (0x38, 0x39):     # OpenDeny, OpenRFDeny
        return _open(tb, op | 0x200, sel == 0x39, deny=True)
    if sel == 0x1B:         # MakeFSSpec
        name = fm.name_at(pb)
        err, path, v = fm.resolve(m.s16(pb + 22), m.r32(pb + 48), name)
        if err:
            return fm.done(pb, err)
        spec = m.r32(pb + 28)
        real = os.path.realpath(path)
        if real == os.path.realpath(fm.vol_path(v)):
            parent, leaf = 1, fm.vols[v]
        else:
            parent, leaf = fm.dir_id(v, os.path.dirname(path)), os.path.basename(path)
        m.w16(spec, v & 0xFFFF)
        m.w32(spec + 2, parent)
        m.wpstr(spec + 6, _to_mac_name(leaf), 63)
        return fm.done(pb, noErr if os.path.exists(path) else fnfErr)
    if sel == 0x30:         # GetVolParms
        buf = m.r32(pb + 32)
        n = min(m.r32(pb + 36), 14)
        info = struct.pack(">HIII", 1, 0, 0, 0)[:n]
        m.wbytes(buf, info)
        m.w32(pb + 40, len(info))
        return fm.done(pb, noErr)
    raise Unimplemented("HFSDispatch selector %d" % sel)


# -- FSSpec calls (HighLevelFSDispatch, System 7) ---------------------------------------------

def _spec_path(fm, m, spec):
    """(error, path, vref) for an FSSpec."""
    vref, parid, name = m.s16(spec), m.r32(spec + 2), m.pstr(spec + 6)
    if parid == 1:
        vref, _d = fm._vref_dir(vref, 0)
        if vref not in fm.vols:
            return nsvErr, None, None
        return noErr, fm.vol_path(vref), vref
    return fm.resolve(vref, parid, name)


def _high_level_fs(tb, op):
    """Pascal calls with the selector in D0: the OSErr result sits above
    the arguments."""
    fm, m = _fm(tb), tb.m
    sel = m.d(0) & 0xFFFF
    if sel == 1:            # FSMakeFSSpec(vRefNum, dirID, fileName, VAR spec)
        spec = m.pop32()
        name_p = m.pop32()
        dirid = m.pop32()
        vref = m.pops16()
        name = m.pstr(name_p) if name_p else b""
        err, path, v = fm.resolve(vref, dirid, name)
        if not err:
            if os.path.realpath(path) == os.path.realpath(fm.vol_path(v)):
                parent, leaf = 1, fm.vols[v]
            else:
                parent, leaf = fm.dir_id(v, os.path.dirname(path)), os.path.basename(path)
            m.w16(spec, v & 0xFFFF)
            m.w32(spec + 2, parent)
            m.wpstr(spec + 6, _to_mac_name(leaf), 63)
            err = noErr if os.path.lexists(path) else fnfErr
    elif sel in (4, 5):     # FSpCreate(spec, creator, fileType, scriptTag) / FSpDirCreate(spec, scriptTag, VAR dirID)
        out = m.pop32() if sel == 5 else 0
        m.pop16()           # scriptTag
        if sel == 4:
            ftype, creator = m.rbytes(m.sp, 4), m.rbytes(m.sp + 4, 4)
            m.sp = m.sp + 8
        err, path, v = _spec_path(fm, m, m.pop32())
        if not err:
            if os.path.lexists(path):
                err = dupFNErr
            elif not os.path.isdir(os.path.dirname(path)):
                err = dirNFErr
            else:
                try:
                    if sel == 5:
                        os.mkdir(path)
                        m.w32(out, fm.dir_id(v, path))
                    else:
                        open(path, "xb").close()
                        fm.set_finfo(path, ftype, creator)
                except OSError:
                    err = ioErr
    elif sel == 6:          # FSpDelete(spec)
        err, path, v = _spec_path(fm, m, m.pop32())
        if not err:
            if not os.path.lexists(path):
                err = fnfErr
            elif any(f.path == path for f in fm.fcbs.values()) or \
                    os.path.realpath(path) == os.path.realpath(fm.vol_path(v)):
                err = fBsyErr
            elif os.path.isdir(path):
                if any(_visible(n) for n in os.listdir(path)):
                    err = fBsyErr
                else:
                    shutil.rmtree(path)
            else:
                os.remove(path)
    elif sel in (7, 8):     # FSpGetFInfo / FSpSetFInfo(spec, fndrInfo)
        info = m.pop32()
        err, path, _v = _spec_path(fm, m, m.pop32())
        if not err:
            if not os.path.lexists(path):
                err = fnfErr
            elif os.path.isdir(path):
                err = fnfErr
            elif sel == 7:
                ftype, creator = fm.get_finfo(path)
                m.wbytes(info, ftype + creator + b"\0" * 8)
            else:
                fm.set_finfo(path, m.rbytes(info, 4), m.rbytes(info + 4, 4))
    else:
        raise Unimplemented("HighLevelFSDispatch selector %d" % sel)
    m.w16(m.sp, err & 0xFFFF)
    m.set_d(0, err & 0xFFFFFFFF)


def _alias_dispatch(tb, op):
    """The Alias Manager. There are no alias files in the sandbox, so
    ResolveAliasFile only checks that the file is there."""
    fm, m = _fm(tb), tb.m
    sel = m.d(0) & 0xFFFF
    if sel == 0:            # FindFolder(vRefNum, folderType, create, VAR vRefNum, VAR dirID)
        dir_p = m.pop32()
        vref_p = m.pop32()
        create = m.pop16() >> 8
        kind = m.pop32().to_bytes(4, "big").decode("mac_roman")
        vref, _d = fm._vref_dir(m.pops16(), 0)
        if vref not in fm.vols:             # kOnSystemDisk, or another number: the first disk
            vref = -1
        from hcx.xcmds import find_folder
        path = find_folder(fm.vol_path(vref), kind, create) if vref in fm.vols else None
        if path is None:
            err = fnfErr
        else:
            err = noErr
            m.w16(vref_p, vref & 0xFFFF)
            m.w32(dir_p, fm.dir_id(vref, path))
    elif sel == 0x0C:       # ResolveAliasFile(VAR spec, chains, VAR isFolder, VAR wasAliased)
        was_p = m.pop32()
        folder_p = m.pop32()
        m.pop16()
        err, path, _v = _spec_path(fm, m, m.pop32())
        if not err and not os.path.lexists(path):
            err = fnfErr
        m.w8(was_p, 0)
        m.w8(folder_p, 1 if not err and os.path.isdir(path) else 0)
    else:
        raise Unimplemented("AliasDispatch selector %d" % sel)
    m.w16(m.sp, err & 0xFFFF)
    m.set_d(0, err & 0xFFFFFFFF)


def install(os_trap, tb_trap=None):
    """Registers the File Manager traps with toolbox's os_trap."""
    if tb_trap:
        tb_trap(0xAA52)(_high_level_fs)
        tb_trap(0xA823)(_alias_dispatch)
    os_trap(0xA000)(lambda tb, op: _open(tb, op, False))
    os_trap(0xA00A)(lambda tb, op: _open(tb, op, True))
    os_trap(0xA001)(_close)
    os_trap(0xA002)(_read)
    os_trap(0xA003)(_write)
    os_trap(0xA007)(_vol_info)
    os_trap(0xA008)(_create)
    os_trap(0xA009)(_delete)
    os_trap(0xA00B)(_rename)
    os_trap(0xA00C)(_get_file_info)
    os_trap(0xA00D)(_set_file_info)
    os_trap(0xA010, 0xA013, 0xA016, 0xA041, 0xA042, 0xA043)(_ok)  # Allocate, FlushVol, FInitQueue, SetFilLock, RstFilLock, SetFilType
    os_trap(0xA011)(_get_eof)
    os_trap(0xA012)(_set_eof)
    os_trap(0xA014)(_get_vol)
    os_trap(0xA015)(_set_vol)
    os_trap(0xA018)(_get_fpos)
    os_trap(0xA044)(_set_fpos)
    os_trap(0xA045)(_flush_file)
    os_trap(0xA060)(_hfs_dispatch)
