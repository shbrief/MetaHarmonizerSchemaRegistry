# Changelog: curatedMetagenomicData

All notable changes to the `cmd` schema. Versions follow
[Semantic Versioning](https://semver.org/) with the bump rules in
[docs/target_schema.md](../../docs/target_schema.md#9-versioning). Each version
is tagged `cmd-v<version>`.

Add new entries under a heading `## [x.y.z] - YYYY-MM-DD` matching `meta.yaml`.

## [0.2.0] - 2026-11-24

- `disease`, `disease_details`, `target_condition`: the obsolete root
  `EFO:0000408` (disease, obsolete since EFO 3.88.0) is replaced by
  `MONDO:0000001` (disease), as was done for cBioPortal. MetaHarmonizer
  skipped the obsolete root, so these fields gain the MONDO disease corpus.
  The descriptions name the new root.
- `location` and `ncbi_accession`: now multi-valued with `;` as the
  delimiter, as in the curated data (for example `Dhaka;Nanbipara;peri-urban`
  and `ERR1913108;ERR1913109`). The `ncbi_accession` regexp applies to each
  accession.
- New optional column `value.of`: the field whose values this field records,
  item by item. Set for `probing_pocket_depth_value` (`probing_pocket_depth`),
  `feces_phenotype_value` (`feces_phenotype`) and `biomarker_value`
  (`biomarker_name`). With the existing `unit.of` (`biomarker_unit` →
  `biomarker_value`) it marks parallel lists, which must share `multiplevalues`
  and `delimiter`.
- `uncurated_metadata`: `separator` is `:`, as its items are `key:value`
  (`BASDAI:6.8<;>BASFI:0.9`).
- `cmd.linkml.yaml` carries the new `value_of` and `separator` annotations.

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
