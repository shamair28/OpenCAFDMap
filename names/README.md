# names

Community names for trimmed CAFD groups, functions and values. There's one file per ECU (`BDC_BODY3.tsv`, `FLM2.tsv`, ...), and a file can hold several CAFD ids of that ECU.

- Format and how to contribute: [../CONTRIBUTING.md](../CONTRIBUTING.md)
- Priority when two sources disagree:
  1. your private `workspace\my_names.tsv`
  2. these files, and `versioned\*.tsv` for names that hold only for some CAFD versions (a 9th column, `versions`; applied by the next v3, ignored by OpenLauncher v3.0.0). For the versions it covers, a versioned row beats a row here.
  3. BMW's untrimmed names (`definitions\untrimmed\`)
  4. contributed definition XMLs (`definitions\`)
  5. `cheatsheet\*.tsv`: names taken from a cheat sheet that can't be shipped ([cheatsheet/README.md](cheatsheet/README.md))
  6. cheat sheets: the community ones in `definitions\community-cheatsheets\` and any you set up locally
  7. `legacy_*.tsv` (older, unverified names)
- Licence: CC0 ([LICENSE](LICENSE)) for the files directly in this folder. Anyone may reuse these names for anything. `cheatsheet\` has its own note.
