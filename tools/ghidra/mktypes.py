"""Builds types.json for the Ghidra scripts from the Multiversal Interfaces
(MIT, Wolfgang Thaller): Mac typedefs, structs and unions with their member
offsets under 68k alignment (2 bytes for anything wider than a byte), plus
HyperCard's XCmdBlock."""
import sys, os, re, glob, json, yaml
src = sys.argv[1]
items = []
for f in sorted(glob.glob(os.path.join(src, "defs", "*.yaml"))):
    items += [d for d in (yaml.safe_load(open(f)) or []) if isinstance(d, dict)]
macros = {}
for d in items:
    v = d.get("verbatim")
    if isinstance(v, str):
        for m in re.finditer(r"#define (\w+)\s*\\\n((?:.*\\\n)*.*)", v):
            ms = re.findall(r"GUEST<([^>]+(?:<[^>]*>)?)>\s+(\w+)(\[\d+\])?", m.group(2))
            macros[m.group(1)] = [{"name": n, "type": t + (a or "")} for t, n, a in ms]
typedefs, aggs = {}, {}
for d in items:
    if "typedef" in d:
        typedefs[d["typedef"]["name"]] = str(d["typedef"]["type"])
    for k in ("struct", "union"):
        if k in d and d[k].get("name"):
            aggs[d[k]["name"]] = dict(d[k], kind=k)
BASE = {"int8_t": 1, "uint8_t": 1, "char": 1, "unsigned char": 1, "bool": 1, "int16_t": 2, "uint16_t": 2,
        "short": 2, "int32_t": 4, "uint32_t": 4, "long": 4, "int": 4, "unsigned": 4, "float": 4, "double": 8,
        "void": 1}
out_aggs, bad = {}, []


def norm(t):
    return str(t).replace("const ", "").replace("struct ", "").replace("volatile ", "").strip()


def layout(t, depth=0):
    """(size, align) of a type."""
    t = norm(t)
    if depth > 30:
        return 4, 2
    if t.endswith("*") or "(" in t:
        return 4, 2
    m = re.match(r"(.*)\[(\d+)\]$", t)
    if m:
        s, a = layout(m.group(1), depth + 1)
        return s * int(m.group(2)), a
    if t in BASE:
        return BASE[t], min(BASE[t], 2)
    if t in typedefs:
        return layout(typedefs[t], depth + 1)
    if t in aggs:
        return agg(t, depth + 1)
    return 4, 2


def agg(name, depth=0):
    if name in out_aggs:
        o = out_aggs[name]
        return o["size"], o["align"]
    a = aggs[name]
    mem = []
    for m in a.get("members") or []:
        if "common" in m:
            mem += macros.get(m["common"], [])
        elif m.get("name") and m.get("type"):
            mem.append({"name": m["name"], "type": str(m["type"])})
        elif m.get("struct") or m.get("union"):
            pass                    # anonymous nested aggregates: skipped
    off, al, ms = 0, 1, []
    for m in mem:
        s, ma = layout(m["type"], depth + 1)
        if a["kind"] == "struct":
            if ma > 1 and off % 2:
                off += 1
            ms.append(dict(m, offset=off, size=s))
            off += s
        else:
            ms.append(dict(m, offset=0, size=s))
            off = max(off, s)
        al = max(al, ma)
    size = off + (off % 2 if al > 1 else 0)
    if a.get("size") and a["size"] != size:
        bad.append((name, a["size"], size))
        size = a["size"]
    out_aggs[name] = {"kind": a["kind"], "size": size, "align": al, "members": ms}
    return size, al


for n in aggs:
    agg(n)
H = lambda: "Handle"
out_aggs["XCmdBlock"] = {"kind": "struct", "size": 128, "align": 2, "members": [
    {"name": "paramCount", "type": "int16_t", "offset": 0, "size": 2},
    {"name": "params", "type": "Handle[16]", "offset": 2, "size": 64},
    {"name": "returnValue", "type": "Handle", "offset": 66, "size": 4},
    {"name": "passFlag", "type": "Boolean", "offset": 70, "size": 1},
    {"name": "entryPoint", "type": "ProcPtr", "offset": 72, "size": 4},
    {"name": "request", "type": "int16_t", "offset": 76, "size": 2},
    {"name": "result", "type": "int16_t", "offset": 78, "size": 2},
    {"name": "inArgs", "type": "int32_t[8]", "offset": 80, "size": 32},
    {"name": "outArgs", "type": "int32_t[4]", "offset": 112, "size": 16}]}
typedefs["XCmdPtr"] = "XCmdBlock*"
json.dump({"typedefs": typedefs, "aggs": out_aggs}, open(sys.argv[2], "w"))
print(len(typedefs), "typedefs,", len(out_aggs), "aggregates;", len(bad), "size mismatches:", bad[:8])
