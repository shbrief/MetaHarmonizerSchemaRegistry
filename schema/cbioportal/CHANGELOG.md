# Changelog: cBioPortal clinical attributes

All notable changes to the `cbioportal` schema. Versions follow
[Semantic Versioning](https://semver.org/) with the bump rules in
[docs/target_schema.md](../../docs/target_schema.md#9-versioning). Each version
is tagged `cbioportal-v<version>`.

Add new entries under a heading `## [x.y.z] - YYYY-MM-DD` matching `meta.yaml`.

## [0.1.1] - 2026-11-24

- `cbioportal_schema_internal.csv` is a working list: it stays in this
  directory but is no longer one of the schema's tables, so it is not linted
  or released. The 0.1.0 release still carries it. No values changed.

## [0.1.0] - 2026-11-24

Baseline: the first versioned state, after the switch from the Google Sheet to
this CSV as the source of truth. Versions below 1.0.0 may still change in
breaking ways; 1.0.0 is planned once the table passes `scripts/lint_schema.py`.

- Tables: `cbioportal_schema.csv` (artifacts built as `cbioportal`),
  `cbioportal_schema_clinical_only.csv` and `cbioportal_schema_internal.csv`.
- Written as comma CSV with columns in the canonical order (`d450d35` and this
  release; no values changed).
- LinkML `version` and `last_updated_on` now come from `meta.yaml`.
