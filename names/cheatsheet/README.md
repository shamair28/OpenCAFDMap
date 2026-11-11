# names/cheatsheet

`fdl_names.tsv`: BMW setting names (FDL identifiers such as `AMBIENTE_RGB_LAUFLICHT_BEI_TUEROEFFNEN`)
and value names (`aktiv`, `nicht_aktiv`) taken from a third-party E-Sys cheat sheet whose file can't be
redistributed. Only BMW's own identifiers were kept; the sheet's descriptions were left out.

- Same format as the files in `names\` (see [../../CONTRIBUTING.md](../../CONTRIBUTING.md)).
- **Priority:** below `names\*.tsv` and contributed definitions, above imported cheat sheets
  (`definitions\community-cheatsheets\` and your own `cheat_sheets_dir`).
- **Corrections** go in `names\<ECU>.tsv`, not here: those files win.
