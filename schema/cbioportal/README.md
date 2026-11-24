# cBioPortal schema tables

This directory holds **three** cBioPortal schema tables. They are not versions of
one another in the "newer replaces older" sense — they differ in *provenance* and
in *breadth*, and each one is the right input for a different job. Only one of
them currently has derived artifacts built from it.

All three use the registry's schema-table format: comma-separated CSV written
by `write.csv(..., row.names = FALSE)`, with `NA` for empty cells. It is read by
`scripts/build_schema_artifacts.{R,py}`, `scripts/schema_validation.{R,py}` and
MetaHarmonizer's `harmonize(schema_table=)`.

## At a glance

| File | Attributes | Cols | Source | Covers |
|------|-----------:|-----:|--------|--------|
| `cbioportal_schema.csv` | 52 | 13 | Google Sheet, `cbioportal` tab (`mh.use == "T"`) | Clinical Data + Timeline Data + mutation `ASCN.*` |
| `cbioportal_schema_clinical_only.csv` | 32 | 15 | [docs.cbioportal.org/file-formats/#clinical-data](https://docs.cbioportal.org/file-formats/#clinical-data) | Clinical Data only |
| `cbioportal_schema_internal.csv` | 4,615 | 15 | cBioPortal Clinical Data Dictionary (MSKCC) | every known clinical attribute |

A fourth cBioPortal view lives in a sibling directory: `schema/omicsmlrepo_cbio/`
is cBioPortal as harmonized by the OmicsMLRepo data dictionary, not a copy of
anything here.

## `cbioportal_schema.csv` — the registry's active cBioPortal schema

**This is the table the derived artifacts in this directory were built from.**
`cbioportal.linkml.yaml`, `cbioportal_target_attrs.csv` (52 fields),
`cbioportal_target_attrs_allowed_values.json` (9 enum fields) and
`cbioportal_target_attrs_alias_haiku.csv` (863 alias rows over 52 target fields)
all trace back to it. Don't rebuild them from a different table without renaming
the outputs.

Pulled from the Google Sheet, `cbioportal` tab, filtered to `mh.use == "T"` — see
the `cbio_schema_from_google_sheet` chunk in `../../get_schema_to_deploy.qmd`.

It is the **broadest file-format coverage** of the three, because it spans three
different sections of the cBioPortal file-formats page:

- Clinical Data (30 attributes)
- Timeline Data (19): `EVENT_TYPE`, `START_DATE`, `STOP_DATE`, `TL_PATIENT_ID`,
  `TL_SAMPLE_ID`, `TL_RESULT`, `TL_SOURCE`, `STYLE_COLOR`, `STYLE_SHAPE`,
  `AGENT`, `AGENT_CLASS`, `TREATMENT_TYPE`, `SUBTYPE`, `TEST`, `STATUS`,
  `SPECIMEN_SITE`, `SPECIMEN_TYPE`, `DIAGNOSTIC_TYPE`, `DIAGNOSTIC_TYPE_DETAILED`
- Mutation Data, `ascn` namespace (3): `ASCN.CCF_EXPECTED_COPIES`,
  `ASCN.CCF_EXPECTED_COPIES_UPPER`, `ASCN.EXPECTED_ALT_COPIES`

**Caveats.** It has 13 columns — no `scope`, no `unit.of` — so it cannot say
whether an attribute belongs in the patient file or the sample file, and the
LinkML build emits no `scope` annotation for it. Its descriptions are one-liners
that omit the `0:`/`1:` survival-prefix rule, the `_STATUS`/`_MONTHS` pairing
requirement and the Study View default priorities.

## `cbioportal_schema_clinical_only.csv` — the clinical-data spec, verbatim

Transcribed directly from the **Clinical Data** section of the cBioPortal
file-formats documentation, so it is the most faithful statement of what that
section actually requires. Use it when correctness of the clinical contract
matters more than breadth: validating a `data_clinical_patient.txt` /
`data_clinical_sample.txt` pair, or as the authoritative reference when
reconciling the other two tables.

What it adds over `cbioportal_schema.csv`:

- **15 columns** — the canonical `schema_cols` set from `get_schema_to_deploy.qmd`,
  including `scope` and `unit.of`. `unit.of` is `NA` throughout (the clinical spec
  defines no unit-carrying attributes).
- **`scope`** — `patient` (12), `sample` (8), `either` (12), matching the docs'
  own split into "Clinical patient columns", "Clinical sample columns" and
  "Columns with specific functionality" (which may sit in either file).
- **`PFS_STATUS` / `PFS_MONTHS`** — the pair the docs name when describing the
  generic `<PREFIX>_STATUS` + `<PREFIX>_MONTHS` survival convention. Since no
  value list is documented for PFS, the status is a `regexp` (`^(0|1):.+`) rather
  than an enum.
- **Fuller descriptions** — the `0:` (no event) / `1:` (event occurred) prefix
  rule, the green/red patient-view labels, the survival-pair requirement, which
  columns the pan-cancer summary tab needs, `METASTATIC_SITE`/`PRIMARY_SITE`
  overriding `TUMOR_SITE`, and the Study View default priorities (CANCER_TYPE
  3000, CANCER_TYPE_DETAILED 2000, OS 400, DFS 300, GENDER/SEX 9, AGE 8).
- **`AGE` as `double`/`numeric`** rather than `integer` — the 5-row clinical
  header declares it `NUMBER`.

**Caveats.** It is clinical-data only: the 19 timeline and 3 `ASCN.*` attributes
of `cbioportal_schema.csv` are absent by design, and with them four of that
table's five `required` flags and four of its nine enum value sets. It is *not* a
drop-in replacement for the active schema. Excluded on purpose: `MUTATION_COUNT`
and `FRACTION_GENOME_ALTERED` (banned — cBioPortal auto-populates them),
`OTHER_SAMPLE_ID` (no longer supported; use `SAMPLE_ID`), and `SUBTYPE` (only an
example custom column in the docs).

## `cbioportal_schema_internal.csv` — the full clinical data dictionary

Every clinical attribute cBioPortal knows about, from the MSKCC Clinical Data
Dictionary that the docs point to for normalizing clinical data across studies.
4,615 attributes, `scope` split `sample` 2,377 / `patient` 2,238.

Use it for **recall**: SchemaMapper matching against real-world study columns,
where an incoming header is far more likely to hit one of these 4,615 names than
one of the 32 core ones.

Richer than the other two where it overlaps them: 982 numeric attributes, 3,633
attributes carrying `allowedvalues`, descriptions that record the upstream
datatype and the Study View priority, and real controlled vocabularies on
`CANCER_TYPE`, `CANCER_TYPE_DETAILED` and `ONCOTREE_CODE` (OncoTree main types
and codes, with NCIt meanings in `static.enum` where OncoTree records one). 21
attributes are flagged in their description as cbioportal-core validator
predefined attributes.

**Caveats.** Not yet wired into `get_schema_to_deploy.qmd`, and no artifacts are
built from it. Being a dictionary rather than a file-format spec, it carries only
2 `required` flags (`PATIENT_ID`, `SAMPLE_ID`) and 5 enum fields — the
constraints that make a clinical file *valid* are in the other two tables. It
also contains no timeline attributes. At 4,615 fields, a Haiku alias dictionary
over it would be roughly 90× the cost of the current one.

## Which one do I want?

| Goal | Table |
|------|-------|
| Rebuild the artifacts the ecosystem consumes today | `cbioportal_schema.csv` |
| Validate a clinical patient/sample file against the spec | `cbioportal_schema_clinical_only.csv` |
| Know whether an attribute is patient- or sample-level | `cbioportal_schema_clinical_only.csv` (core) or `cbioportal_schema_internal.csv` (any) |
| Validate timeline or `ASCN.*` data | `cbioportal_schema.csv` |
| Maximize SchemaMapper recall on unseen study columns | `cbioportal_schema_internal.csv` |
| cBioPortal as OmicsMLRepo harmonizes it | `../omicsmlrepo_cbio/` |

## Rebuilding artifacts

The active artifacts, regenerated in place (step 4 needs `ANTHROPIC_API_KEY`):

```bash
python scripts/build_schema_artifacts.py \
    --input schema/cbioportal/cbioportal_schema.csv --no-alias
```

Artifact names come from `--prefix`, which defaults to the input filename minus a
trailing `_schema.csv`. The three tables here are named `cbioportal_schema*.csv`
so they sort and group together, which means only `cbioportal_schema.csv` hits
that default; the two variants would fall back to their whole stem. **Always pass
`--prefix` explicitly for them**, both to keep the names tidy and to keep their
outputs clear of the active set:

```bash
python scripts/build_schema_artifacts.py \
    --input schema/cbioportal/cbioportal_schema_clinical_only.csv \
    --prefix cbioportal_clinical_only --no-alias

python scripts/build_schema_artifacts.py \
    --input schema/cbioportal/cbioportal_schema_internal.csv \
    --prefix cbioportal_internal --no-alias
```

`build_schema_artifacts.R` takes the same flags and produces semantically
identical output. See `../../README.md` for the registry-wide pipeline and
`../../scripts/README.md` for per-script usage.
