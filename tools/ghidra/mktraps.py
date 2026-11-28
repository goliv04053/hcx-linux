"""Builds traps.json for the Ghidra scripts from the Multiversal Interfaces
(MIT, Wolfgang Thaller; https://github.com/autc04/multiversal): each Mac
trap's name, its arguments (size, register or stack) and result."""
import sys, os, json, glob, yaml
src = sys.argv[1]
items = []
for f in sorted(glob.glob(os.path.join(src, "defs", "*.yaml"))):
    items += [d for d in (yaml.safe_load(open(f)) or []) if isinstance(d, dict)]
types, structs = {}, {}
for d in items:
    if "typedef" in d:
        types[d["typedef"]["name"]] = d["typedef"].get("type")
    for k in ("struct", "union"):
        if k in d:
            structs[d[k]["name"]] = d[k]
BASE = {"int8_t": 1, "uint8_t": 1, "char": 1, "unsigned char": 1, "Byte": 1, "SignedByte": 1, "bool": 1,
        "int16_t": 2, "uint16_t": 2, "short": 2, "int32_t": 4, "uint32_t": 4, "long": 4, "int": 4,
        "void": 0, "float": 4, "double": 8}


def size_of(t, depth=0):
    if t is None or depth > 20:
        return 4
    t = str(t).strip()
    if t.endswith("*") or t.endswith("]") or "(" in t:
        return 4
    t = t.replace("const ", "").replace("struct ", "")
    if t in BASE:
        return BASE[t]
    if t in types:
        return size_of(types[t], depth + 1)
    if t in structs:
        s = structs[t]
        if "size" in s:
            return s["size"]
        return sum(size_of(m.get("type"), depth + 1) for m in s.get("members", []) if isinstance(m, dict)) or 4
    if t.endswith(("Ptr", "Handle", "Hand")) or t.startswith("Str"):
        return 4
    return 4


def sig(fn):
    args, seen = [], set()
    for a in fn.get("args") or []:
        reg = a.get("register")
        if reg and not str(reg)[0] in "AD":
            continue            # TrapBit<...>: in the trap word
        sz = size_of(a.get("type"))
        if not reg and sz > 4:
            sz = 4              # records over 4 bytes go by address
        n = a.get("name") or "arg"
        while n in seen:
            n += "_"
        seen.add(n)
        args.append({"n": n, "t": str(a.get("type")), "size": sz,
                     "reg": str(reg)[:2] if reg else None})
    ret = fn.get("return")
    r = None
    if ret and ret != "void":
        r = {"t": str(ret), "size": min(size_of(ret), 4), "reg": fn.get("returnreg")}
    return {"name": fn["name"], "args": args, "ret": r}


out = {}
disp = {}
for d in items:
    if "dispatcher" in d:
        x = d["dispatcher"]
        disp[x["name"]] = x
        out[int(str(x["trap"]), 0)] = {"name": x["name"], "sel": x.get("selector-location"), "sels": {}}
for d in items:
    fn = d.get("function")
    if not fn or "trap" not in fn:
        continue
    trap = int(str(fn["trap"]), 0)
    s = sig(fn)
    if "selector" in fn and fn.get("dispatcher") in disp:
        dt = int(str(disp[fn["dispatcher"]]["trap"]), 0)
        out.setdefault(dt, {"name": fn["dispatcher"], "sel": None, "sels": {}})["sels"][int(str(fn["selector"]), 0)] = s
    elif trap not in out:
        out[trap] = s
json.dump({"%04X" % k: v for k, v in sorted(out.items())}, open(sys.argv[2], "w"), indent=0)
print(len(out), "traps")
