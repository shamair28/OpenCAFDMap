# Contributing names

Open CAFD Map gets better with every name someone adds. There are three ways to contribute:

- **A few lines in a `names\<ECU>.tsv` file.** Best for names you found yourself, or to fix or fill in anything the untrimmed names lack. These files win over every other source; the format is below.
- **Untrimmed dumps.** If you have untrimmed CAFD definitions (XML with every function, like the dumps in `definitions\untrimmed\` were made from), run `python ocm\untrimmed.py pack-dumps <folder>`. For decrypted CAFDs from a psdzdata old enough to still carry its names, run `python ocm\untrimmed.py pack-caf <folder>` instead. Both merge into `definitions\untrimmed\<CAFD id>.tsv.gz` by layout and never remove existing names. Open a pull request with the changed `.tsv.gz` files.
- **Whole files in `definitions\`**: definition XMLs (`<CafdDefinition>`) or FDL cheat sheets (`<FDL>`) you already have. See [definitions/README.md](definitions/README.md). Their names are merged automatically. A definition XML also covers people whose psdzdata lacks that CAFD version.

## The format
Tab-separated, UTF-8, one name per row:

```
cafd_id   address  start  end  mask       value_hex  name                    comment
00007083  3024                                       Batteryguard            BimmerCode expert mode group list
00007083  3221     13     13   11111111b             FesComfortWorldMode2    mapped E-Sys tree screenshot
00007083  3221     12     12   11111111b  05         mode Comfort+           drive-mode IDs from a coding guide
00007083  3221     9      9    11111111b                                     comment only: what this byte does
```

| Row type | Fill in |
|---|---|
| Group name | `cafd_id`, `address`, `name` |
| Function name | + `start`, `end`, `mask` |
| Value name | + `value_hex` |
| Comment only | function columns + `comment`, empty `name`. Use it when you know what a function does but not its BMW name. |

- **cafd_id**: 8 hex digits (`00007083`). **address**: the datagroup, 4 hex digits (`3221`).
- **start / end / mask**: the function's position. Copy them from the Backup Editor's detail pane (`byte 9  mask 11111111b`), or from the placeholder name. `PfFesMaster_9_9_255` is start 9, end 9, mask 255 = `11111111b`; write the mask in binary with a trailing `b`.
- **name**: BMW's own name if you know it (`FesSportPlusMode`, `INBAND_RINGING`). Otherwise a short, clear description.
- **comment**: **where the name comes from** (screenshot of tool X, BimmerCode diff, coding table, forum thread). This lets others judge and verify it.

Names don't depend on the software version. A row applies to every version of that CAFD that has a function with exactly that address, byte range and mask.

## Where names come from
- **BimmerCode backup diffs.** Take a backup, change one option in BimmerCode, take another backup, then run:

  ```
  python ocm\cod_import.py --sheet
  ```

  It lists exactly which parameter changed. Write the BimmerCode option name into the sheet, run `python ocm\cod_import.py --apply` (it goes into your private `workspace\my_names.tsv`), then press **Apply names**.
- **Screenshots and tables** of tools that still show names, with their source.
- **Your own testing.** Comment-only rows are welcome.

Move rows you're confident about from `my_names.tsv` into the right `names\<ECU>.tsv` (create the file if the ECU has none yet) and open a pull request.

## Before you open a pull request
1. Press **Check contributions** in OpenLauncher (or run `python ocm\check_names.py --db`). There must be no errors, and your rows should match your DB.
2. **Names and definition files only.** Never commit:
   - psdzdata or decrypted psdzdata files (BMW's own CAFD/FAFP XMLs)
   - your workspace DB
   - NCDs, BimmerCode backups, FA/FP/SVT files
   - VINs or anything else from your car

   `.gitignore` blocks the usual file types and the check rejects psdzdata files and VINs, but please look at your diff.
3. Only submit files you have the right to share, and keep their licence notice (many community cheat sheets are GPL-3.0).

Don't edit `definitions.json`. After your pull request is merged, GitHub bumps the definitions version automatically, and everyone gets your names through OpenLauncher's **Check for updates** / **Update now**.

`names\legacy_*.tsv` holds older, unverified names. They rank below everything else, so a verified name in a normal file replaces them automatically.

## Code
Bug fixes and improvements to the tools are welcome too. Keep the launcher-only rule: nothing may be installed into, patched into or written to the E-Sys folder.

## Branches and releases (maintainers)
| Branch | What | Releases (tags) |
|---|---|---|
| `main` | OpenLauncher v3, and the **names for every version**: every install (v2 and v3) downloads `names\`, `definitions\`, `data\` from `main`. Name pull requests go here. | `v3.x.y`, GitHub's *latest* release |
| `v3-testing` | the next v3 version and its tests; merge `main` into it regularly | `v3.x.y-rc.N`, pre-releases "(v3 preview)" |
| `v2` | OpenLauncher v2 maintenance (fixes only) | `v2.x.y`, "(v2)", never *latest* |

Keep the name `main`: installed launchers fetch name updates from it by name.

Tag a version on its branch and push the tag:

```
git tag -a v3.0.1 -m "OpenLauncher v3.0.1"
git push origin v3.0.1
```

`.github/workflows/release.yml` (the same file on every branch) checks the names and the manifest, builds `OpenCAFDMap.zip` (+ `.sha256`) with `installer/build_release.py`, and publishes the release for the tag's channel. Tag after the definitions bot's commit when names changed (`defs_manifest.py --check` must pass).

**Which update an install gets** (`ocm/updater.py`): an install follows the newest release of its *own* major version, never GitHub's *latest* release, and setup is always given the exact tag. v2 stays on v2 until the user picks **Try OpenLauncher v3**; a v3 install follows v3; a v3 preview install also follows newer previews (**Try the preview** / **Back to stable**). Setup run by hand installs the latest release; on an existing v2 install it asks whether to upgrade to v3 or stay on v2, and the setup attached to v2 releases installs v2. OpenLauncher v2.3.10 and older (which take GitHub's latest release) are kept on v2 by setup itself. Name-only changes don't need a release: **Check for updates** picks them up from `main` for every version.
