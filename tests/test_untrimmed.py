"""Regression tests for BMW untrimmed names (ocm/untrimmed.py, build_defdb.apply_untrimmed): names by datagroup
layout, and the field fallback for groups whose layout isn't packaged (issue #8: EGS 00002458, group 3000,
byte 4 mask 0xFE split into 0x02 + 0xFC; the other fields keep their names).

    python -m unittest discover -s tests -v

Synthetic package and DB only: no psdzdata, no decrypted CAFDs."""
import glob
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import contextlib
import io

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
import build_defdb as B  # noqa: E402
import untrimmed as U  # noqa: E402

CID = "00002458"
# packaged layout of group 3000 (as in definitions/untrimmed/00002458.tsv.gz, trimmed to bytes 3-4)
PACKED = [(3, 3, "00000001b", "Sportschalter", {"00": "nicht aktiv", "01": "aktiv"}),
          (3, 3, "00000010b", "Allrad", {"00": "nicht_aktiv", "01": "aktiv"}),
          (3, 3, "11111100b", "UNBELEGT", {"00": "initwert"}),
          (4, 4, "00000001b", "AGS_US", {"00": "nicht_aktiv", "01": "aktiv"}),
          (4, 4, "11111110b", "UNBELEGT", {"00": "initwert"})]
# the reporter's layout: byte 4's 0xFE split in two
SPLIT = [(3, 3, "00000001b", ["00", "01"]), (3, 3, "00000010b", ["00", "01"]), (3, 3, "11111100b", ["00"]),
         (4, 4, "00000001b", ["01", "00"]), (4, 4, "00000010b", ["01", "00"]), (4, 4, "11111100b", ["00"])]


def pack_group(layout, names):
    h = U.layout_hash([(s, e, m) for s, e, m, *_ in layout])
    g = {"G": "Applikation", "F": {}, "V": {}}
    for st, en, mk, name, vals in names:
        g["F"][(st, en, mk)] = name
        for hx, vn in vals.items():
            g["V"][(st, en, mk, hx)] = vn
    return ("3000", h), g


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


class FieldMatch(unittest.TestCase):
    def idx(self, *groups):
        return U.field_index(dict(groups))

    def test_exact_field_borrows_name_and_values(self):
        idx = self.idx(pack_group(PACKED, PACKED))
        self.assertEqual(U.field_match(idx, "3000", 4, 4, "00000001b", {"00", "01"}),
                         ("AGS_US", {"00": "nicht_aktiv", "01": "aktiv"}))

    def test_split_field_has_no_match(self):
        idx = self.idx(pack_group(PACKED, PACKED))
        self.assertIsNone(U.field_match(idx, "3000", 4, 4, "00000010b", {"00", "01"}))
        self.assertIsNone(U.field_match(idx, "3000", 4, 4, "11111100b", {"00"}))

    def test_layouts_that_disagree_give_nothing(self):
        other = [r if r[2] != "00000010b" or r[0] != 3 else (3, 3, "00000010b", "xDrive", {}) for r in PACKED]
        lay2 = PACKED[:-1] + [(4, 4, "00000010b", "X", {}), (4, 4, "11111100b", "UNBELEGT", {})]
        idx = self.idx(pack_group(PACKED, PACKED), pack_group(lay2, other))
        self.assertIsNone(U.field_match(idx, "3000", 3, 3, "00000010b", {"00", "01"}))
        self.assertEqual(U.field_match(idx, "3000", 4, 4, "00000001b", {"00", "01"})[0], "AGS_US")

    def test_value_codes_the_function_lacks_reject_a_layout(self):
        # a packaged feature bit {00, 01} must not name a bit that only has {00} here (reserved)
        idx = self.idx(pack_group(PACKED, PACKED))
        self.assertIsNone(U.field_match(idx, "3000", 3, 3, "00000010b", {"00"}))
        # the other way round is allowed: the packaged layout names fewer codes than the function has
        self.assertEqual(U.field_match(idx, "3000", 4, 4, "11111110b", {"00", "01"})[0], "UNBELEGT")

    def test_marked_names_are_never_packed_back(self):
        self.assertTrue(U.placeholder("Allrad" + U.POS_MARK))
        self.assertFalse(U.placeholder("Allrad"))


