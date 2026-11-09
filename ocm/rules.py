"""CAFD coding-rule helpers shared by build_defdb.py and defdb.py.

Rules are XPath 1.0 boolean expressions over the vehicle FP (Fahrzeugprofil):
  boolean(//cat[@val<"128"][crit[@val=N]])        crit N present in any category < 128
  boolean(//cat[@val="C"][crit[@val OP N]])       category C (e.g. 255 build date YYMM,
                                                  128 cylinders, 129 displacement) compared to N
combined with and / or / not(...) / true() / false().
"""
import re

FIXED = "__fixed__"  # condParam without any per-series rule: constant value
CRIT_RE = re.compile(r'boolean\(//cat\[@val<"128"\]\[crit\[@val=(\d+)\]\]\)')
CAT_RE = re.compile(r'boolean\(//cat\[@val="(\d+)"\]\[crit\[@val(>=|<=|=|<|>)(\d+)\]\]\)')
FA_RE = re.compile(r'boolean\(//standardFA\[substring\(@(fabricCode|colourCode),(\d+),(\d+)\)="([^"]*)"\]\)')
_FA_NAMES = {"fabricCode": "upholstery", "colourCode": "paint"}
_SAFE = re.compile(r"^[\s()0-9a-zA-Z_,'<>=!]*$")
CAT_NAMES = {255: "built", 128: "cyl", 129: "disp"}


def crit_refs(raw):
    return [int(x) for x in CRIT_RE.findall(raw)] if raw and raw != FIXED else []


def _cat_text(cat, op, n):
    cat, n = int(cat), int(n)
    if cat == 255 and n >= 100:
        return f"built{op}{n // 100:02d}-{n % 100:02d}"  # YYMM
    if cat == 129:
        return f"disp{op}{n / 10:g}L"
    return f"{CAT_NAMES.get(cat, 'cat' + str(cat))}{op}{n}"


def _fa_text(attr, a, b, val):
    a, b = int(a), int(b)
    full = {"fabricCode": 4, "colourCode": 4}.get(attr, 0)
    op = "=" if (a == 1 and b >= full) else ("^=" if a == 1 else f"[{a}:{b}]=")
    return f"{_FA_NAMES.get(attr, attr)}{op}{val}"


def balanced(s):
    d = 0
    for ch in s:
        d += ch == "("
        d -= ch == ")"
        if d < 0:
            return False
    return d == 0


def readable(raw, crits):
    """Compact boolean expression over option codes. crits: {num: {'short': ...}}"""
    if raw is None:
        return None
    if raw == FIXED:
        return "fixed"
    r = raw.strip()
    if r in ("", "true()", "false()"):
        return {"": "", "true()": "always", "false()": "never"}[r]
    r = CRIT_RE.sub(lambda m: crits[int(m.group(1))]["short"] if int(m.group(1)) in crits else f"#{m.group(1)}", r)
    r = CAT_RE.sub(lambda m: _cat_text(*m.groups()), r)
    r = FA_RE.sub(lambda m: _fa_text(*m.groups()), r)
    r = re.sub(r"\bnot\(", "!(", r)
    r = re.sub(r"\s+and\s+", " & ", r)
    r = re.sub(r"\s+or\s+", " | ", r)
    r = re.sub(r"!\(([^()\s&|]+)\)", r"!\1", r)
    r = re.sub(r"\(([^()\s&|]+)\)", r"\1", r)  # (X) -> X
    while r.startswith("(") and r.endswith(")") and balanced(r[1:-1]):
        r = r[1:-1]
    return r


# ------------------------------------------------------------------ AST + partial evaluation
# Vehicle-class categories (fixed by type key / build date). Everything else (SA, HO-/E-words,
# country SAs) is what you change by editing the FA, so it stays symbolic.
FIXED_CATS = {0, 1, 4, 7, 11, 12, 13, 17, 19, 21, 23, 25, 28, 34, 35, 38, 39, 128, 129, 255}
_TOK = re.compile(r'\s*(boolean\(//cat\[@val<"128"\]\[crit\[@val=\d+\]\]\)|'
                  r'boolean\(//cat\[@val="\d+"\]\[crit\[@val(?:>=|<=|=|<|>)\d+\]\]\)|'
                  r'boolean\(//standardFA\[substring\(@\w+,\d+,\d+\)="[^"]*"\]\)|'
                  r'not\(|\(|\)|\band\b|\bor\b|true\(\)|false\(\))')


