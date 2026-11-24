#!/usr/bin/env python3
# =============================================================================
# build_schema_artifacts.py
#
# Python counterpart of build_schema_artifacts.R. Turns a MetaHarmonizer schema
# table (e.g. schema/cbioportal/cbioportal_schema.csv) into the downstream
# artifacts consumed by the rest of the ecosystem:
#
#   1. <prefix>.linkml.yaml                       -> LinkML schema
#                                                    (validation + metacurator)
#   2. <prefix>_target_attrs.csv                  -> field_name,is_numeric_field
#                                                    (SchemaMapper target schema)
#   3. <prefix>_target_attrs_allowed_values.json  -> {field: [allowed values]}
#                                                    (SchemaMapper value dict)
#   4. <prefix>_target_attrs_alias_haiku.csv      -> field_name,source,is_numeric_field
#      (+ .meta.json provenance)                     (SchemaMapper alias dict;
#                                                     LLM-generated, needs API key)
#
# The LinkML conversion is a direct port of OmicsMLRepoCuration::tableToLinkmlSchema
# (R has no Python build of that package); the alias step delegates to
# metaharmonizer.models.schema_mapper.generate_alias_dict.
#
# Usage:
#   python scripts/build_schema_artifacts.py \
#       --input schema/cbioportal/cbioportal_schema.csv \
#       --outdir schema/cbioportal \
#       --prefix cbioportal \
#       [--model claude-haiku-4-5-20251001] \
#       [--no-alias]        # skip the LLM step (no API key / offline)
#
# The alias step reads ANTHROPIC_API_KEY from the environment. Without it (or
# with --no-alias) the first three artifacts are still produced.
# =============================================================================
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

# Corpus types whose `allowedvalues` is an explicit value set (vs a regex).
ENUM_CORPUS_TYPES = {"static_enum", "custom_enum", "binary"}
NUMERIC_COL_CLASSES = {"integer", "double", "numeric"}