class ApplyUntrimmed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-untrimmed-")
        key, g = pack_group(PACKED, PACKED)
        quiet(U.save, CID, {key: g}, {"3000": "Applikation"}, self.tmp)
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(B.SCHEMA)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_cafd(self, layout, version, named=None):
        """layout: [(start, end, mask, [value hex..])]; named: {(start, end, mask): (name, source)} pre-named."""
        c = self.con
        cpk = c.execute("INSERT INTO cafd(cafd_id, version) VALUES(?, ?)", (CID, version)).lastrowid
        dpk = c.execute("INSERT INTO datagroup(cafd_pk, address) VALUES(?, '3000')", (cpk,)).lastrowid
        for i, (st, en, mk, hexes) in enumerate(layout):
            name, src = (named or {}).get((st, en, mk), (f"3000_{st}_{en}_{int(mk[:-1], 2)}", "derived"))
            fpk = c.execute("INSERT INTO func(cafd_pk, dg_pk, address, idx, start, end, mask, name, name_source) "
                            "VALUES(?,?,?,?,?,?,?,?,?)", (cpk, dpk, "3000", i, st, en, mk, name, src)).lastrowid
            for j, hx in enumerate(hexes):
                c.execute("INSERT INTO val(func_pk, idx, hex, label, label_source) VALUES(?,?,?,?, 'derived')",
                          (fpk, j, hx, f"wert_{j + 1:02d} [n/a here]"))
        return cpk

    def names(self, cpk):
        out = {}
        for fpk, st, en, mk, name, src in self.con.execute(
                "SELECT pk, start, end, mask, name, name_source FROM func WHERE cafd_pk=? ORDER BY idx", (cpk,)):
            vals = dict(self.con.execute("SELECT hex, label FROM val WHERE func_pk=?", (fpk,)).fetchall())
            out[(st, en, mk)] = (name, src, vals)
        return out

    def test_packaged_layout_still_matches_exactly(self):
        cpk = self.add_cafd([(s, e, m, list(v)) for s, e, m, _, v in PACKED], "008_044_021")
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        self.assertEqual(n[(4, 4, "00000001b")][:2], ("AGS_US", "untrimmed"))
        self.assertEqual(n[(4, 4, "00000001b")][2]["01"], "wert_02 aktiv [n/a here]")

    def test_split_layout_gets_marked_names_by_field(self):
        cpk = self.add_cafd(SPLIT, "008_044_022")
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        self.assertEqual(n[(3, 3, "00000010b")][:2], ("Allrad" + U.POS_MARK, "untrimmed-pos"))
        self.assertEqual(n[(4, 4, "00000001b")][0], "AGS_US" + U.POS_MARK)
        self.assertEqual(n[(4, 4, "00000001b")][2], {"01": "wert_01 aktiv [n/a here]",
                                                     "00": "wert_02 nicht_aktiv [n/a here]"})
        comment = self.con.execute("SELECT comment FROM func WHERE cafd_pk=? AND start=4 AND mask='00000001b'",
                                   (cpk,)).fetchone()[0]
        self.assertEqual(comment, U.POS_NOTE)
        # the two new fields from the split stay placeholders
        self.assertEqual(n[(4, 4, "00000010b")][:2], ("3000_4_4_2", "derived"))
        self.assertEqual(n[(4, 4, "11111100b")][:2], ("3000_4_4_252", "derived"))

    def test_names_from_other_sources_win(self):
        cpk = self.add_cafd(SPLIT, "008_044_022", named={(3, 3, "00000001b"): ("SPORTSCHALTER", "cheatsheet"),
                                                          (3, 3, "00000010b"): ("4WD", "contributed")})
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        # same name (any case): kept, and its values get the packaged value names
        self.assertEqual(n[(3, 3, "00000001b")][:2], ("SPORTSCHALTER", "cheatsheet"))
        self.assertEqual(n[(3, 3, "00000001b")][2]["01"], "wert_02 aktiv [n/a here]")
        # a different name: untouched, values too
        self.assertEqual(n[(3, 3, "00000010b")], ("4WD", "contributed", {"00": "wert_01 [n/a here]",
                                                                          "01": "wert_02 [n/a here]"}))


