"""A very small subset of HyperTalk: only the navigation commands people put
in simple link buttons ("go next", "go to card 3", "go card id 1234",
'go cd "Index"', "go back", "go home"). Anything else is ignored, like
the original HCX which had no HyperTalk interpreter at all."""

import re

_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}


def handler(script, name):
    """Lines of `on <name>` … `end <name>` (case-insensitive), or None."""
    lines = script.replace("¬\n", " ").split("\n")
    out = None
    for ln in lines:
        s = ln.split("--", 1)[0].strip()
        low = s.lower()
        if out is None:
            if re.match(r"on\s+%s\b" % re.escape(name), low):
                out = []
        else:
            if re.match(r"end\s+%s\b" % re.escape(name), low):
                return out
            if s:
                out.append(s)
    return out


def parse_go(line):
    """Return a navigation tuple for a `go` line, or None.
    ('rel', 'next'|'prev'|'first'|'last'), ('num', n), ('id', n),
    ('name', str), ('back',), ('home',), ('other',)"""
    low = line.lower().strip()
    if not low.startswith("go"):
        return None
    m = re.match(r'go\s+(?:to\s+)?(.*)$', line.strip(), re.I)
    if not m:
        return None
    rest = m.group(1).strip()
    # strip trailing "in a new window", "without dialog", visual effects in-line
    rest = re.sub(r"\s+(in\s+a\s+new\s+window|without\s+dialog).*$", "", rest, flags=re.I)
    low = rest.lower()
    if low in ("back", "recent card", "recent cd"):
        return ("back",)
    if low in ("home", 'stack "home"'):
        return ("home",)
    m = re.match(r"(next|prev|previous|first|last|this)(?:\s+(?:card|cd))?\s*$", low)
    if m:
        w = m.group(1)
        return ("rel", {"previous": "prev"}.get(w, w))
    m = re.match(r"(?:card|cd)\s+id\s+(\d+)\s*$", low)
    if m:
        return ("id", int(m.group(1)))
    m = re.match(r"(?:card|cd)\s+(\d+)\s*$", low)
    if m:
        return ("num", int(m.group(1)))
    m = re.match(r"(%s)\s+(?:card|cd)\s*$" % "|".join(_ORD), low)
    if m:
        return ("num", _ORD[m.group(1)])
    m = re.match(r'(?:card|cd)\s+"([^"]*)"\s*$', rest, re.I)
    if m:
        return ("name", m.group(1))
    m = re.match(r'(?:card|cd)\s+(\w+)\s*$', rest, re.I)
    if m and not m.group(1).isdigit():
        return ("name", m.group(1))
    return ("other",)


def _subst_me(line, me_name):
    if me_name is None:
        return line
    q = '"%s"' % me_name.replace('"', "")
    return re.sub(r"\(?\s*the\s+(?:short\s+)?name\s+of\s+me\s*\)?", q, line, flags=re.I)


def navigation_for(script, me_name=None, messages=("mouseUp", "mouseDown")):
    """First `go` found in the object's mouse handlers. Returns a tuple, or
    None when there is nothing to follow; ('script',) when the handler holds
    other HyperTalk only."""
    found_handler = False
    for msg in messages:
        lines = handler(script or "", msg.lower())
        if lines is None:
            continue
        found_handler = found_handler or bool(lines)
        for ln in lines:
            low = ln.lower()
            if low.startswith(("visual", "push ", "lock screen", "unlock screen", "set lockscreen",
                               "play ", "beep", "wait ")):
                continue
            if low == "pop card":
                return ("back",)
            g = parse_go(_subst_me(ln, me_name))
            if g:
                return g
    return ("script",) if found_handler else None