def parse(raw):
    """XPath rule -> nested tuples: ('or',[..]) ('and',[..]) ('not',x) ('crit',n) ('cat',c,op,n) True/False."""
    toks, pos = [], 0
    while pos < len(raw):
        m = _TOK.match(raw, pos)
        if not m:
            if raw[pos:].strip() == "":
                break
            raise ValueError("unparsable rule at: " + raw[pos:pos + 40])
        toks.append(m.group(1))
        pos = m.end()
    i = 0

    def expr():
        nonlocal i
        kids = [term()]
        while i < len(toks) and toks[i] == "or":
            i += 1
            kids.append(term())
        return kids[0] if len(kids) == 1 else ("or", kids)

    def term():
        nonlocal i
        kids = [factor()]
        while i < len(toks) and toks[i] == "and":
            i += 1
            kids.append(factor())
        return kids[0] if len(kids) == 1 else ("and", kids)

    def factor():
        nonlocal i
        t = toks[i]
        i += 1
        if t in ("not(", "("):
            e = expr()
            i += 1  # ')'
            return ("not", e) if t == "not(" else e
        if t == "true()":
            return True
        if t == "false()":
            return False
        m = CRIT_RE.fullmatch(t)
        if m:
            return ("crit", int(m.group(1)))
        m = CAT_RE.fullmatch(t)
        if m:
            return ("cat", int(m.group(1)), m.group(2), int(m.group(3)))
        m = FA_RE.fullmatch(t)
        return ("fa",) + m.groups()

    return expr()


def simplify(node, known):
    """Substitute known atoms (known(atom) -> True/False/None) and fold constants."""
    if isinstance(node, bool):
        return node
    k = node[0]
    if k == "fa":
        return node  # FA paint/upholstery: always symbolic
    if k in ("crit", "cat"):
        v = known(node)
        return node if v is None else v
    if k == "not":
        x = simplify(node[1], known)
        if isinstance(x, bool):
            return not x
        return x[1] if x[0] == "not" else ("not", x)
    absorb, ident = (True, False) if k == "or" else (False, True)
    out = []
    for c in node[1]:
        c = simplify(c, known)
        if c is absorb:
            return absorb
        if c is ident:
            continue
        for cc in (c[1] if c[0] == k else [c]):
            if cc not in out:
                out.append(cc)
    if not out:
        return ident
    # absorption: X | (X & Y) -> X ;  X & (X | Y) -> X
    dual = "and" if k == "or" else "or"
    kept = []
    for c in out:
        if c[0] == dual and any(o in c[1] for o in out if o is not c):
            continue
        kept.append(c)
    out = kept
    return out[0] if len(out) == 1 else (k, out)


# ------------------------------------------------------------------ exact minimisation
MAX_MIN_VARS = 8


def _atoms_of(node, acc):
    if isinstance(node, bool):
        return acc
    if node[0] in ("crit", "cat", "fa"):
        if node not in acc:
            acc.append(node)
    elif node[0] == "not":
        _atoms_of(node[1], acc)
    else:
        for c in node[1]:
            _atoms_of(c, acc)
    return acc


def _ev(node, env):
    if isinstance(node, bool):
        return node
    k = node[0]
    if k in ("crit", "cat", "fa"):
        return env[node]
    if k == "not":
        return not _ev(node[1], env)
    if k == "and":
        return all(_ev(c, env) for c in node[1])
    return any(_ev(c, env) for c in node[1])


def _qm(n, ones):
    """Quine-McCluskey: minimal-ish sum of products. Returns list of (value, mask) implicants."""
    terms = {(m, 0) for m in ones}
    primes = set()
    while terms:
        nxt, used = set(), set()
        tl = list(terms)
        for i in range(len(tl)):
            for j in range(i + 1, len(tl)):
                (v1, m1), (v2, m2) = tl[i], tl[j]
                if m1 == m2:
                    d = v1 ^ v2
                    if d and not d & (d - 1) and not d & m1:
                        nxt.add((v1 & ~d, m1 | d))
                        used.add(tl[i])
                        used.add(tl[j])
        primes |= terms - used
        terms = nxt
    # cover: essential primes first, then greedy
    uncovered = set(ones)

    def covers(p, m):
        return (m & ~p[1]) == (p[0] & ~p[1])
    chosen = []
    for m in list(uncovered):
        ps = [p for p in primes if covers(p, m)]
        if len(ps) == 1 and ps[0] not in chosen:
            chosen.append(ps[0])
    for p in chosen:
        uncovered -= {m for m in uncovered if covers(p, m)}
    while uncovered:
        p = max(primes, key=lambda p: (sum(1 for m in uncovered if covers(p, m)), -bin(~p[1] & ((1 << n) - 1)).count("1")))
        chosen.append(p)
        uncovered -= {m for m in uncovered if covers(p, m)}
    return chosen


