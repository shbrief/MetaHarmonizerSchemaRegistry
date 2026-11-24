#!/usr/bin/env python3
# =============================================================================
# schema_validation.py
#
# Python counterpart of schema_validation.R. Validate a curated metadata table
# against a MetaHarmonizer schema table (e.g.
# schema/cbioportal/cbioportal_schema.csv). The schema CSV is the single source
# of truth produced in intro.qmd; this script turns it into validation rules and
# checks a data file against it, reporting:
#
#   * required fields that are missing                       (error)
#   * columns not covered by the schema                      (error)
#   * values not coercible to the declared col.class         (warning)
#   * values outside a static/custom enum's allowed set      (warning)
#   * values not matching the declared regex pattern         (warning)
#
# The validator is a faithful port of OmicsMLRepoCuration::validateDataAgainstSchema
# (R has no Python build of that package), so it returns the same
# {valid, errors, warnings} verdict as schema_validation.R. For production /
# cross-language validation, pass --linkml <prefix>.linkml.yaml to additionally
# run LinkML's `linkml-validate` on each record.
#
# Usage:
#   python scripts/schema_validation.py \
#       --schema schema/cbioportal/cbioportal_schema.csv \
#       --data   my_records.csv \
#       [--strict]                              # warnings fail too
#       [--linkml schema/cbioportal/cbioportal.linkml.yaml] \
#       [--target-class CbioportalRecord]
#
# Exit status: 0 if valid, 1 if errors (or, with --strict, any warnings), 2 on
# a usage/IO problem. --data may be omitted to run a self-check on the schema.
# =============================================================================
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

