# Changelog: curatedMetagenomicData

All notable changes to the `cmd` schema. Versions follow
[Semantic Versioning](https://semver.org/) with the bump rules in
[docs/target_schema.md](../../docs/target_schema.md#9-versioning). Each version
is tagged `cmd-v<version>`.

Add new entries under a heading `## [x.y.z] - YYYY-MM-DD` matching `meta.yaml`.

## [0.1.0] - 2026-11-24

Baseline: the first versioned state, after the switch from the Google Sheet to
this CSV as the source of truth. Versions below 1.0.0 may still change in
breaking ways; 1.0.0 is planned once the table passes `scripts/lint_schema.py`.

- Starts from `waldronlab/curatedMetagenomicDataCuration`
  `inst/extdata/cMD_data_dictionary.csv` (71 fields).
- Added in the registry: `scope` and `unit.of` (`c7cc8e0`), and
  `propagate.study.value` (TRUE for `location`, `country`, `body_site`,
  `lifestyle`, `westernized`, `ancestry`, `antibiotics_current_use` and
  `disease`). The column restores the values that the committed
  `cmd.linkml.yaml` annotations already carried.
- `disease` and `disease_details`: `Healthy` added to `allowedvalues`, paired
  with `NCIT:C115935` (`f9eb62f`).
- Written as comma CSV with columns in the canonical order (`d450d35` and this
  release; no values changed).
- LinkML `version` and `last_updated_on` now come from `meta.yaml`.
