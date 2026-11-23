r"""CAFD version ranges for version-scoped names (names\versioned\*.tsv, versions="..." on contributed definitions).

A CAFD version is main_sub_patch (062_010_106). A range list is comma separated; each item is
    062               every 062_*_* version
    062_010_106       exactly that version
    062-              062_000_000 and newer         062_010_106-   that version and newer
    -052              up to 052_999_999             -052_008_000   up to that version
    052-061           052_000_000 to 061_999_999    (either end may be a full version)
'.' works instead of '_' (062.010.106). Leading zeros are optional.

ranges() turns it into [(lo, hi)] with key() numbers (None = open end), which the definition DB compares with
main * 1000000 + sub * 1000 + patch of each CAFD.
"""
import re

PART = re.compile(r"^\d{1,3}(?:[_.]\d{1,3}){0,2}$")


def key(main, sub=0, patch=0):
    return int(main) * 1_000_000 + int(sub) * 1_000 + int(patch)


def _bound(text, upper):
    if not PART.match(text):
        raise ValueError(f"'{text}' is not a CAFD version (062, 062_010, 062_010_106)")
    nums = [int(x) for x in re.split(r"[_.]", text)]
    fill = 999 if upper else 0
    nums += [fill] * (3 - len(nums))
    return key(*nums)


def ranges(spec):
    """'062-, -040' -> [(62000000, None), (None, 40999999)]; '' -> [(None, None)] (every version).
    Raises ValueError for anything else."""
    spec = (spec or "").strip()
    if not spec:
        return [(None, None)]
    out = []
    for item in (s.strip() for s in spec.split(",")):
        if not item:
            raise ValueError("empty item in the version list")
        if item.count("-") > 1:
            raise ValueError(f"'{item}': one '-' per range")
        if "-" in item:
            lo_s, hi_s = (s.strip() for s in item.split("-"))
            if not lo_s and not hi_s:
                raise ValueError(f"'{item}': give at least one end")
            lo = _bound(lo_s, False) if lo_s else None
            hi = _bound(hi_s, True) if hi_s else None
        else:
            lo, hi = _bound(item, False), _bound(item, True)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"'{item}': the range ends before it starts")
        out.append((lo, hi))
    return out


def overlap(a, b):
    """Do two (lo, hi) ranges share a version?"""
    lo = max(x for x in (a[0], b[0], -1) if x is not None)
    hi = min(x for x in (a[1], b[1], 10 ** 12) if x is not None)
    return lo <= hi


def text(lo, hi):
    """(lo, hi) -> '062_000_000-' style text for messages."""
    f = lambda k: f"{k // 1_000_000:03d}_{k // 1_000 % 1_000:03d}_{k % 1_000:03d}"  # noqa: E731
    if lo is None and hi is None:
        return "every version"
    return (f(lo) if lo is not None else "") + ("-" if lo != hi else "") + (f(hi) if hi is not None and hi != lo else "")