# Corpus types whose `allowedvalues` is an explicit value set (vs a regex);
# matches ENUM_CORPUS_TYPES in build_schema_artifacts.py.
ENUM_CORPUS_TYPES = {"static_enum", "custom_enum", "binary"}
NUMERIC_COL_CLASSES = {"integer", "numeric", "double"}
DEFAULT_WILDCARDS = ("Not applicable", "Not reported")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _isna(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip() in ("", "NA")


def read_schema(path: str) -> dict:
    """Turn a *_schema.csv (comma CSV; legacy write.table layout also read)
    into field -> validation rules.

    Mirrors OmicsMLRepoCuration::tableToYamlSchema: `allowedvalues` is split into
    an enum list when `corpus.type` is static/custom/binary, else kept as a regex.
    """
    with open(path, encoding="utf-8") as fh:
        header = fh.readline()
    sep = "," if re.match(r'^\s*"?[^",]*"?\s*,', header) else " "
    df = pd.read_csv(path, sep=sep, quotechar='"', skipinitialspace=True,
                     keep_default_na=True, na_values=["NA"])
    schema: dict = {}
    for _, row in df.iterrows():
        corpus = None if _isna(row.get("corpus.type")) else str(row["corpus.type"])
        allowed = None if _isna(row.get("allowedvalues")) else str(row["allowedvalues"])
        field = {
            "col_class": row["col.class"],
            "required": str(row.get("required", "")).strip().lower() == "required",
            "multiple_values": (not _isna(row.get("multiplevalues"))
                                and bool(row["multiplevalues"])),
            "delimiter": None if _isna(row.get("delimiter")) else str(row["delimiter"]),
            "allowed_values": None,
            "pattern": None,
        }
        if allowed is not None:
            # Enum when any `|`-token of corpus.type is an enum type (covers
            # compound `dynamic_enum|static_enum`); otherwise treat as regex.
            corpus_toks = set() if corpus is None else {
                t.strip() for t in corpus.split("|")}
            if corpus_toks & ENUM_CORPUS_TYPES:
                field["allowed_values"] = [v.strip() for v in allowed.split("|")]
            else:
                field["pattern"] = allowed
        schema[row["col.name"]] = field
    return schema


def _tokenize(value, field) -> list:
    """Split a cell into individual tokens for multi-valued fields."""
    if field["multiple_values"] and field["delimiter"]:
        return [t.strip() for t in str(value).split(field["delimiter"]) if t.strip()]
    return [value]


# --------------------------------------------------------------------------- #
# validation  (port of OmicsMLRepoCuration::validateDataAgainstSchema)
# --------------------------------------------------------------------------- #
def validate(data: pd.DataFrame, schema: dict,
             wildcards=DEFAULT_WILDCARDS) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    # required fields & uncovered columns (errors)
    missing = [c for c in schema if schema[c]["required"] and c not in data.columns]
    if missing:
        errors.append("Missing required fields: " + ", ".join(missing))
    extra = [c for c in data.columns if c not in schema]
    if extra:
        errors.append("Columns not covered by schema: " + ", ".join(extra))

    # per-field type / enum / pattern (warnings)
    for col in data.columns:
        field = schema.get(col)
        if field is None:
            continue
        values = [v for v in data[col].tolist()
                  if not _isna(v) and v not in wildcards]
        if not values:
            continue

        if field["col_class"] in NUMERIC_COL_CLASSES:
            tokens = [t for v in values for t in _tokenize(v, field)]
            bad = [t for t in tokens
                   if pd.to_numeric(pd.Series([t]), errors="coerce").isna().iloc[0]]
            if bad:
                warnings.append(
                    f"Field '{col}' expected type '{field['col_class']}' "
                    f"but found values not coercible: {', '.join(map(str, bad))}")

        if field["allowed_values"]:
            for i, v in enumerate(values, 1):
                invalid = [t for t in _tokenize(v, field)
                           if t not in field["allowed_values"] and t not in wildcards]
                if invalid:
                    warnings.append(
                        f"Field '{col}' row {i} has invalid values: "
                        f"{', '.join(invalid)}. "
                        f"Allowed values: {', '.join(field['allowed_values'])}")
        elif field["pattern"]:
            rx = re.compile("^(?:" + field["pattern"] + ")$")
            for i, v in enumerate(values, 1):
                bad = [t for t in _tokenize(v, field) if not rx.match(str(t))]
                if bad:
                    warnings.append(
                        f"Field '{col}' row {i} not matching pattern "
                        f"'{field['pattern']}': {', '.join(map(str, bad))}")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


# --------------------------------------------------------------------------- #
# optional LinkML production check
# --------------------------------------------------------------------------- #
def validate_linkml(data: pd.DataFrame, linkml_yaml: str,
                    target_class: str) -> int:
    """Run `linkml-validate` on each record; return the number of failing rows."""
    if shutil.which("linkml-validate") is None:
        print("  linkml-validate not found (pip install linkml linkml-runtime); "
              "skipping.", file=sys.stderr)
        return 0
    import yaml  # local import: only needed for this optional path

    failures = 0
    for i, record in enumerate(data.to_dict(orient="records")):
        record = {k: v for k, v in record.items() if not _isna(v)}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            yaml.safe_dump(record, fh)
            path = fh.name
        out = subprocess.run(
            ["linkml-validate", "-s", linkml_yaml, "-C", target_class, path],
            capture_output=True, text=True)
        if "No issues found" not in (out.stdout + out.stderr):
            failures += 1
            print(f"  [row {i}] {(out.stdout or out.stderr).strip()}", file=sys.stderr)
    return failures


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def print_report(res: dict, data: pd.DataFrame) -> None:
    print("=== Validation report ===", file=sys.stderr)
    print(f"Rows: {len(data)}   Columns: {data.shape[1]}", file=sys.stderr)
    print(f"Overall valid: {res['valid']}", file=sys.stderr)
    print(f"Errors: {len(res['errors'])}   Warnings: {len(res['warnings'])}",
          file=sys.stderr)
    for e in res["errors"]:
        print(f"  • {e}", file=sys.stderr)
    for w in res["warnings"]:
        print(f"  • {w}", file=sys.stderr)
    if res["valid"] and not res["warnings"]:
        print("✓ All checks passed.", file=sys.stderr)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--schema", default="schema/cbioportal/cbioportal_schema.csv")
    p.add_argument("--data", default=None,
                   help="CSV of records to validate. Omit for a schema self-check.")
    p.add_argument("--wildcards", default=",".join(DEFAULT_WILDCARDS),
                   help="Comma-separated always-allowed values.")
    p.add_argument("--strict", action="store_true",
                   help="Treat warnings as failures too.")
    p.add_argument("--linkml", default=None,
                   help="Also run linkml-validate against this <prefix>.linkml.yaml.")
    p.add_argument("--target-class", default=None,
                   help="LinkML record class (default: <Prefix>Record).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if not Path(args.schema).exists():
        print(f"Schema file not found: {args.schema}", file=sys.stderr)
        return 2
    print(f"Reading schema: {args.schema}", file=sys.stderr)
    schema = read_schema(args.schema)
    required = [c for c in schema if schema[c]["required"]]
    print(f"Schema fields: {len(schema)}   Required: {', '.join(required)}",
          file=sys.stderr)

    if args.data is None:
        print("\nNo --data supplied; running a self-check on required fields.\n",
              file=sys.stderr)
        data = pd.DataFrame([{c: "x" for c in required}])
    else:
        if not Path(args.data).exists():
            print(f"Data file not found: {args.data}", file=sys.stderr)
            return 2
        print(f"Reading data:   {args.data}", file=sys.stderr)
        data = pd.read_csv(args.data, keep_default_na=True, na_values=["", "NA"])

    wildcards = tuple(w.strip() for w in args.wildcards.split(","))
    res = validate(data, schema, wildcards=wildcards)
    print_report(res, data)

    linkml_failures = 0
    if args.linkml:
        prefix = re.sub(r"(_schema)?\.csv$", "", Path(args.schema).name)
        target_class = args.target_class or f"{prefix[:1].upper()}{prefix[1:]}Record"
        print(f"\nLinkML validation ({args.linkml}, class {target_class}):",
              file=sys.stderr)
        linkml_failures = validate_linkml(data, args.linkml, target_class)
        if linkml_failures == 0:
            print("  ✓ All records pass linkml-validate.", file=sys.stderr)

    failed = (not res["valid"]
              or (args.strict and res["warnings"])
              or linkml_failures > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
