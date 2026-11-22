"""Mac dialog templates (DLOG, ALRT and DITL resources) turned into a
description that scriptui.ditl_dialog shows with GTK widgets.

Externals like ShowDialog, DialogDisplay and Dialoger drew their dialogs
from DLOG/DITL resources the stack author made with ResEdit. This module
reads those resources from the stack's resource chain, fills in the ^0-^3
placeholders (ParamText) and finds the icons and pictures the items show.

A dialog description is a dict:
  title, proc (window kind), width, height (in Mac pixels)
  items: list of dicts with n (1-based DITL number), kind ("button", "check",
         "radio", "static", "edit", "icon", "pict", "user", "help"), rect
         (left, top, right, bottom inside the dialog), text, enabled, png.
"""

import os
import struct

from . import hypertalk as ht

_KINDS = {0: "user", 1: "help", 4: "button", 5: "check", 6: "radio", 7: "control",
          8: "static", 16: "edit", 32: "icon", 64: "pict"}


def parse_dlog(data):
    top, left, bottom, right, proc = struct.unpack_from(">hhhhh", data, 0)
    items_id = struct.unpack_from(">h", data, 18)[0]
    title = b""
    if len(data) > 20:
        title = data[21:21 + data[20]]
    return {"rect": (left, top, right, bottom), "proc": proc, "items_id": items_id,
            "title": title.decode("mac_roman", "replace")}


def parse_alrt(data):
    top, left, bottom, right, items_id = struct.unpack_from(">hhhhh", data, 0)
    return {"rect": (left, top, right, bottom), "proc": 1, "items_id": items_id, "title": ""}


def parse_ditl(data):
    items = []
    if len(data) < 2:
        return items
    n = struct.unpack_from(">h", data, 0)[0] + 1
    o = 2
    for i in range(max(0, n)):
        if o + 14 > len(data):
            break
        top, left, bottom, right = struct.unpack_from(">hhhh", data, o + 4)
        t = data[o + 12]
        ln = data[o + 13]
        raw = data[o + 14:o + 14 + ln]
        o += 14 + ln + (ln & 1)
        kind = _KINDS.get(t & 0x7F, "user")
        item = {"n": i + 1, "kind": kind, "rect": (left, top, right, bottom),
                "enabled": not (t & 0x80), "text": "", "res_id": None}
        if kind in ("button", "check", "radio", "static", "edit"):
            item["text"] = raw.decode("mac_roman", "replace").replace("\r", "\n")
        elif kind in ("icon", "pict", "control") and len(raw) >= 2:
            item["res_id"] = struct.unpack_from(">h", raw, 0)[0]
        items.append(item)
    return items


def _num(v):
    return int(ht.to_num(v)) if ht.is_number(v) else None


def find(it, rtypes, key):
    """(type, id, name, data) of a resource in the stack's resource chain,
    by id or by name, or None."""
    key = str(key).strip()
    low = key.lower()
    num = _num(key)
    for res in it.host.resource_chain():
        for r in res:
            if r[0] in rtypes and ((num is not None and r[1] == num) or r[2].lower() == low):
                return r
    return None


def icon_png(it, rid):
    from . import resources
    r = find(it, ("cicn",), rid)
    if r is not None:
        try:
            return resources.cicn_png(r[3])
        except Exception:  # noqa: BLE001
            pass
    r = find(it, ("ICON",), rid)
    if r is not None:
        return resources.icon_png(r[3])
    from .render import BUILTIN_ICONS
    path = os.path.join(BUILTIN_ICONS, "icon_%d.png" % int(rid))
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def pict_png(it, rid):
    from . import pict
    r = find(it, ("PICT",), rid)
    if r is None:
        return None
    try:
        return pict.to_png(r[3])
    except Exception:  # noqa: BLE001
        return None


def _control(it, item):
    """A DITL control item: its CNTL resource says if it is a button,
    a check box, a radio button or a pop-up menu."""
    r = find(it, ("CNTL",), item["res_id"]) if item["res_id"] is not None else None
    if r is None:
        item["kind"] = "user"
        return
    d = r[3]
    value = struct.unpack_from(">h", d, 8)[0] if len(d) >= 10 else 0
    proc = struct.unpack_from(">h", d, 16)[0] if len(d) >= 18 else 0
    title = d[23:23 + d[22]].decode("mac_roman", "replace") if len(d) > 22 else ""
    variant = proc & 0x0F
    base = proc >> 4
    if base == 0 and variant & 3 == 1:
        item["kind"] = "check"
    elif base == 0 and variant & 3 == 2:
        item["kind"] = "radio"
    elif base == 0:
        item["kind"] = "button"
    elif base == 63:
        item["kind"] = "popup"
        item["menu_id"] = struct.unpack_from(">h", d, 14)[0] if len(d) >= 16 else 0
    else:
        item["kind"] = "user"
    item["text"] = title
    item["checked"] = bool(value)


def substitute(text, params):
    for k in range(4):
        text = text.replace("^%d" % k, str(params[k]) if k < len(params) and params[k] is not None else "")
    return text


def load(it, key, params=(), alert=False):
    """The description of dialog `key` (DLOG or ALRT id or name), with the
    ^0-^3 placeholders replaced by params, or None if there is no such dialog."""
    r = None
    if not alert:
        r = find(it, ("DLOG",), key)
    if r is None:
        r = find(it, ("ALRT",), key)
        if r is None:
            return None
        head = parse_alrt(r[3])
    else:
        head = parse_dlog(r[3])
    d = find(it, ("DITL",), head["items_id"])
    if d is None:
        return None
    items = parse_ditl(d[3])
    for item in items:
        if item["kind"] == "control":
            _control(it, item)
        if item["kind"] in ("static", "button", "check", "radio", "edit"):
            item["text"] = substitute(item["text"], params)
        elif item["kind"] == "icon":
            item["png"] = icon_png(it, item["res_id"])
        elif item["kind"] == "pict":
            item["png"] = pict_png(it, item["res_id"])
    left, top, right, bottom = head["rect"]
    return {"title": head["title"], "proc": head["proc"], "width": max(40, right - left),
            "height": max(30, bottom - top), "items": items, "name": r[2], "id": r[1],
            "default": 1}


def item(spec, n):
    for i in spec["items"]:
        if i["n"] == n:
            return i
    return None


def items_of(spec, *kinds):
    return [i for i in spec["items"] if i["kind"] in kinds]
