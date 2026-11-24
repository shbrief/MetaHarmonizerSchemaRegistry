# Schema artifact builder

`build_schema_artifacts.R` and `build_schema_artifacts.py` are equivalent
implementations that turn a MetaHarmonizer schema table (the
comma-separated CSV written with `write.csv()` in `get_schema_to_deploy.qmd`, e.g.
`schema/cbioportal/cbioportal_schema.csv`) into the downstream artifacts the
ecosystem consumes:

| # | Output                                        | Consumer                     | Source of logic |
|---|-----------------------------------------------|------------------------------|-----------------|
| 1 | `<prefix>.linkml.yaml`                        | validation, **metacurator**  | `OmicsMLRepoCuration::tableToLinkmlSchema` (R) / a faithful port (Python) |
| 2 | `<prefix>_target_attrs.csv`                   | **SchemaMapper** target schema | inline (`field_name,is_numeric_field`) |
| 3 | `<prefix>_target_attrs_allowed_values.json`  | **SchemaMapper** value dict  | inline (enum fields → `{field: [values]}`) |
| 4 | `<prefix>_target_attrs_alias_haiku.csv` (+ `.meta.json`) | **SchemaMapper** alias dict | `MetaHarmonizerR::generate_alias_dict` (R) / `metaharmonizer.models.schema_mapper.generate_alias_dict` (Python) — **LLM, needs an API key** |

Artifacts 1–3 are deterministic and offline. Artifact 4 calls an LLM (Claude
Haiku by default) and reads `ANTHROPIC_API_KEY` from the environment; without a
key (or with `--no-alias`) the first three are still produced.

## Usage

**Input:** a single `*_schema.csv` (`--input`, default
`schema/cbioportal/cbioportal_schema.csv`).   
**Output:** the four files in the table above, written to `--outdir` (default:
the directory of `--input`) and named by `--prefix`. Steps 1–3 always run;
step 4 runs only when `ANTHROPIC_API_KEY` is set and `--no-alias` is absent.

```bash
# Python — reads schema/cbioportal/cbioportal_schema.csv, writes the 4 artifacts
python scripts/build_schema_artifacts.py            # defaults to cbioportal
python scripts/build_schema_artifacts.py --no-alias # skip the LLM step (steps 1-3 only)

# R — same input/output as above
Rscript scripts/build_schema_artifacts.R --no-alias

# generate the alias dictionary too (step 4; costs tokens)
export ANTHROPIC_API_KEY=sk-...
python scripts/build_schema_artifacts.py --model claude-haiku-4-5-20251001

# a different schema: input schema/gdc/gdc_schema.csv -> outputs prefixed "gdc"
python scripts/build_schema_artifacts.py --input schema/gdc/gdc_schema.csv --no-alias
```

Flags (identical in both): `--input`, `--outdir` (default: dir of `--input`),
`--prefix` (default: input filename minus `_schema.csv`), `--model`,
`--schema-domain` (default `cancer_genomics`), `--no-alias`.

## Notes

- **Field naming.** `field_name` is preserved verbatim from the schema's
  `col.name` (the raw cBioPortal attribute names, e.g. `SAMPLE_TYPE`). The
  three `*_target_attrs*` files are internally consistent so SchemaMapper maps
  incoming columns onto these exact names.
- **Enum extraction.** A field contributes to the value dict when its
  `corpus.type` contains an enum token (`static_enum`, `custom_enum`, `binary`) —
  including compound types like `dynamic_enum|static_enum`; its `allowedvalues`
  are split on `|`.
- **Delimiter.** `|` is the single list delimiter across the schema: enum
  `allowedvalues`, positional `static.enum` ids, multi-node `dynamic.enum`
  roots, and compound `corpus.type` tokens all use it. (`;`/`<;>` in the
  `delimiter`/`separator` columns are data-cell separators, unrelated.)
- **Numeric flag.** `is_numeric_field` is `yes` when `col.class` is `integer`,
  `double`, or `numeric`, else `no`.
- **R vs Python parity.** Both emit identical `slots`, `enums`, `classes`, the
  same `target_attrs.csv`, and the same value dict. The R path delegates the
  LinkML build to `OmicsMLRepoCuration`; reinstall that package from source to
  pick up its latest LinkML features (e.g. `static.enum` ontology `meaning`s and
  `dynamic.enum` `reachable_from` blocks), which the Python port already mirrors.
- **Dependencies.** R: `OmicsMLRepoCuration`, `jsonlite` (+ `MetaHarmonizerR`
  for step 4). Python: `pandas`, `pyyaml` (+ `metaharmonizer` for step 4).


# Ontology corpus & term artifacts

`build_ontology_artifacts.py` materializes the **ontology-backed** layers of a
schema — the network-dependent companion to `build_schema_artifacts.py`. For
each corpus-bearing field it unions the three corpus layers into one target
term list:

