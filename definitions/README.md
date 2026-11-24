# definitions

Contributed definition XMLs. Put them in a sub-folder per ECU or per contributor, e.g. `definitions/FLM2/CAFD_000043AF_016_020_000.xml`. Two formats are accepted, recognised by their root element.

## 1. `<CafdDefinition>`: Open CAFD Map's definition format
The format the agent reads, used by `export_defs.py` and older hand-made definition files:

```xml
<CafdDefinition schemaVersion="2" cafdId="000043AF" mainVersion="016" subVersion="020" patchVersion="000"
                ecu="FLM2" source="where these names come from">
  <Group id="3002" name="LM_Name">
    <Function name="LM_Name" start="0" end="0" mask="00001111b" comment="what it does">
      <Value idx="0" code="00" label="aktiv" />
    </Function>
  </Group>
</CafdDefinition>
```

- **File name:**
  - `CAFD_<id>_<main>_<sub>_<patch>.xml` for one software version. Its names are shown as verified on that version (from `mainVersion` / `subVersion` / `patchVersion` on the root) and with ` (?)` on every other one, unless `versions="..."` says where they hold.
  - `CAFD_<id>.xml` for a file that applies to every version. With no exact match, functions are matched on start/end bytes.
- **How it's used:**
  - *Names*: every real group, function and value name in it is imported for all versions that have the same address, bytes and mask, or only for the CAFD versions in an optional `versions="..."` on `<CafdDefinition>` (same ranges as `names\versioned\`: `062-`, `-052`, `052-061`, `062_010_106`; see [CONTRIBUTING.md](../CONTRIBUTING.md#names-for-some-cafd-versions-only)). Placeholders like `Group_3000` / `PfFesMaster_9_9_255` and derived labels like `wert_02 [SA5AC]` are ignored. `wert_02 aktiv [SA5AC]` gives the value name `aktiv`.
  - *Fallback*: if someone's psdzdata doesn't have that CAFD version, E-Sys uses the contributed file directly.
- **Priority:** names files (`names\*.tsv`) > BMW's own untrimmed names (`untrimmed\bmw\`) > contributed definitions > E-Sys Plus's transferred names (`untrimmed\transferred\`, only over placeholders and other unverified names) > `names\cheatsheet\*.tsv` > cheat sheets > legacy names. Your private `my_names.tsv` always wins. A name not proven for the CAFD version you open ends in ` (?)` (section 3).
- **Making one:**
  - Export from your build: `python ocm\export_defs.py --ids 000043AF --latest 0 --out definitions\FLM2`.
  - Or hand-write one for the functions you know.
  - Add `source="..."` to the root element.

## 2. `<FDL>`: cheat sheets
FDL cheat-sheet XMLs (`<FDL><cafd id><code description><group id><function start end mask comment>VALUE`). They're imported like local cheat sheets: each entry is checked against your DB, and BMW names in comments (`INBAND_RINGING (value: aktiv)`) become names. The public community sheets are already here, unchanged and under their GPL-3.0 licence: [community-cheatsheets/](community-cheatsheets/README.md).

## Rules
- **Check first:** OpenLauncher → **Check contributions** (or `python ocm\check_names.py`). The same check runs on every pull request.
- **Not accepted:**
  - decrypted psdzdata files themselves (BMW's own CAFD/FAFP XMLs, `xmlns=".../psdz.data..."`)
  - NCDs, BimmerCode backups, FA/FP/SVT files
  - anything containing a VIN

  The check rejects psdzdata files and VINs.
- **Licence:** only submit files you have the right to share. A file keeps any licence stated in it (e.g. GPL-3.0 for many community cheat sheets). Files without one are contributed under CC0 like `names\`.

## 3. `untrimmed\`: untrimmed names
Written by `ocm\untrimmed.py`, one file per CAFD id in each folder:

| Folder | What it holds | How it's shown |
|---|---|---|
| `untrimmed\bmw\<CAFD id>.tsv.gz` | BMW's own names, read from psdzdata old enough to still carry them (PSdZData 54.1 and older) | as they are |
| `untrimmed\<CAFD id>.tsv.gz` | the pack for OpenLauncher v2 and v3.0.0, which only read this folder: `bmw\` plus the realigned names below, BMW's own winning (`untrimmed.py compat`, rewritten after every pack, realign and vet) | as they are by v2 / v3.0.0 (they can't mark names); not read by newer launchers |
| `untrimmed\transferred\raw\` | what E-Sys Plus showed for newer, trimmed CAFD versions (E-Sys 3.35.3 dumps), with the reference it used | never: input for `realign` |
| `untrimmed\transferred\raw\byindex\` | E-Sys Plus's references read directly by function index for every CAFD version (Inspelning's E-Sys Plus 3.8.1 dumps, [discussion #9](https://github.com/shamair28/OpenCAFDMap/discussions/9)): rows `kind address reference function_index value_hex name`, kinds H (group), N (function), P (value), A (a value the reference has no name for) | never: input for `realign` |
| `untrimmed\transferred\<CAFD id>.tsv.gz` | E-Sys Plus's reference names put back on the fields they belong to (`realign`) | always ending in ` (?)` |
| `untrimmed\transferred\rejected\` | realigned names that contradict BMW's own, with the reason in a 9th column | never |

Each row is `kind address layout_hash start end mask value_hex name`:
- **kind** is G (group), F (function), V (value), R (a function's coding-rule print, in `name`) or S (the E-Sys Plus reference a raw layout came from, in `name`).
- **layout_hash** identifies the datagroup's exact function layout. A G row with `*` gives that address's group name for any layout.

**Why E-Sys Plus's names need realigning.** BMW trimmed every name from PSdZData 54.2 on. E-Sys Plus shows names for
newer versions from reference CAFDs of its own, and it looks them up *by position*: "function 13 of group 3001"
([discussion #9](https://github.com/shamair28/OpenCAFDMap/discussions/9)). A reference fits only the layout it was
made for. Wherever BMW inserted or removed a function earlier in the group, every later name lands on the wrong
field: in 00001EF6 006\_064\_014 (HU\_NBT\_EVO), 37 new functions in group 3000 push `SPEEDLOCK_SPEEDVALUE_MIN` onto
a 1-bit field and `AKD_DRIVING_TEXT_LENGTH` onto a 13-byte one. Across the dumps, about a fifth of E-Sys Plus's
names sat on the wrong field.

**How `realign` fixes it** (`python ocm\untrimmed.py realign <definition DBs>`, run by the maintainer with DBs that
hold every version, e.g. `workspace\cafd_defs_G020.sqlite`):
1. Rebuild each reference's names by position from the dumps of every version that used it.
2. Find the version whose layout the reference was made for: function by function, it has every value code E-Sys
   Plus named for that position and none it couldn't name. (A group with a single layout in every version and no
   value names needs no such evidence: there, position and field are the same thing.)
3. Decide every version's layout in the DBs, field by field: a function gets the reference's name for the same
   address, start, end and mask when every value code has a value name there. An on/off flag or reserved bit (two
   value codes or fewer) also needs the reference's coding rules, and the fields around it, up to the nearest
   multi-value field on each side, must be the reference's too: BMW inserting a flag shifts every later flag in the
   byte without changing their shape, values or rules.
4. Store the names per layout. A version that isn't in the DBs (a newer psdzdata) borrows only multi-value fields,
   by field.

Checked against BMW's own names where both exist (before `vet`), 25,640 of 25,769 agree; the 129 others include
BMW's own renames between versions (`*_FUNC` / `*_TYPE`). Groups whose reference layout isn't in the DBs get no
transferred names at all.

Transferred names stay marked ` (?)`: they are E-Sys Plus's names, placed by this matching, not BMW's own names for
your version. **What gets rejected** (`python ocm\untrimmed.py vet`, run after every realign or pack): a name BMW's
own names give a field of another size in this CAFD, a name the reference gives two fields of one layout, and a
field BMW's own package names differently in the same layout. `UNBELEGT`, `Padding*` and other reserved-bit names
are exempt from the size rule.

BMW's own names apply where a datagroup's layout matches exactly. One whose layout matches but which doesn't name
every value code the function has here (a reserved bit that became a feature) is marked ` (?)` too. When a CAFD
version has a layout that isn't packaged (for example one field split in two), each of its functions can still
borrow BMW's own name from a packaged layout with exactly the same address, start, end and mask. All such layouts
must agree on the name, and none may name a value code the function doesn't have. Borrowed names end in ` (?)`
as well.

**` (?)` everywhere means: not proven for this CAFD version, check before coding.** Besides transferred and
borrowed untrimmed names, it marks names from `names\*.tsv`, cheat sheets and contributed definitions on versions
they weren't made for. Names in `my_names.tsv`, `names\versioned\` rows and definitions on their own version
(`versions="..."`, or the version in the file) aren't marked.

To add names: `python ocm\untrimmed.py pack-caf <folder>` for decrypted psdzdata CAFDs that still carry their names
(BMW's own). For E-Sys 3.35.3 dumps: `python ocm\untrimmed.py pack-dumps <folder>` (into `raw\`), then `realign`.
For E-Sys Plus reference reads by index (`<ECU> (Reference_CAF_<id>_<ref>)_<...>.tsv` with `GROUP_ADDR_HEX GROUP_NAME
FUNC_INDEX FUNC_NAME PARAM_INDEX PARAM_NAME PARAM_VALUE`): `python ocm\untrimmed.py pack-esysplus <folder>` (into
`raw\byindex\`), then `realign`. E-Sys Plus names values by value, so a value it names `.` tells `realign` which
codes the reference function doesn't have; a function listing one value twice gets a name per position, and those
value names are dropped. The reference E-Sys Plus picks for a CAFD can differ between machines; every one helps.
Dumps of every reference a CAFD has, and of the version each reference was made for, help most. Packing only adds
names; the check validates every row.
