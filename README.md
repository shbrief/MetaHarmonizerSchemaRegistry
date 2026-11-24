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
  cbioportal/            cBioPortal attributes (from Google Sheet)
  omicsmlrepo_cbio/      cBioPortal via the OmicsMLRepo data dictionary
  curatedmetagenomicdata/ cMD data dictionary (from waldronlab)
  gdc/                   GDC columns (built from observed values, no LinkML source)
scripts/                 the artifact builders, validators and lint (R + Python)
docs/target_schema.md    how to prepare a schema table (the convention + what MetaHarmonizer reads)
get_schema_to_deploy.qmd how each schema table is pulled and how artifacts are built
```

Within each `schema/<source>/` directory:

| File | What it is | Produced by |
|------|-----------|-------------|
| `<prefix>_schema.csv` | **Source schema table** (comma-separated CSV, `write.csv()`) | pulled from a Google Sheet / data dictionary (see the `.qmd`) |
| `<prefix>.linkml.yaml` | LinkML schema — validation + metacurator | `build_schema_artifacts.{R,py}` |
| `<prefix>_target_attrs.csv` | SchemaMapper target schema (`field_name,is_numeric_field`) | `build_schema_artifacts.{R,py}` |
| `<prefix>_target_attrs_allowed_values.json` | SchemaMapper value dictionary (enum fields → `{field: [values]}`) | `build_schema_artifacts.{R,py}` |
| `<prefix>_target_attrs_alias_haiku.csv` (+ `.meta.json`) | SchemaMapper alias dictionary (LLM-generated) | `build_schema_artifacts.{R,py}` (needs an API key) |
| `corpus/<field>[_<ontology>]_corpus.csv` + `<prefix>_ontology_terms.json` | OntoMapEngine corpus + validation term lists | `build_ontology_artifacts.py` (needs network) |

> **GDC is an exception.** It has no LinkML source. Its `target_attrs.csv` and
> value dict are derived directly from `schema/gdc/source/gdc_unique_columns_concat_values.csv`
> (736 columns with their observed unique values) rather than from a `_schema.csv`.

## Workflow

The end-to-end pipeline — pull each schema table, build the artifacts, validate
data against a schema — is documented in
[get_schema_to_deploy.qmd](get_schema_to_deploy.qmd), with per-script usage in
[scripts/README.md](scripts/README.md). The short version:

### 1. Get the schema table

Each schema originates in a Google Sheet or an upstream data dictionary and is
written to `<prefix>_schema.csv` with R's `write.csv(..., row.names = FALSE)`
(comma-separated — the format every downstream script and MetaHarmonizer's
`harmonize(schema_table=)` expect; the scripts still read the old
space-separated `write.table()` layout). See the
`get_schema_to_deploy.qmd` chunks for the specific sources and column subset.

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

- **R:** `OmicsMLRepoCuration`, `jsonlite` (+ `MetaHarmonizerR` for the alias step).
- **Python:** `pandas`, `pyyaml` (+ `metaharmonizer` for the alias step).

The R path delegates the LinkML build to `OmicsMLRepoCuration::tableToLinkmlSchema`;
the Python builders are faithful ports producing semantically identical output.
See [scripts/README.md](scripts/README.md) for full details on each script.