1. `dynamic.enum` roots → descendant (or direct-child, per `dynamic.enum.property`) branch;
2. enum `allowedvalues` labels (bound to their positional `static.enum` id + synonyms where present);
3. standalone `static.enum` terms (the combined `dynamic_enum|static_enum` case).

Regex `allowedvalues` contribute nothing (they are validation-only). Ontology
routing matches `OntoMapEngine`: **NCIt** terms are expanded via the **NCI
EVSREST** API, **all other** ontologies via **EBI OLS4** (both keyless). It writes:

| Output | Consumer |
|--------|----------|
| `corpus/<field>[_<ontology>]_corpus.csv` (+ `.json`) | **OntoMapEngine** `corpus_df` (mixed-ontology fields split one CSV per ontology) |
| `<prefix>_ontology_terms.json` | R `validateDataAgainstSchema(ontology_terms=)` via `OmicsMLRepoCuration::loadOntologyTerms()` |

This closes the loop for both goals: the same materialized terms **populate the
target corpus** (matching) and **feed validation** of `dynamic_enum` /
`static_enum` fields, which previously required a caller-supplied term list.

```bash
# needs network: NCIt terms via NCI EVSREST, others via EBI OLS4
# (both public/unauthenticated — no API key)
python scripts/build_ontology_artifacts.py \
    --input schema/curatedmetagenomicdata/cmd_schema.csv --prefix cmd
python scripts/build_ontology_artifacts.py --input .../cmd_schema.csv \
    --prefix cmd --fields disease,treatment   # subset; --no-synonyms for fewer calls
```

Mixed-ontology fields (e.g. `disease` = NCIT + MONDO) cannot be mapped in one
`OntoMapEngine` run; use `combined_corpus_builder.run_ontology_partitioned()` to
run per ontology and merge the candidate lists. The core builders live in
`MetaHarmonizer` (`metaharmonizer.knowledge_db.combined_corpus_builder`).

# Validating data against a schema

Once a `*_schema.csv` exists, you can validate curated metadata against it.

`schema_validation.R` and `schema_validation.py` are equivalent CLIs (mirroring
the builders above): they read the schema table, check a data CSV, print a
report, and exit non-zero on failure. The R path delegates to
`OmicsMLRepoCuration::tableToYamlSchema()` + `validateDataAgainstSchema()`; the
Python path is a faithful port of the same validator.

**Input:** a `*_schema.csv` (`--schema`) and a data CSV of records (`--data`;
omit for a schema self-check).    
**Output:** a printed report only — no files are written; the result is 
communicated through the exit status.

```bash
# R
Rscript scripts/schema_validation.R \
    --schema schema/cbioportal/cbioportal_schema.csv \
    --data   my_records.csv

# Python
python scripts/schema_validation.py \
    --schema schema/cbioportal/cbioportal_schema.csv \
    --data   my_records.csv

# Also run LinkML's linkml-validate on each record (Python only):
python scripts/schema_validation.py --data my_records.csv \
    --linkml schema/cbioportal/cbioportal.linkml.yaml
```

Both check required fields, types, patterns, static/multi-valued enums, and
columns not covered by the schema, and produce matching results. Shared flags:
`--schema`, `--data` (omit for a schema self-check), `--strict` (treat warnings
as failures). Exit status: `0` valid, `1` invalid, `2` usage/IO error.

Two narrative walk-throughs of the same checks live in
[`../vignettes`](../vignettes):
[`schema_validation.Rmd`](../vignettes/schema_validation.Rmd) (R) and
[`schema_validation.ipynb`](../vignettes/schema_validation.ipynb) (Python).

## `lint_schema.py` — check schema tables against the conventions

```bash
python scripts/lint_schema.py                 # every schema/*/*_schema*.csv
python scripts/lint_schema.py --quiet         # per-file counts only
python scripts/lint_schema.py PATH --online   # also check roots in OLS4 (network)
python scripts/lint_schema.py --strict        # warnings fail too
```

It checks the file format, the header names and order, the closed vocabularies
(`col.class`, `unique`, `required`, `multiplevalues`, `corpus.type`, and
`dynamic.enum.property`), the `|` lists, CURIE syntax, how `static.enum` codes
pair with labels, and the `<;>` data delimiter.

- `static_enum` lists should have term IDs; a missing ID is a warning.
- `custom_enum` lists are non-ontology by design; a term ID there is an error.
- Retired `corpus.type` tokens (`binary`, `integer`, `numeric`, `character`) are
  reported together with their replacement.

When `metaharmonizer` is importable (for example with
`PYTHONPATH=~/OmicsMLRepo/MetaHarmonizer/src`), each table is also parsed with
`read_schema_table()`. Its warnings are reported, and so is any label list it
would drop.

Exit status: `0` clean, `1` errors, `2` usage or I/O error.
