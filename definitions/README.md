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
  - `CAFD_<id>_<main>_<sub>_<patch>.xml` for one software version.
  - `CAFD_<id>.xml` for a file that applies to every version. With no exact match, functions are matched on start/end bytes.
- **How it's used:**
  - *Names*: every real group, function and value name in it is imported for all versions that have the same address, bytes and mask. Placeholders like `Group_3000` / `PfFesMaster_9_9_255` and derived labels like `wert_02 [SA5AC]` are ignored. `wert_02 aktiv [SA5AC]` gives the value name `aktiv`.
  - *Fallback*: if someone's psdzdata doesn't have that CAFD version, E-Sys uses the contributed file directly.
- **Priority:** names files (`names\*.tsv`) > contributed definitions > cheat sheets > legacy names. Your private `my_names.tsv` always wins.
- **Making one:**
  - Export from your build: `python ocm\export_defs.py --ids 000043AF --latest 0 --out definitions\FLM2`.
  - Or hand-write one for the functions you know.
  - Add `source="..."` to the root element.

## 2. `<FDL>`: cheat sheets
FDL cheat-sheet XMLs (`<FDL><cafd id><code description><group id><function start end mask comment>VALUE`). They're imported like local cheat sheets: each entry is checked against your DB, and BMW names in comments (`INBAND_RINGING (value: aktiv)`) become names.

## Rules
- **Check first:** launcher → **Check contributions** (or `python ocm\check_names.py`). The same check runs on every pull request.
- **Not accepted:**
  - decrypted psdzdata files themselves (BMW's own CAFD/FAFP XMLs, `xmlns=".../psdz.data..."`)
  - NCDs, BimmerCode backups, FA/FP/SVT files
  - anything containing a VIN

  The check rejects psdzdata files and VINs.
- **Licence:** only submit files you have the right to share. A file keeps any licence stated in it (e.g. GPL-3.0 for many community cheat sheets). Files without one are contributed under CC0 like `names\`.