def minimize(node):
    """Exact two-level minimisation for small rules; returns an equivalent (often shorter) AST."""
    if isinstance(node, bool):
        return node
    ats = _atoms_of(node, [])
    n = len(ats)
    if n == 0 or n > MAX_MIN_VARS:
        return node
    ones = []
    for m in range(1 << n):
        env = {a: bool(m >> i & 1) for i, a in enumerate(ats)}
        if _ev(node, env):
            ones.append(m)
    if not ones:
        return False
    if len(ones) == 1 << n:
        return True
    prods = []
    for v, mk in _qm(n, ones):
        lits = []
        for i, a in enumerate(ats):
            if mk >> i & 1:
                continue
            lits.append(a if v >> i & 1 else ("not", a))
        prods.append(lits[0] if len(lits) == 1 else ("and", lits))
    dnf = prods[0] if len(prods) == 1 else ("or", prods)
    return dnf if _size(dnf) <= _size(node) else node


def _size(node):
    if isinstance(node, bool) or node[0] in ("crit", "cat", "fa"):
        return 1
    if node[0] == "not":
        return _size(node[1])
    return sum(_size(c) for c in node[1])


def render(node, crits, top=True):
    if node is True:
        return "always"
    if node is False:
        return "never"
    k = node[0]
    if k == "crit":
        c = crits.get(node[1])
        return c["short"] if c else f"#{node[1]}"
    if k == "cat":
        return _cat_text(node[1], node[2], node[3])
    if k == "fa":
        return _fa_text(*node[1:])
    if k == "not":
        return "!" + render(node[1], crits, False)  # compound inner already parenthesised
    s = (" | " if k == "or" else " & ").join(render(c, crits, False) for c in node[1])
    return s if top else "(" + s + ")"


def context_from_fp(fp, crits):
    """known() for a vehicle class: atoms in fixed categories resolve against the FP."""
    present = set().union(*fp.values()) if fp else set()

    def known(atom):
        if atom[0] == "crit":
            c = crits.get(atom[1])
            if c is None or c["cat"] not in FIXED_CATS:
                return None
            return atom[1] in fp.get(c["cat"], set())
        cat, op, n = atom[1], atom[2], atom[3]
        if cat not in FIXED_CATS or cat not in fp:
            return None
        return any({"=": v == n, ">=": v >= n, "<=": v <= n, "<": v < n, ">": v > n}[op] for v in fp[cat])
    return known


def atoms(node):
    """crit numbers still present in a (simplified) AST."""
    if isinstance(node, bool):
        return []
    if node[0] == "crit":
        return [node[1]]
    if node[0] in ("cat", "fa"):
        return []
    if node[0] == "not":
        return atoms(node[1])
    return [a for c in node[1] for a in atoms(c)]


def simplified(raw, crits, known):
    """(readable text, remaining crit list) for a rule under a vehicle context."""
    if raw is None:
        return None, []
    if raw == FIXED:
        return "fixed", []
    r = raw.strip()
    if r == "":
        return "", []
    try:
        t = minimize(simplify(parse(r), known or (lambda a: None)))
        return render(t, crits), atoms(t)
    except Exception:
        return readable(raw, crits), crit_refs(raw)


def readable_in_context(raw, crits, known):
    if raw is None:
        return None
    if raw == FIXED:
        return "fixed"
    r = raw.strip()
    if r == "":
        return ""
    try:
        return render(simplify(parse(r), known), crits)
    except Exception:
        return readable(raw, crits)


_OPS = {"=": "==", ">=": ">=", "<=": "<=", "<": "<", ">": ">"}


def eval_rule(raw, fp, fa=None):
    """Evaluate a rule for a vehicle. fp: {cat: set(crits)}; fa: optional {'fabricCode': .., 'colourCode': ..}
    for rules that read the FA directly (paint / upholstery). Returns bool, or None if unknown."""
    if raw is None or raw == FIXED:
        return None
    r = raw.strip()
    if r == "":
        return None
    if FA_RE.search(r):
        if not fa:
            return None  # depends on paint/upholstery we don't know
        r = FA_RE.sub(lambda m: " True " if (fa.get(m.group(1)) or "")[int(m.group(2)) - 1:
                                                                   int(m.group(2)) - 1 + int(m.group(3))] == m.group(4)
                      else " False ", r)
    py = CRIT_RE.sub(lambda m: f" _h({m.group(1)}) ", r)
    py = CAT_RE.sub(lambda m: f" _c({m.group(1)},'{_OPS[m.group(2)]}',{m.group(3)}) ", py)
    py = py.replace("true()", " True ").replace("false()", " False ")
    py = re.sub(r"\bnot\(", " not (", py)
    if not _SAFE.match(py) or "//" in py or "@" in py:
        return None  # unknown XPath construct; don't guess
    low = set().union(*(v for k, v in fp.items() if k < 128)) if fp else set()

    def _h(n):
        return n in low

    def _c(cat, op, n):
        vals = fp.get(cat, ())
        return any({"==": v == n, ">=": v >= n, "<=": v <= n, "<": v < n, ">": v > n}[op] for v in vals)

    try:
        return bool(eval(py, {"__builtins__": {}}, {"_h": _h, "_c": _c, "True": True, "False": False}))
    except Exception:
        return None
