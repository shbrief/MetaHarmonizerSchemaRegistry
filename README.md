# MetaHarmonizer Schema Registry

Source of record for the metadata schemas used across the **MetaHarmonizer**
ecosystem, plus the scripts that turn each schema into the downstream artifacts
that MetaHarmonizer's validation, matching, and curation tools consume.

Each data source (cBioPortal, GDC, curatedMetagenomicData, …) has one schema
table. From that single table the registry generates a LinkML schema, the
SchemaMapper target/value/alias dictionaries, and — where fields are backed by
ontologies — the ontology corpus and term lists. Keeping all of this in one repo
means the schemas and their derived artifacts stay versioned together.

## Repository layout

```
schema/
  cbioportal/            cBioPortal attributes
  omicsmlrepo_cbio/      cBioPortal via the OmicsMLRepo data dictionary
  curatedmetagenomicdata/ cMD data dictionary (from waldronlab)
  gdc/                   GDC columns (built from observed values, no LinkML source)
scripts/                 the artifact builders, validators and lint (R + Python)
docs/target_schema.md    how to prepare a schema table (the convention + what MetaHarmonizer reads)
.github/workflows/       CI: schema checks on every PR/push, version tags on main
```

Within each `schema/<source>/` directory:

| File | What it is | Produced by |
|------|-----------|-------------|
| `<prefix>_schema.csv` | **Source schema table, the source of truth** (comma CSV, `write.csv()`) | edited by pull request |
| `meta.yaml` | Schema id, version, date, tables, lint mode, ontology versions | edited with every schema change |
| `CHANGELOG.md` | One entry per version | edited with every schema change |
| `<prefix>.linkml.yaml` | LinkML schema — validation + metacurator | `build_schema_artifacts.{R,py}` |
| `<prefix>_target_attrs.csv` | SchemaMapper target schema (`field_name,is_numeric_field`) | `build_schema_artifacts.{R,py}` |
| `<prefix>_target_attrs_allowed_values.json` | SchemaMapper value dictionary (enum fields → `{field: [values]}`) | `build_schema_artifacts.{R,py}` |
| `<prefix>_target_attrs_alias_haiku.csv` (+ `.meta.json`) | SchemaMapper alias dictionary (LLM-generated) | `build_schema_artifacts.{R,py}` (needs an API key) |
| `corpus/<field>[_<ontology>]_corpus.csv` + `<prefix>_ontology_terms.json` | OntoMapEngine corpus + validation term lists | `build_ontology_artifacts.py` (needs network) |

> **GDC is an exception.** It has no LinkML source. Its `target_attrs.csv` and
> value dict are derived directly from `schema/gdc/source/gdc_unique_columns_concat_values.csv`
> (736 columns with their observed unique values) rather than from a `_schema.csv`.

## Workflow

Per-script usage is in [scripts/README.md](scripts/README.md). The short version:

### 1. Edit the schema table

**The `<prefix>_schema.csv` files in this repository are the source of truth**
(since 2026-11-24). The Google Sheets they used to be pulled from are archived,
and the sync code is commented out. To change a schema:

1. On a branch, edit the table (see [docs/target_schema.md](docs/target_schema.md)
   for the convention), then run `Rscript scripts/format_schema.R` so the file
   stays in canonical form.