# R type class -> LinkML range.
TYPE_MAPPING = {
    "character": "string",
    "integer": "integer",
    "numeric": "float",
    "double": "float",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _isna(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or v == "" or v == "NA"


def _truthy(v) -> bool:
    return str(v).strip().upper() in {"TRUE", "T", "1"}


def _is_enum_corpus_type(ct) -> bool:
    """True if any ``|``-token of corpus.type is an enum type.

    Handles compound types like ``dynamic_enum|static_enum`` so their explicit
    ``allowedvalues`` (the static half) are still emitted as an enum value set.
    """
    if _isna(ct):
        return False
    return any(tok.strip() in ENUM_CORPUS_TYPES for tok in str(ct).split("|"))


def read_schema(path: str) -> pd.DataFrame:
    """Read a registry schema table.

    Schema tables are comma-separated CSV (R ``write.csv()``). Files in the
    legacy ``write.table()`` layout (space-separated, quoted) are still read,
    so an un-migrated copy keeps working.
    """
    with open(path, encoding="utf-8") as fh:
        header = fh.readline()
    sep = "," if re.match(r'^\s*"?[^",]*"?\s*,', header) else " "
    return pd.read_csv(
        path,
        sep=sep,
        quotechar='"',
        skipinitialspace=True,
        keep_default_na=True,
        na_values=["NA"],
    )


# --------------------------------------------------------------------------- #
# 1. LinkML schema  (port of OmicsMLRepoCuration::tableToLinkmlSchema)
# --------------------------------------------------------------------------- #
def table_to_linkml_schema(
    df: pd.DataFrame,
    schema_id: str | None = None,
    schema_name: str | None = None,
    schema_version: str = "1.0.0",
    schema_description: str | None = None,
    class_name: str = "MetadataRecord",
    output_file: str | None = None,
    last_updated_on: str | None = None,
) -> dict:
    schema: dict = {}
    if schema_id is not None:
        schema["id"] = schema_id
    if schema_name is not None:
        schema["name"] = schema_name
    schema["version"] = schema_version
    schema["last_updated_on"] = last_updated_on or datetime.now().strftime("%Y-%m-%d")
    if schema_description is not None:
        schema["description"] = schema_description
    schema["prefixes"] = {
        "linkml": "https://w3id.org/linkml/",
        "NCIT": "http://purl.obolibrary.org/obo/NCIT_",
        "EFO": "http://www.ebi.ac.uk/efo/EFO_",
        "HANCESTRO": "http://purl.obolibrary.org/obo/HANCESTRO_",
        "UBERON": "http://purl.obolibrary.org/obo/UBERON_",
        "MRO": "http://purl.obolibrary.org/obo/MRO_",
        "SNOMED": "http://purl.bioontology.org/ontology/SNOMEDCT/",
    }
    if schema_name is not None:
        schema["default_prefix"] = schema_name
    schema["imports"] = ["linkml:types"]

    slots: dict = {}
    enums: dict = {}
    slot_names: list[str] = []

    for _, row in df.iterrows():
        field_name = row["col.name"]
        slot_names.append(field_name)

        linkml_type = TYPE_MAPPING.get(row.get("col.class"), "string")
        slot_def: dict = {"description": row.get("description"), "range": linkml_type}

        required = str(row.get("required", "")).strip().lower() == "required"
        if required:
            slot_def["required"] = True

        if not _isna(row.get("multiplevalues")) and _truthy(row.get("multiplevalues")):
            slot_def["multivalued"] = True

        if str(row.get("unique", "non-unique")).strip() == "unique":
            slot_def["identifier"] = True

        allowed = row.get("allowedvalues")
        if not _isna(allowed):
            allowed = str(allowed)
            # Same discrimination the R function uses: regex metacharacters ->
            # pattern; a bare pipe-list -> enum.
            if re.search(r"^\^|\$$|\[|\]|\{|\}|\+", allowed):
                slot_def["pattern"] = allowed
            elif "|" in allowed:
                enum_name = f"{field_name}_enum"
                slot_def["range"] = enum_name
                vals = [v.strip() for v in allowed.split("|")]

                # Positional ontology ids from static.enum give each value its
                # `meaning`; a blank id leaves that value's meaning null.
                meanings: list[str | None] = [None] * len(vals)
                se = row.get("static.enum")
                if not _isna(se):
                    ids = [x.strip() for x in str(se).split("|")]
                    for k in range(min(len(ids), len(vals))):
                        if ids[k] not in ("", "NA"):
                            meanings[k] = ids[k]

                permissible = {}
                for k, v in enumerate(vals):
                    permissible[v] = {"meaning": meanings[k]} if meanings[k] else None
                enums[enum_name] = {"permissible_values": permissible}

        # Dynamic (ontology-branch) enums -> LinkML reachable_from. cBioPortal
        # has none, but kept for parity with the R implementation.
        de = row.get("dynamic.enum")
        if not _isna(de):
            roots = [r.strip() for r in str(de).split("|") if r.strip() not in ("", "NA")]
            prop = row.get("dynamic.enum.property")
            prop = str(prop) if not _isna(prop) else "descendant"
            is_direct = prop == "children"
            includes = [
                {
                    "reachable_from": {
                        "source_ontology": f"obo:{root.split(':')[0].lower()}",
                        "source_nodes": [root],
                        "relationship_types": ["rdfs:subClassOf"],
                        "is_direct": is_direct,
                    }
                }
                for root in roots
            ]
            enum_name = f"{field_name}_enum"
            enum_def: dict = {"include": includes}
            if enum_name in enums and "permissible_values" in enums[enum_name]:
                enum_def["permissible_values"] = enums[enum_name]["permissible_values"]
            enums[enum_name] = enum_def
            slot_def["range"] = enum_name
            slot_def["comments"] = [
                "Values should be "
                + ("direct children" if is_direct else "descendants")
                + " of: "
                + ", ".join(roots)
            ]

        # Curation-behaviour annotations carried through from the schema table.
        # `scope` says whether a value describes the study or an individual sample;
        # `unit.of` marks a slot that carries another slot's unit, `value.of` one
        # that carries another slot's values (parallel lists, paired by
        # position), and `separator` the key:value token inside each item. Downstream
        # consumers (MetaExtractor's make_clean_schema.py) read these instead of
        # keeping their own hardcoded copies.
        annotations: dict = {}
        scope = row.get("scope")
        if not _isna(scope) and str(scope).strip():
            annotations["scope"] = str(scope).strip()
        if _truthy(row.get("propagate.study.value")):
            annotations["propagate_study_value"] = True
        unit_of = row.get("unit.of")
        if not _isna(unit_of) and str(unit_of).strip():
            token = str(unit_of).strip()
            if token.upper() in ("TRUE", "FALSE"):
                # Boolean form: the column only marks *that* a slot is a unit, not
                # which slot it is the unit of, so it cannot name a target field.
                if token.upper() == "TRUE":
                    annotations["is_unit"] = True
            else:
                annotations["unit_of"] = token
        value_of = row.get("value.of")
        if not _isna(value_of) and str(value_of).strip():
            annotations["value_of"] = str(value_of).strip()
        separator = row.get("separator")
        if not _isna(separator) and str(separator).strip():
            annotations["separator"] = str(separator).strip()
        if annotations:
            slot_def["annotations"] = annotations

        slots[field_name] = slot_def

    schema["classes"] = {
        class_name: {
            "description": f"Main data class for "
            f"{class_name if schema_name is None else schema_name}",
            "slots": slot_names,
        }
    }
    schema["slots"] = slots
    if enums:
        schema["enums"] = enums

    if output_file:
        with open(output_file, "w") as fh:
            yaml.safe_dump(schema, fh, sort_keys=False, default_flow_style=False,
                           allow_unicode=True)
    return schema


def read_meta(schema_dir: Path) -> dict:
    """The schema's ``meta.yaml`` (version, date, ...), or ``{}`` if there is none.

    Its ``version`` and ``date`` become the LinkML ``version`` and
    ``last_updated_on``, so a rebuild of an unchanged schema is byte-identical.
    """
    path = schema_dir / "meta.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def write_linkml(df: pd.DataFrame, outdir: Path, prefix: str,
                 meta: dict | None = None) -> Path:
    meta = meta or {}
    out = outdir / f"{prefix}.linkml.yaml"
    class_name = f"{prefix[:1].upper()}{prefix[1:]}Record"
    table_to_linkml_schema(
        df,
        schema_id=f"https://w3id.org/omicsmlrepo/{prefix}",
        schema_name=prefix,
        schema_version=str(meta.get("version", "1.0.0")),
        last_updated_on=str(meta["date"]) if meta.get("date") else None,
        schema_description=f"{prefix} schema for the MetaHarmonizer ecosystem",
        class_name=class_name,
        output_file=str(out),
    )
    print(f"[1/4] LinkML schema         -> {out}", file=sys.stderr)
    return out


# --------------------------------------------------------------------------- #
# 2. target attrs (field_name, is_numeric_field)
# --------------------------------------------------------------------------- #
def write_target_attrs(df: pd.DataFrame, outdir: Path, prefix: str) -> pd.DataFrame:
    out = outdir / f"{prefix}_target_attrs.csv"
    ta = pd.DataFrame(
        {
            "field_name": df["col.name"],
            "is_numeric_field": [
                "yes" if c in NUMERIC_COL_CLASSES else "no" for c in df["col.class"]
            ],
        }
    )
    ta.to_csv(out, index=False)
    print(f"[2/4] Target attrs          -> {out} ({len(ta)} fields)", file=sys.stderr)
    return ta


# --------------------------------------------------------------------------- #
# 3. allowed-values JSON (SchemaMapper value dict)
# --------------------------------------------------------------------------- #
def write_allowed_values(df: pd.DataFrame, outdir: Path, prefix: str) -> Path:
    out = outdir / f"{prefix}_target_attrs_allowed_values.json"
    values: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        ct = row.get("corpus.type")
        av = row.get("allowedvalues")
        if _is_enum_corpus_type(ct) and not _isna(av):
            values[row["col.name"]] = [v.strip() for v in str(av).split("|")]
    with open(out, "w") as fh:
        json.dump(values, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(
        f"[3/4] Allowed values JSON   -> {out} ({len(values)} enum fields)",
        file=sys.stderr,
    )
    return out


# --------------------------------------------------------------------------- #
# 4. LLM alias dictionary (needs API key)
# --------------------------------------------------------------------------- #
def write_alias_dict(
    target_df: pd.DataFrame, outdir: Path, prefix: str, model: str, schema_domain: str
) -> Path | None:
    out = outdir / f"{prefix}_target_attrs_alias_haiku.csv"
    meta_out = outdir / f"{prefix}_target_attrs_alias_haiku.meta.json"

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("[4/4] Alias dict            -> SKIPPED (ANTHROPIC_API_KEY not set).",
              file=sys.stderr)
        return None
    try:
        from metaharmonizer.models.schema_mapper.generate_alias_dict import (
            generate_alias_dict,
        )
    except ImportError as e:
        print(f"[4/4] Alias dict            -> SKIPPED (metaharmonizer not importable: {e}).",
              file=sys.stderr)
        return None

    started = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    aliases = generate_alias_dict(
        target_fields=target_df,          # carries field_name + is_numeric_field
        model=model,
        schema_domain=schema_domain,
    )
    finished = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    if aliases.empty:
        print("[4/4] Alias dict            -> ERROR (no alias rows produced).",
              file=sys.stderr)
        return None

    aliases.to_csv(out, index=False)
    meta = {
        "script": "build_schema_artifacts.py",
        "model": model,
        "prompt_version": schema_domain,
        "source_schema": f"{prefix}_target_attrs.csv",
        "n_target_fields": int(len(target_df)),
        "n_alias_rows": int(len(aliases)),
        "n_unique_sources": int(aliases["source"].nunique()),
        "started_at": started,
        "finished_at": finished,
    }
    with open(meta_out, "w") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    print(f"[4/4] Alias dict            -> {out} ({len(aliases)} rows)", file=sys.stderr)
    print(f"      Alias provenance      -> {meta_out}", file=sys.stderr)
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default="schema/cbioportal/cbioportal_schema.csv")
    p.add_argument("--outdir", default=None, help="Defaults to dirname(--input).")
    p.add_argument("--prefix", default=None,
                   help="Defaults to basename(--input) minus _schema.csv.")
    p.add_argument("--model", default="claude-haiku-4-5-20251001")
    p.add_argument("--schema-domain", default="cancer_genomics")
    p.add_argument("--no-alias", action="store_true",
                   help="Skip the LLM alias step (offline / no API key).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    outdir = Path(args.outdir) if args.outdir else Path(args.input).parent
    prefix = args.prefix or re.sub(r"(_schema)?\.csv$", "", Path(args.input).name)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Reading schema: {args.input}", file=sys.stderr)
    df = read_schema(args.input)
    meta = read_meta(Path(args.input).parent)
    if meta:
        print(f"Schema version: {meta.get('version')} ({meta.get('date')})", file=sys.stderr)

    write_linkml(df, outdir, prefix, meta)
    ta = write_target_attrs(df, outdir, prefix)
    write_allowed_values(df, outdir, prefix)
    if args.no_alias:
        print("[4/4] Alias dict            -> SKIPPED (--no-alias).", file=sys.stderr)
    else:
        write_alias_dict(ta, outdir, prefix, args.model, args.schema_domain)
    print("Done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
