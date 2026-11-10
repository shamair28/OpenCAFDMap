# BimmerCode Backup Editor

Prepare coding changes as **new** BimmerCode backups, then restore them from your phone with BimmerCode. Your real backups are never touched.

This works with BimmerCode **iOS** backups (`.cod`, `<ECU>-<VIN>-<unix time>.cod`). Android's backup format isn't supported yet.

## Setup
1. In OpenLauncher, set **BimmerCode backups** to the folder where you copied BimmerCode's backups from the phone, then press **Save settings**.
2. Build the definitions, if you haven't yet.
3. Open **BimmerCode Backup Editor**, then press **Import new backups from source folder**. This copies them into `workspace\bimmercode\`:

```
originals\   copies of your backups (never written; "Check originals" verifies them against the source)
decoded\     one .txt (every parameter, name, value, label) + one .json (raw blocks) per backup
edits\       edit files for the command line (EXAMPLE.tsv)
out\         new backups made by the editor (+ a .txt log next to each)
```

## Using the editor
1. Pick a backup on the left. Use the newest one for the ECU you want to change.
2. Find the parameter with Search, Group, or "named only".
   - The details pane shows its position, mask, description and every value (`*` = current).
   - Long values (light curves, staging data) can be side-scrolled.
3. Double-click a value (or press "Stage selected value") to stage it.
   - A custom hex value needs "allow custom". It must have exactly the parameter's byte length; it's never padded.
   - Staged rows turn yellow.
4. Add a note, then press **Write new backup**. The new `.cod` goes to `out\` and opens, so you can check it or stage more on top of it.

Ctrl-click two backups and press **Diff** to see exactly which parameters differ.

New files are named like BimmerCode's (`<ECU>-<VIN>-<current unix time>.cod`), so they show up as the newest backup for that ECU. Copy them to BimmerCode's backup folder on the phone and restore them in BimmerCode like any backup.

## What the editor guarantees
- The new `.cod` is a byte copy of the source backup with **only the edited coding bytes changed**: same size, same plist structure, VIN, ECU and CAFD ids. Every write is re-read and checked, and discarded if anything else changed.
- Bit fields are edited bit-exactly (other bits in the same byte are kept).
- Multi-CAFD ECUs (BDC, ACSM, ...) are handled: every CAFD listed in the backup is used.
- SYSTEM datagroups (the NCD signature) can't be edited.
- `originals\` is never written.

## Read before restoring
1. **Signature.** Each backup contains the ECU's NCD signature block (e.g. FLM2 37F1, BDC 3456, MGU 3016). BimmerCode computes a new one on every change it makes; this editor doesn't compute or forge signatures. Edited backups therefore carry the **source backup's signature**.
   - If BimmerCode re-signs when it restores, restoring works normally.
   - If it writes the stored blocks as-is, the ECU may refuse the coding or report a coding fault. Restoring the unedited original backup returns the ECU to its previous state.
   - **Try one low-risk change first** (e.g. a head-unit display option) before anything safety- or drive-related.
2. **Same software only.** A backup applies to the CAFD version it was read from. After an ECU update, take a fresh BimmerCode backup and edit that.
3. **Multi-ECU features** often need a change in several ECUs (e.g. a drive mode in the BDC *and* the head unit). Make and restore every backup involved.
4. **Names come from the definition DB.** Community names, cheat sheets you imported locally, and labels derived from BMW's coding rules (`[SA9XA]` = the factory option that normally selects that value).

## Command line
```
python ocm\bc_tool.py list
python ocm\bc_tool.py show latest:BDC_BODY3 --group 3221
python ocm\bc_tool.py values latest:BDC_BODY3 3221:12:12:11111111b
python ocm\bc_tool.py set latest:BDC_BODY3 "3221:12:12:11111111b=mode Comfort+" --note "test"
python ocm\bc_tool.py apply latest:HU_MGU workspace\bimmercode\edits\my_changes.tsv
python ocm\bc_tool.py diff <backup A> <backup B>
```
`<backup>` is `latest:<ECU name | 0x63 | CAFD id>` or any unique part of a file name. Values can be hex, a label, or `wert_NN`. Values outside the CAFD's list are refused unless you add `--allow-custom`.