class Vet(unittest.TestCase):
    """Transferred (E-Sys Plus) rows against BMW's own names and against each other."""
    def test_rules(self):
        bmw = dict([pack_group(PACKED, PACKED)])
        lay = [(3, 3, "00000001b"), (3, 3, "00000110b"), (3, 3, "00001000b"), (3, 3, "00010000b"),
               (3, 3, "00100000b"), (3, 3, "11000000b")]
        h = U.layout_hash(lay)
        other = U.layout_hash(lay[:2])
        tr = {("3000", h): {"G": None, "V": {}, "F": {
            (3, 3, "00000001b"): "Sportschalter",        # 1 bit, like BMW's own: kept
            (3, 3, "00000110b"): "Allrad",               # BMW's own Allrad is 1 bit: rejected
            (3, 3, "00001000b"): "DUP", (3, 3, "00010000b"): "DUP",   # one name on two fields: both rejected
            (3, 3, "00100000b"): "UNBELEGT", (3, 3, "11000000b"): "UNBELEGT"}},   # reserved: never a conflict
              ("3000", other): {"G": None, "V": {}, "F": {(3, 3, "00000110b"): "WIDE"}}}
        tr[("3000", h)]["F"][(3, 3, "11000000b")] = "WIDE"   # 2 bits here, 2 bits there: kept
        tr[("3000", other)]["F"][(3, 3, "00000001b")] = "NARROW"
        tr[("3000", h)]["F"][(3, 3, "00100000b")] = "NARROW"
        tr[("3000", other)]["F"][(3, 3, "00000110b")] = "WIDE"
        bad, same = U.vet_rows(bmw, tr)
        self.assertNotIn(("3000", h, (3, 3, "00000001b")), bad)
        self.assertIn("another size", bad[("3000", h, (3, 3, "00000110b"))])
        self.assertIn("more than one field", bad[("3000", h, (3, 3, "00001000b"))])
        self.assertNotIn(("3000", h, (3, 3, "11000000b")), bad)
        self.assertNotIn(("3000", h, (3, 3, "00100000b")), bad)   # NARROW: 1 bit in both layouts
        self.assertEqual(same, set())

    def test_names_on_other_versions_are_not_checked_against_each_other(self):
        # realigned names sit on their reference's own layout; two references may size a field differently
        a = U.layout_hash([(5, 5, "00000001b")])
        b = U.layout_hash([(5, 5, "00011111b")])
        tr = {("3000", a): {"G": None, "V": {}, "F": {(5, 5, "00000001b"): "SPEEDLOCK_SPEEDVALUE_MIN"}},
              ("3000", b): {"G": None, "V": {}, "F": {(5, 5, "00011111b"): "SPEEDLOCK_SPEEDVALUE_MIN"}}}
        bad, _ = U.vet_rows({}, tr)
        self.assertEqual(bad, {})


