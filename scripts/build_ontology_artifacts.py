#!/usr/bin/env python3
# =============================================================================
# build_ontology_artifacts.py
#
# Network-dependent companion to build_schema_artifacts.py. Materializes the
# ontology-backed layers of a schema (dynamic.enum branches + static.enum terms)
# together with the enum allowedvalues into two downstream artifacts:
#
#   1. corpus/<field>[_<ontology>]_corpus.csv   -> per-field target corpus
#      (+ .json sidecars)                           (OntoMapEngine corpus_df;
#                                                    mixed-ontology fields are
#                                                    split one CSV per ontology)
#   2. <prefix>_ontology_terms.json             -> {field: {labels, synonym_lookup}}
#                                                    (R validateDataAgainstSchema
#                                                    ontology_terms argument, via
#                                                    OmicsMLRepoCuration::loadOntologyTerms)
#
# Unlike build_schema_artifacts.py (offline except the LLM alias step), this
# script calls ontology REST APIs to expand branches and fetch synonyms, so it
# needs network access. NCIt terms (NCIT: prefix) are resolved via the NCI
# EVSREST API; all other ontologies via the EBI OLS4 API. Both are public and
# unauthenticated -- no API key is required (the EVSREST calls used here do not
# touch the UMLS API).
#
# Usage:
#   python scripts/build_ontology_artifacts.py \
#       --input schema/curatedmetagenomicdata/cmd_schema.csv \
#       --outdir schema/curatedmetagenomicdata \
#       --prefix cmd \
#       [--fields disease,treatment] \
#       [--no-synonyms]        # skip synonym fetch (labels only, fewer calls)
# =============================================================================
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

from metaharmonizer.knowledge_db.combined_corpus_builder import (
    CombinedCorpusBuilder,
    build_schema_corpus,
    schema_to_ontology_terms,
)


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
        path, sep=sep, quotechar='"', skipinitialspace=True,
        keep_default_na=True, na_values=["NA"],
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--outdir", default=None, help="Defaults to dirname(--input).")
    p.add_argument("--prefix", default=None,
                   help="Defaults to basename(--input) minus _schema.csv.")
    p.add_argument("--fields", default=None,
                   help="Comma-separated subset of fields to build.")
    p.add_argument("--no-synonyms", action="store_true",
                   help="Skip ontology synonym fetch (labels only).")
    p.add_argument("--include-hierarchy", action="store_true",
                   help="Enrich corpus records with parent/child labels (~2x calls).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    outdir = Path(args.outdir) if args.outdir else Path(args.input).parent
    prefix = args.prefix or re.sub(r"(_schema)?\.csv$", "", Path(args.input).name)
    fields = [f.strip() for f in args.fields.split(",")] if args.fields else None
    corpus_dir = outdir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading schema: {args.input}", file=sys.stderr)
    df = read_schema(args.input)

    corpora = build_schema_corpus(
        df,
        fields=fields,
        include_hierarchy=args.include_hierarchy,
        fetch_static_synonyms=not args.no_synonyms,
    )

    builder = CombinedCorpusBuilder()
    for field, records in corpora.items():
        # One CSV per ontology so each is directly loadable by OntoMapEngine.
        written = builder.save_partitioned(records, str(corpus_dir / field))
        print(f"[corpus] {field:28} -> {len(records)} terms "
              f"({', '.join(written)})", file=sys.stderr)

    onto_terms = schema_to_ontology_terms(corpora)
    ot_path = outdir / f"{prefix}_ontology_terms.json"
    with open(ot_path, "w", encoding="utf-8") as fh:
        json.dump(onto_terms, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"[ontology_terms] {len(onto_terms)} fields -> {ot_path}", file=sys.stderr)
    print("Done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