2. Rebuild its artifacts (step 2 below) and run `python scripts/check_schemas.py`.
3. Bump `version` and `date` in the schema's `meta.yaml` and add a
   `CHANGELOG.md` entry ([Versioning](#versioning)).
4. Open a pull request. CI runs the same checks.

### 2. Build the downstream artifacts

`build_schema_artifacts.R` and `build_schema_artifacts.py` are equivalent
implementations that turn one `<prefix>_schema.csv` into the LinkML schema and
the SchemaMapper target/value/alias files. Artifacts 1–3 are deterministic and
offline; the alias dictionary calls an LLM (Claude Haiku) and needs
`ANTHROPIC_API_KEY`.

```bash
# Python — reads schema/cbioportal/cbioportal_schema.csv, writes the 4 artifacts
python scripts/build_schema_artifacts.py --no-alias   # steps 1-3 only (offline)

# R — same input/output
Rscript scripts/build_schema_artifacts.R --no-alias

# generate the alias dictionary too (step 4; calls the LLM, costs tokens)
export ANTHROPIC_API_KEY=sk-...
python scripts/build_schema_artifacts.py --model claude-haiku-4-5-20251001

# a different schema
python scripts/build_schema_artifacts.py --input schema/gdc/gdc_schema.csv --no-alias
```

Shared flags: `--input`, `--outdir` (default: dir of `--input`), `--prefix`
(default: input filename minus `_schema.csv`), `--model`, `--schema-domain`
(default `cancer_genomics`), `--no-alias`.

For ontology-backed fields (`dynamic.enum` / `static.enum`),
`build_ontology_artifacts.py` materializes the OntoMapEngine corpus and the
validation term lists. It calls public ontology APIs (NCIt via NCI EVSREST, all
others via EBI OLS4 — both keyless), so it needs network access:

```bash
python scripts/build_ontology_artifacts.py \
    --input schema/curatedmetagenomicdata/cmd_schema.csv --prefix cmd
```

### 3. Validate data against a schema

`schema_validation.R` and `schema_validation.py` are equivalent CLIs that check
a data CSV against a `<prefix>_schema.csv` — required fields, types, patterns,
controlled vocabularies — printing a report and exiting non-zero on failure. No
files are written.

```bash
python scripts/schema_validation.py \
    --schema schema/cbioportal/cbioportal_schema.csv \
    --data   my_records.csv
```

Shared flags: `--schema`, `--data` (omit for a schema self-check), `--strict`.
Exit status: `0` valid, `1` invalid, `2` usage/IO error.

## Versioning

Each schema is versioned on its own with [SemVer](https://semver.org/). The
version lives in `schema/<source>/meta.yaml`, and each version is tagged
`<schema>-v<version>` (for example `cmd-v0.1.0`) with a GitHub release that
carries the schema's files and its CHANGELOG entry. The tag is created by CI
after the checks pass on `main`; nobody tags by hand.

| Bump | When |
|---|---|
| Major | A field removed or renamed, an allowed value removed, `col.class` changed, a field made required, roots narrowed |
| Minor | A field, allowed value or root added; a missing term ID filled in |
| Patch | Descriptions and typos that change no value |

Versions below 1.0.0 may still change in breaking ways. Each schema reaches
1.0.0 once its table passes the lint, at which point its `meta.yaml` switches to
`lint: blocking`.

**Consumers pin a tag**, never `main`, for example
`https://raw.githubusercontent.com/shbrief/MetaHarmonizerSchemaRegistry/cmd-v0.1.0/schema/curatedmetagenomicdata/cmd_schema.csv`.

## Schema table conventions

The full convention, and what MetaHarmonizer reads from each column, is in
[docs/target_schema.md](docs/target_schema.md). `scripts/lint_schema.py`
enforces it. The rules below are the ones the artifact builders depend on.

- **Delimiter.** `|` is the single list delimiter across the schema: enum
  `allowedvalues`, positional `static.enum` ids, multi-node `dynamic.enum` roots,
  and compound `corpus.type` tokens. (`;` / `<;>` in the `delimiter` / `separator`
  columns are data-cell separators, unrelated.)
- **Field naming.** `field_name` is preserved verbatim from `col.name` (raw
  attribute names, e.g. `SAMPLE_TYPE`) so the three `*_target_attrs*` files stay
  internally consistent.
- **Numeric flag.** `is_numeric_field` is `yes` when `col.class` is `integer`,
  `double`, or `numeric`, else `no`.
- **Enum extraction.** A field contributes to the value dict when its
  `corpus.type` contains an enum token (`static_enum`, `custom_enum`, `binary`),
  including compound types like `dynamic_enum|static_enum`.

## Dependencies

- **R:** `OmicsMLRepoCuration`, `jsonlite`, `yaml` (+ `MetaHarmonizerR` for the alias step). `format_schema.R` needs base R only.
- **Python:** `pandas`, `pyyaml` (+ `metaharmonizer` for the alias step).

The R path delegates the LinkML build to `OmicsMLRepoCuration::tableToLinkmlSchema`;
the Python builders are faithful ports producing semantically identical output.
See [scripts/README.md](scripts/README.md) for full details on each script.
