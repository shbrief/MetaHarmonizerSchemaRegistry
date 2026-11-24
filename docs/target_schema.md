# Preparing a target schema for MetaHarmonizer

A **target schema** is the schema table that tells
[MetaHarmonizer](https://github.com/shbrief/MetaHarmonizer) what the harmonized
metadata should look like: which fields exist, and for each field, which values
are allowed. In this registry each schema is one `<prefix>_schema.csv` under
`schema/<source>/`. The same table feeds the LinkML, validation and SchemaMapper
artifacts (see the [README](../README.md)), so one table serves all of them.

This guide covers two things:

- **The registry convention**, which `scripts/lint_schema.py` enforces.
- **What MetaHarmonizer's `harmonize()` reads** from the table, as implemented
  in `src/metaharmonizer/schema_vocab.py` and `src/metaharmonizer/pipeline.py`.
  If the code and this guide disagree, the code wins. Please update this guide
  when that happens.

For the current state of each table, run the lint (see [§8](#8-check-the-table))
rather than relying on a list here. How schemas are versioned and released is
in [§9](#9-versioning).

---

## 1. What MetaHarmonizer does with the schema

`harmonize(input_path, schema_table=...)` runs two stages, and the schema table
drives both of them:

1. **Schema mapping (SchemaMapper).** Each source column is matched to one
   target field. The list of target fields is the `col.name` column. Matching
   uses the **field names**, plus the optional alias and value dictionaries
   described in [§7](#7-helping-schema-mapping-alias-and-value-dictionaries).
2. **Value mapping (OntologyMapper).** The values in each mapped column are
   matched to terms. Each field's candidate terms, called its *corpus*, are
   built from that field's row:
   - every term under the field's **ontology roots** (`dynamic.enum`), fetched
     from OLS4; and
   - its **allowed-value labels** (`allowedvalues`), with optional term IDs
     (`static.enum`).

A field with neither roots nor a label list is a **passthrough** field. Its
source values are copied into the output without ontology mapping.

---

## 2. File format

- **UTF-8, comma-separated CSV with a header row**, written by R's
  `write.csv(df, path, row.names = FALSE)`. MetaHarmonizer reads it with
  `pandas.read_csv`, so a TSV or the old space-separated `write.table()` layout
  will not parse there (the registry scripts still read the old layout).
- **One row per target field.**
- **`NA` for an empty cell.** MetaHarmonizer also treats `""`, `NaN`, `None`
  and `null` as empty.
- **Headers are dotted lower case** and come in this order:

  ```
  col.name, col.class, unique, required, scope, unit.of, multiplevalues,
  delimiter, separator, description, corpus.type, allowedvalues, static.enum,
  dynamic.enum, dynamic.enum.property
  ```

  `scope`, `unit.of` and `propagate.study.value` (between `unit.of` and
  `multiplevalues`) are optional. `Rscript scripts/format_schema.R` puts the
  columns in this order for you. Header names must match exactly:
  `dynamic_enum` or `Separater` are not recognized, and an unrecognized column
  is ignored.
- **`|` separates list items** in `corpus.type`, `allowedvalues`, `static.enum`
  and `dynamic.enum`. NCIt labels contain `;` (for example `t(9;22)`), and no
  corpus label contains `|`, so `|` is the safe choice. The `delimiter` column
  describes the *data* cells (`;` or `<;>`); that is unrelated.

Example, using rows from `cmd_schema.csv` and `omicsmlprepo_cbio_schema.csv`
(other columns omitted):

```csv
col.name,col.class,multiplevalues,delimiter,corpus.type,allowedvalues,static.enum,dynamic.enum,dynamic.enum.property
sex,character,FALSE,NA,static_enum,Female|Male,NCIT:C16576|NCIT:C20197,NA,NA
body_site,character,TRUE,<;>,dynamic_enum,NA,NA,NCIT:C12219,descendant
ancestry,character,FALSE,NA,dynamic_enum,NA,NA,HANCESTRO:0004,children
sequencing_platform,character,FALSE,NA,custom_enum,Illumina HiSeq|Illumina MiSeq|BGISEQ,NA,NA,NA
age_years,numeric,FALSE,NA,regexp,[0-9]+\.?[0-9]*,NA,NA,NA
patientId,character,FALSE,NA,any,NA,NA,NA,NA
```

Every row follows the convention. MetaHarmonizer currently drops the
`sequencing_platform` list, though (see the limitations in [§5](#5-closed-value-lists-allowedvalues-staticenum)).

---

## 3. Column reference

**Read by MetaHarmonizer** means `harmonize()` uses the column. The other
columns are still required by the convention: schema validation, the LinkML
export and curators rely on them.

| Column | Read by MetaHarmonizer? | Convention |
|---|---|---|
| `col.name` | **Yes (required)** | Non-empty and unique. No naming style is enforced, but it is the SchemaMapper target, so make it descriptive (see [§7](#7-helping-schema-mapping-alias-and-value-dictionaries)). |
| `col.class` | Yes | `character`, `integer`, `numeric`, `logical` or `date`. `double` is retired; use `numeric`. `integer` and `numeric` mark a numeric field for SchemaMapper. |
| `unique` | No | `unique` or `non-unique` |
| `required` | No | `required` or `optional` |
| `scope` | No | Optional, for example `sample` or `study` |
| `unit.of` | No | Optional; the field whose unit this field records |
| `propagate.study.value` | No | Optional; `TRUE` when a study-level value applies to every sample, else `FALSE` |
| `multiplevalues` | Yes | `TRUE` or `FALSE`, never `NA` (see [§6](#6-multi-valued-fields)) |
| `delimiter` | No | The separator the curated data uses in a multi-valued cell (`;` or `<;>`), `NA` otherwise |
| `separator` | No | A multi-character token for key:value pairs, else `NA` |
| `description` | No | Definition of the field, ideally the ontology definition |
| `corpus.type` | Only the exact value `static_enum` (see [§5](#5-closed-value-lists-allowedvalues-staticenum)) | **Required.** Decides how `allowedvalues` is read. See below. |
| `allowedvalues` | Yes | Depends on `corpus.type` |
| `static.enum` | Yes, when `corpus.type` is exactly `static_enum` | One CURIE per label, same order |
| `dynamic.enum` | **Yes** | `\|` list of root CURIEs (see [§4](#4-ontology-backed-fields-dynamicenum)) |
| `dynamic.enum.property` | Yes | `descendant` (default) or `children`; applies to all roots |

### `corpus.type`

| Token | Meaning | `allowedvalues` | `static.enum` | `dynamic.enum` |
|---|---|---|---|---|
| `dynamic_enum` | Values come from ontology roots | `NA`, unless combined with a list type | `NA` | Required |
| `static_enum` | Closed list of ontology terms | `\|` list of labels | `\|` list of CURIEs; a missing ID is a gap to fill | — |
| `custom_enum` | Closed list of non-ontology terms by design (kits, platforms) | `\|` list of labels | `NA` | — |
| `regexp` | Free value matching a pattern | One regex | `NA` | `NA` |
| `any` | Free value | `NA` or `.+` | `NA` | `NA` |

- Combine tokens with `|`, for example `dynamic_enum|static_enum` for a field
  with roots plus a few extra labels.
- `regexp` cannot be combined with the enum types, and `static_enum` cannot be
  combined with `custom_enum`.
- **Retired tokens:** `binary` becomes `static_enum` and keeps its labels:
  `Yes|No` → `NCIT:C49488|NCIT:C49487`, `TRUE|FALSE` →
  `NCIT:C68850|NCIT:C68851`. `integer`, `numeric` and `character`
  become `regexp` when there is a pattern, otherwise `any`. The validators treat
  `static_enum`, `custom_enum` and `binary` alike, and take the type check from
  `col.class`, so validation does not change.

MetaHarmonizer also accepts CamelCase headers (`ColName`, `DynamicEnum`, …) and
its own native names (`field_name`, `ontology_root`, …). The registry uses the
dotted names only.

---

## 4. Ontology-backed fields (`dynamic.enum`)

Use this for any field whose values should come from an ontology: diseases,
body sites, treatments, countries, ancestry, and so on.

**How the corpus is built**

- For each root, MetaHarmonizer fetches the root's **descendants** (or only its
  direct **children**) from **OLS4**. This applies to NCIt too.
- **The root term itself is excluded** from the corpus. If the root is a valid
  value, such as `Neoplasm`, list it in `allowedvalues` as well.
- Only terms with **the root's own prefix** are kept. For example, terms that
  HANCESTRO imports from AfPO are dropped from a `HANCESTRO:` root.
- Roots that share an ontology are merged into one corpus. A field whose roots
  span several ontologies, such as `NCIT:C7057|MONDO:0000001`, gets one corpus
  per ontology, and MetaHarmonizer keeps the best-scoring match across them.

**Rules**

1. **Write roots as upper-case CURIEs**, for example `NCIT:C3262`,
   `MONDO:0000001`, `HANCESTRO:0004`. A root without `:` is skipped with a
   warning, and a lower-case prefix such as `ncit:C3262` builds a wrong IRI.
   (`Orphanet` keeps its mixed case.)
2. **Separate several roots with `|`**, for example `NCIT:C1909|NCIT:C25218`.
3. **Do not use obsolete terms.** An obsolete root is skipped with only a
   warning, so the field silently loses that part of its corpus. Example:
   `EFO:0000408` (disease) has been obsolete since EFO 3.88.0; use
   `MONDO:0000001`. `lint_schema.py --online` checks this.
4. **Set `dynamic.enum.property`:**
   - `descendant`: every term below the root, at any depth. This is the default.
   - `children`: only direct children, for fields that hold top-level
     categories. For example, `ancestry` uses the children of `HANCESTRO:0004`,
     while `ancestry_details` uses the descendants.
5. **Leave `dynamic.enum` as `NA` for numeric, ID, date and free-text fields.**
   A root on a numeric field only builds a pointless corpus.
6. **Set `corpus.type` to `dynamic_enum`** (or `dynamic_enum|static_enum` when
   the field also has labels).

**Choosing good roots**

- **Pick the narrowest root, or set of roots, that still covers the values you
  expect.** A broad root such as `NCIT:C7057` (Disease, Disorder or Finding)
  makes a very large corpus, which is slower and gives more chances of a wrong
  match.
- **Start from the terms already curated.**
  `OmicsMLRepoCuration::mapNodes(ids, cutoff)` finds the ontology nodes that
  cover the most of a set of curated term IDs, and
  `addDynamicEnumNodes(curated_col, dd)` writes them into a table.
- **Check coverage before committing a root.** Confirm in OLS that a sample of
  curated terms sits under one of the roots. Values outside every root can only
  match the allowed-value labels.
- **Ontologies MetaHarmonizer supports fully:** NCIT, EFO, UBERON, HP,
  Orphanet, MONDO, DOID, CL, CHEBI, PATO, GO, BFO, OBI and SNOMED. Other
  prefixes, such as HANCESTRO and MRO, still build a corpus from OLS, but run as
  a "local" corpus without the synonym and concept-table lookups, so expect
  somewhat lower recall.
- **Schema mapping uses only NCIT roots.** SchemaMapper's ontology-based field
  matcher ignores other roots. Give at least one NCIT root to fields that you
  want SchemaMapper to find from their values.
- **An NCIT corpus needs a UMLS key** (`UMLS_API_KEY`) to build the NCI EVS
  concept tables.

---

## 5. Closed value lists (`allowedvalues`, `static.enum`)

Use a list for fields with a small fixed set of values, such as `sex`,
`vital_status` or `smoker`. A list can also add values that are not under any
root, such as `Healthy` for `disease`.

```csv
col.name,corpus.type,allowedvalues,static.enum
smoker,static_enum,Smoker (finding)|Non-smoker (finding)|Ex-smoker (finding)|Never smoked tobacco (finding),SNOMED:77176002|SNOMED:8392000|SNOMED:8517006|SNOMED:266919005
dna_extraction_kit,custom_enum,PSP Spin Stool DNA Plus Kit|PowerSoil DNA Isolation Kit|QIAamp,NA
```

**Rules**

1. **`static_enum` or `custom_enum`?** Use `static_enum` when the labels are
   ontology terms, and fill in their IDs. Use `custom_enum` only when the terms
   have no ontology IDs by design. A `static_enum` row with missing IDs is a gap
   to fill, not a `custom_enum`.
2. **Keep `static.enum` the same length and order as the labels.** A label
   without an ID gets an empty slot (`NCIT:C1||NCIT:C3`), or the whole cell is
   `NA`.
3. **Write each label exactly as it should appear in the harmonized output**,
   including case.
4. **Labels and roots can be combined.** A label that already exists in a root
   corpus (compared case-insensitively) is not added again; the rest go into a
   small local corpus for that field.
5. **Patterns belong in `regexp` rows.** Patterns such as `[0-9]+` or
   `T[X1-4]N[X0-3]M[X0-1]` are for validation; MetaHarmonizer does not match
   values against them.

**Current MetaHarmonizer limitations.** The convention is ahead of the parser in
`schema_vocab.py`. Until it is fixed, these rows lose information in
`harmonize()` (the lint reports them as "MetaHarmonizer drops this list"):

- **`custom_enum` lists are dropped.** Only an exact `static_enum` row is read
  as a list. Any other row is checked for regex syntax, and a `|` makes the
  whole cell look like a pattern.
- **Compound rows lose their IDs.** `dynamic_enum|static_enum` is not exactly
  `static_enum`, so `static.enum` is ignored, and a list of two or more labels
  is dropped as above. A single label (such as `Healthy`) survives without its
  ID.
- **Empty ID slots break the pairing.** Empty slots are discarded before labels
  and IDs are paired, so the counts differ and *all* IDs for the field are
  dropped with a warning.
- **Labels are also split on `;`**, which breaks labels such as `t(9;22)`.

---

## 6. Multi-valued fields

- **Set `multiplevalues = TRUE`** when one sample can carry several values
  (several diseases, treatments or body sites), and set `delimiter` to the
  separator the curated data uses (`;` or `<;>`).
  - When several source columns map to such a field, MetaHarmonizer keeps the
    union of their terms.
  - For a single-valued field, MetaHarmonizer keeps only the best-scoring
    source column.
- **Output values and term IDs are always joined with `;`** by MetaHarmonizer.
- **Input cells are not split.** `delimiter` is not used to split input cells;
  each distinct cell is matched as one string. Keep `delimiter` accurate anyway
  for validation and for downstream users.

---

## 7. Helping schema mapping: alias and value dictionaries

SchemaMapper matches source columns to target fields by **name**. It uses exact
and fuzzy matching, then embeddings, then an LLM step. `description` is not
used. To improve matching:

- **Use clear, conventional field names.** Prefer `age_at_diagnosis` to
  `age_dx`, and stay consistent within a table.
- **Alias dictionary**: a CSV with exactly `field_name,source,is_numeric_field`,
  one row per known source column name. The registry builds one per schema as
  `<prefix>_target_attrs_alias_haiku.csv`. It is ignored if none of its
  `field_name` values appears in the schema.
- **Value dictionary**: a JSON object mapping each field to example values. The
  registry builds it as `<prefix>_target_attrs_allowed_values.json`.

`harmonize()` picks these up automatically only when they are named
`<schema stem>.alias.csv` and `<schema stem>.values.json` and sit next to the
schema table. The registry names differ, so pass them explicitly:

```python
from metaharmonizer import harmonize

d = "schema/curatedmetagenomicdata/"
res = harmonize(
    "small_sample.csv",
    schema_table=d + "cmd_schema.csv",
    schema_kwargs={
        "alias_dict_path": d + "cmd_target_attrs_alias_haiku.csv",
        "value_dict_path": d + "cmd_target_attrs_allowed_values.json",
    },
)
```

---

## 8. Check the table

**Lint it.** This checks the whole convention above, and also parses the table
with MetaHarmonizer's `read_schema_table()` when MetaHarmonizer is installed:

```bash
python scripts/lint_schema.py schema/curatedmetagenomicdata/cmd_schema.csv
python scripts/lint_schema.py --online schema/omicsmlrepo_cbio/omicsmlprepo_cbio_schema.csv  # + OLS obsolete-root check
python scripts/lint_schema.py   # every schema table in the registry
```

Exit status is `0` clean, `1` errors (or warnings with `--strict`), `2` usage
or IO error. `--quiet` prints only the per-file totals.

**See what MetaHarmonizer extracts.** No network access is needed:

```python
from metaharmonizer.schema_vocab import read_schema_table

vocab = read_schema_table("schema/curatedmetagenomicdata/cmd_schema.csv")
for name, v in vocab.items():
    print(name, v.roots, v.root_property, v.allowed_values, v.allowed_codes,
          v.multiple_values)
```

Look for ontology-backed fields with `roots=()`, label lists that came out
empty, and `allowed_codes` that are all `''`. Watch the log for
`unknown root property` and `static.enum codes ... codes ignored`.

**Then run on a small input** (see the example in [§7](#7-helping-schema-mapping-alias-and-value-dictionaries))
and check the log for obsolete or non-CURIE roots, and for fields whose
vocabulary resolved to no terms (these become passthrough fields).

- **The full run needs network access.** Corpora are built from OLS before
  schema mapping starts, so an OLS outage aborts the whole run.
- **Root corpora are cached and never refreshed.** Each root's corpus is cached
  as `<CORPUS_DIR>/schema_vocab/<root>__<relation>.csv`. Delete that file after
  changing a root's ontology version, or to fetch fresh terms.

### Checklist

- [ ] UTF-8 comma CSV from `write.csv(row.names = FALSE)`, `NA` for empty cells
- [ ] Dotted lower-case headers in the canonical order
- [ ] Every `col.name` is non-empty and unique
- [ ] `col.class` is `character`, `integer`, `numeric`, `logical` or `date`
- [ ] `multiplevalues` is `TRUE`/`FALSE`; multi-valued fields have the data's `delimiter` (`;` or `<;>`)
- [ ] Every row has a `corpus.type`, with `|`-joined current tokens only
- [ ] Ontology fields have upper-case, non-obsolete `|`-separated roots and a
      `dynamic.enum.property`
- [ ] Numeric, ID, date and free-text fields have **no** roots
- [ ] `static_enum` lists have `static.enum` IDs in the same length and order;
      `custom_enum` lists have `static.enum = NA`
- [ ] Patterns are in `regexp` rows
- [ ] `description` is filled in
- [ ] `scripts/lint_schema.py` passes, and `read_schema_table()` output looks right
- [ ] `format_schema.R` run, artifacts rebuilt, `meta.yaml` version bumped, CHANGELOG entry added

---

## 9. Versioning

Each schema directory has a `meta.yaml` (schema id, `version`, `date`, tables,
lint mode, ontology versions) and a `CHANGELOG.md`. In the same pull request as
any change to a table:

1. Run `Rscript scripts/format_schema.R` and rebuild the artifacts.
2. Bump `version` and set `date` in `meta.yaml`:

   | Bump | When |
   |---|---|
   | Major | A field removed or renamed, an allowed value removed, `col.class` changed, a field made required, roots narrowed |
   | Minor | A field, allowed value or root added; a missing term ID filled in |
   | Patch | Descriptions and typos that change no value |

3. Add a `## [x.y.z] - YYYY-MM-DD` entry to `CHANGELOG.md`.

CI (`scripts/check_schemas.py`) fails if a schema changed without a version
bump or CHANGELOG entry. After the checks pass on `main`, CI tags the commit
`<schema>-v<version>` and publishes a GitHub release. Consumers should read a
schema from a tag, not from `main`.

Versions below 1.0.0 may still change in breaking ways. A schema reaches 1.0.0
once its table passes the lint; its `meta.yaml` then switches to
`lint: blocking`.

