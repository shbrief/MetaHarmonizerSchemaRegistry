# Changelog: OmicsMLRepo cBioPortal

All notable changes to the `omicsmlrepo_cbio` schema. Versions follow
[Semantic Versioning](https://semver.org/) with the bump rules in
[docs/target_schema.md](../../docs/target_schema.md#9-versioning). Each version
is tagged `omicsmlrepo_cbio-v<version>`.

Add new entries under a heading `## [x.y.z] - YYYY-MM-DD` matching `meta.yaml`.

## [0.1.0] - 2026-11-24

Baseline: the first versioned state, after the switch from the Google Sheet to
this CSV as the source of truth. Versions below 1.0.0 may still change in
breaking ways; 1.0.0 is planned once the table passes `scripts/lint_schema.py`.

- Starts from the OmicsMLRepo cBioPortal data dictionary (44 fields), frozen in
  `waldronlab/OmicsMLRepoData` on 2026-11-24.
- Changes brought over from OmicsMLRepoData before the freeze (`f9eb62f`):
  narrower `dynamic.enum` roots, `EFO:0000408` (obsolete) replaced by
  `MONDO:0000001`, no root on `age_at_death`, and `disease_metastasic_site`
  renamed to `disease_metastatic_site`.
- Written as comma CSV with columns in the canonical order (`d450d35` and this
  release; no values changed).
- LinkML `version` and `last_updated_on` now come from `meta.yaml`.