class Realign(unittest.TestCase):
    """E-Sys Plus names function #i after its reference's function #i (discussion #9). realign() finds the layout
    the reference was made for and puts the names back on their fields."""
    # the reference layout: 4 functions; a newer version inserts a 3-bit field at byte 0 bits 1-3
    REF = [((0, 0, "00000001b"), ["00", "01"], "A_FLAG"), ((1, 1, "00011111b"), ["00", "03", "06"], "SPEED_MIN"),
           ((2, 2, "00111111b"), ["00", "05", "08"], "SPEED_MAX"), ((3, 3, "00000001b"), ["00", "01"], "B_FLAG")]
    NEW = [((0, 0, "00000001b"), ["00", "01"]), ((0, 0, "00001110b"), ["00", "01", "02"]),
           ((1, 1, "00011111b"), ["00", "03", "06"]), ((2, 2, "00111111b"), ["00", "05", "08"]),
           ((3, 3, "00000001b"), ["00", "01"])]
    VALS = {"A_FLAG": {"00": "nicht_aktiv", "01": "aktiv"}, "SPEED_MIN": {"00": "aus", "03": "3_kmh", "06": "6_kmh"},
            "SPEED_MAX": {"00": "aus", "05": "5_kmh", "08": "8_kmh"}, "B_FLAG": {"00": "nicht_aktiv", "01": "aktiv"}}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-realign-")
        self.dirs = {n: getattr(U, n) for n in ("PACK_DIR", "TRANSFERRED_DIR", "REJECTED_DIR", "RAW_DIR", "BYINDEX_DIR")}
        U.PACK_DIR = self.tmp
        U.TRANSFERRED_DIR = os.path.join(self.tmp, "transferred")
        U.REJECTED_DIR = os.path.join(U.TRANSFERRED_DIR, "rejected")
        U.RAW_DIR = os.path.join(U.TRANSFERRED_DIR, "raw")
        U.BYINDEX_DIR = os.path.join(U.RAW_DIR, "byindex")
        names = [n for _, _, n in self.REF]
        raw = {}
        for lay in (self.REF, self.NEW):            # E-Sys Plus's dump of both: names by index
            e = U.entry()
            e["S"] = "150_130_000"
            for i, (k, codes, *_r) in enumerate(lay):
                if i < len(names):
                    e["F"][k] = names[i]
                    for x in codes:
                        if x in self.VALS[names[i]]:
                            e["V"][k + (x,)] = self.VALS[names[i]][x]
            raw[("3000", U.layout_hash([f[0] for f in lay]))] = e
        quiet(U.save, CID, raw, {}, U.RAW_DIR)
        pr = lambda codes: U.rule_print([(x, "") for x in codes])  # noqa: E731
        self.layouts = {"3000": {U.layout_hash([f[0] for f in lay]): [(k, frozenset(c), {pr(c)}) for k, c, *_r in lay]
                                 for lay in (self.REF, self.NEW)}}

    def tearDown(self):
        for n, v in self.dirs.items():
            setattr(U, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_names_go_back_on_their_fields(self):
        quiet(U.realign_cid, CID, self.layouts)
        g, _ = U.load(CID, U.TRANSFERRED_DIR)
        ref = g[("3000", U.layout_hash([f[0] for f in self.REF]))]
        new = g[("3000", U.layout_hash([f[0] for f in self.NEW]))]
        self.assertEqual(ref["S"], "150_130_000")                    # the reference's own layout
        self.assertEqual(ref["F"], {k: n for k, _c, n in self.REF})
        # the newer version, decided field by field: SPEED_MIN stays on its 5-bit field (E-Sys Plus put it on the
        # new 3-bit one), the new field gets nothing, A_FLAG gets nothing (a field was inserted next to it), B_FLAG
        # keeps its name (nothing changed between it and SPEED_MAX)
        self.assertEqual(new["F"], {(1, 1, "00011111b"): "SPEED_MIN", (2, 2, "00111111b"): "SPEED_MAX",
                                    (3, 3, "00000001b"): "B_FLAG"})
        self.assertEqual(new["V"][(1, 1, "00011111b", "03")], "3_kmh")
        # versions not in the DBs: only multi-value fields, by field
        idx = U.transfer_index(g)
        self.assertEqual(U.transfer_match(idx, "3000", 1, 1, "00011111b", {"00", "03", "06"})[0], "SPEED_MIN")
        self.assertIsNone(U.transfer_match(idx, "3000", 0, 0, "00001110b", {"00", "01", "02"}))
        self.assertIsNone(U.transfer_match(idx, "3000", 3, 3, "00000001b", {"00", "01"}))

    def test_esysplus_reference_reads_by_index(self):
        # the same as the dumps, read straight from E-Sys Plus by index (discussion #9): only the newer version,
        # where '.' marks values the reference has no name for
        for p in glob.glob(os.path.join(U.RAW_DIR, "*.tsv.gz")):
            os.remove(p)
        src = os.path.join(self.tmp, "esysplus", CID)
        os.makedirs(src)
        names = [n for _, _, n in self.REF]
        for ver, lay in (("008_000_001", self.REF), ("009_000_001", self.NEW)):   # every version, as dumped
            rows = ["GROUP_ADDR_HEX\tGROUP_NAME\tFUNC_INDEX\tFUNC_NAME\tPARAM_INDEX\tPARAM_NAME\tPARAM_VALUE"]
            for i, (k, codes, *_r) in enumerate(lay):
                if i >= len(names):
                    continue
                for pi, x in enumerate(codes):
                    v = self.VALS[names[i]].get(x, ".")
                    rows.append(f"3000\tApplikation\t{i}\t{names[i]}\t{pi}\t{v}\t{x}")
            with open(os.path.join(src, f"EGS (Reference_CAF_{CID}_116_064_000)_{CID}_{ver}.tsv"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")
        quiet(U.pack_esysplus, os.path.join(self.tmp, "esysplus"))
        x = U.load_index(CID)[("3000", "116_064_000")]
        self.assertEqual(x["N"][1], "SPEED_MIN")
        self.assertIn("02", x["A"][1])                       # the new 3-bit field's 02 isn't SPEED_MIN's
        quiet(U.realign_cid, CID, self.layouts)
        g, _ = U.load(CID, U.TRANSFERRED_DIR)
        idx = U.transfer_index(g)
        self.assertEqual(U.transfer_match(idx, "3000", 1, 1, "00011111b", {"00", "03", "06"})[0], "SPEED_MIN")
        self.assertIsNone(U.transfer_match(idx, "3000", 0, 0, "00001110b", {"00", "01", "02"}))

    def test_no_reference_layout_no_names(self):
        del self.layouts["3000"][U.layout_hash([f[0] for f in self.REF])]
        n = quiet(U.realign_cid, CID, self.layouts)
        self.assertEqual(n["groups whose reference layout isn't in the definition DBs"], 1)
        g, _ = U.load(CID, U.TRANSFERRED_DIR)
        self.assertFalse(any(e["F"] for e in g.values()))

    def test_marks(self):
        self.assertEqual(U.mark("X"), "X" + U.UNVERIFIED)
        self.assertEqual(U.mark(U.mark("X")), "X" + U.UNVERIFIED)
        self.assertEqual(U.unmark(U.mark("X")), "X")


class ApplyTransferred(ApplyUntrimmed):
    """definitions/untrimmed/transferred: E-Sys Plus's names, never shown as verified."""
    def setUp(self):
        super().setUp()
        lay = [(s, e, m, list(v)) for s, e, m, _, v in PACKED] + [(5, 5, "00000001b", ["00", "01"]),
                                                                 (5, 5, "00000010b", ["00", "01"])]
        self.layout = lay
        names = PACKED + [(5, 5, "00000001b", "NEW_FEATURE", {"00": "nicht_aktiv", "01": "aktiv"}),
                          (5, 5, "00000010b", "HALF_NAMED", {"00": "nicht_aktiv"})]
        key, g = pack_group(lay, names)
        for k in [k for k in g["F"] if k[0] != 5]:      # E-Sys Plus rows only for byte 5
            del g["F"][k]
        g["V"] = {k: v for k, v in g["V"].items() if k[0] == 5}
        # rule prints of the reference's functions (the synthetic DB has no rules: '' for every value)
        g["R"] = {k: {U.rule_print([(x, "") for x in codes])} for k, codes in
                  (((5, 5, "00000001b"), ["00", "01"]), ((5, 5, "00000010b"), ["00", "01"]))}
        quiet(U.save, CID, {key: g}, {}, os.path.join(self.tmp, "transferred"))

    def test_transferred_names_are_marked_and_need_every_value_name(self):
        cpk = self.add_cafd(self.layout, "009_000_001")
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        self.assertEqual(n[(5, 5, "00000001b")][:2], ("NEW_FEATURE" + U.UNVERIFIED, "transferred"))
        self.assertEqual(n[(5, 5, "00000001b")][2]["01"], "wert_02 aktiv [n/a here]")
        # value 01 has no transferred name: E-Sys Plus's field had other values, so the name isn't used at all
        self.assertEqual(n[(5, 5, "00000010b")][:2], ("3000_5_5_2", "derived"))
        comment = self.con.execute("SELECT comment FROM func WHERE cafd_pk=? AND start=5 AND mask='00000001b'",
                                   (cpk,)).fetchone()[0]
        self.assertEqual(comment, U.TRANSFER_NOTE)

    def test_verified_names_are_never_replaced(self):
        cpk = self.add_cafd(self.layout, "009_000_001",
                            named={(5, 5, "00000001b"): ("MINE", "contributed")})
        quiet(B.apply_untrimmed, self.con, self.tmp)
        self.assertEqual(self.names(cpk)[(5, 5, "00000001b")][:2], ("MINE", "contributed"))

    def test_unverified_names_give_way(self):
        cpk = self.add_cafd(self.layout, "009_000_001",
                            named={(5, 5, "00000001b"): ("SHEET_NAME" + U.UNVERIFIED, "cheatsheet")})
        quiet(B.apply_untrimmed, self.con, self.tmp)
        self.assertEqual(self.names(cpk)[(5, 5, "00000001b")][:2], ("NEW_FEATURE" + U.UNVERIFIED, "transferred"))

    def test_bmw_name_on_a_reused_field_is_marked(self):
        # BMW's own layout names only 00 (initwert) for byte 3 0xFC; here it also has 01: reused, so marked
        lay = [(s, e, m, list(v)) for s, e, m, _, v in PACKED]
        lay[2] = (3, 3, "11111100b", ["00", "01"])
        cpk = self.add_cafd(lay, "008_044_023")
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        self.assertEqual(n[(3, 3, "11111100b")][:2], ("UNBELEGT" + U.UNVERIFIED, "untrimmed"))
        self.assertEqual(n[(4, 4, "00000001b")][:2], ("AGS_US", "untrimmed"))


class MarkUnverified(unittest.TestCase):
    def test_definition_counts_only_for_its_own_version(self):
        tmp = tempfile.mkdtemp(prefix="ocm-mark-")
        try:
            p = os.path.join(tmp, "CAFD_00002458_008_044_021.xml")
            with open(p, "w", encoding="utf-8") as f:
                f.write(f'<CafdDefinition cafdId="{CID}" mainVersion="008" subVersion="044" patchVersion="021" '
                        'source="t"><Group id="3000"><Function name="FROM_DEF" start="4" end="4" '
                        'mask="00000001b"/></Group></CafdDefinition>')
            con = sqlite3.connect(":memory:")
            con.executescript(B.SCHEMA)
            for ver in ("008_044_021", "008_044_022"):
                m, s_, pt = (int(x) for x in ver.split("_"))
                cpk = con.execute("INSERT INTO cafd(cafd_id, main, sub, patch, version) VALUES(?,?,?,?,?)",
                                  (CID, m, s_, pt, ver)).lastrowid
                dpk = con.execute("INSERT INTO datagroup(cafd_pk, address) VALUES(?, '3000')", (cpk,)).lastrowid
                con.execute("INSERT INTO func(cafd_pk, dg_pk, address, idx, start, end, mask, name, name_source) "
                            "VALUES(?,?,'3000',0,4,4,'00000001b','x','derived')", (cpk, dpk))
            con.executemany(B.OVERRIDE_INSERT, B.load_definition_xmls([p]))
            quiet(B.apply_overrides, con)
            got = dict(con.execute("SELECT c.version, f.name FROM func f JOIN cafd c ON c.pk=f.cafd_pk"))
            self.assertEqual(got, {"008_044_021": "FROM_DEF", "008_044_022": "FROM_DEF" + U.UNVERIFIED})
            con.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
