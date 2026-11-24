#!/usr/bin/env python3
"""Lint registry schema tables against the schema-table conventions.

The conventions (see "Schema table conventions" in the README):

* File: UTF-8, comma-separated CSV written by ``write.csv(row.names = FALSE)``,
  ``NA`` for empty cells.
* Headers: dotted lower case, in the canonical column order.
* ``col.name``: non-empty and unique (no naming style is enforced).
* ``col.class`` / ``unique`` / ``required`` / ``multiplevalues``: closed vocabularies.
* ``corpus.type``: required; ``|``-joined tokens from dynamic_enum, static_enum,
  custom_enum, regexp and any. ``regexp`` is never combined with the enum types,
  and ``static_enum`` and ``custom_enum`` are never combined with each other.
  - ``static_enum``: a closed list whose labels should carry ontology term IDs in
    ``static.enum``. A missing ID is a gap to fill (warning).
  - ``custom_enum``: a closed list of non-ontology terms by design.
    ``static.enum`` stays ``NA`` (error otherwise).
* ``allowedvalues``: read according to ``corpus.type``. A ``static_enum`` or
  ``custom_enum`` row holds a ``|`` list of labels, a ``regexp`` row holds one
  regex, an ``any`` row holds ``NA`` or ``.+``, and any other row holds ``NA``.
* Retired ``corpus.type`` tokens are errors with a replacement hint: ``binary``
  becomes ``static_enum``; ``integer``, ``numeric`` and ``character`` become
  ``regexp``, or ``any`` when there is no pattern.
* ``static.enum``: a ``|`` list of CURIEs, the same length and order as the
  labels. A label without an ID gets an empty slot (``NCIT:C1||NCIT:C3``) or
  ``NA``.
* ``dynamic.enum``: a ``|`` list of upper-case CURIEs, set exactly when the
  field is ``dynamic_enum``. ``dynamic.enum.property`` is ``descendant`` or
  ``children``.
* ``delimiter``: ``<;>`` for multi-valued fields, ``NA`` otherwise.

Optional checks:

* ``--online``: flag obsolete or unknown ``dynamic.enum`` roots via OLS4. This
  needs MetaHarmonizer and network access.
* If MetaHarmonizer is importable, every table is also parsed with
  ``metaharmonizer.schema_vocab.read_schema_table()``. Its warnings are
  reported, and so is any label list that MetaHarmonizer would drop.

Usage::

    python scripts/lint_schema.py                       # every schema/*/*_schema*.csv
    python scripts/lint_schema.py schema/cmd/cmd_schema.csv --online
    python scripts/lint_schema.py --strict               # warnings fail too

Exit status: 0 clean, 1 errors (or warnings with --strict), 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Canonical column order. OPTIONAL columns may be absent; all others are required.
CANONICAL = [
    "col.name", "col.class", "unique", "required", "scope", "unit.of",
    "propagate.study.value", "multiplevalues", "delimiter", "separator",
    "description", "corpus.type", "allowedvalues", "static.enum",
    "dynamic.enum", "dynamic.enum.property",
]
OPTIONAL = {"scope", "unit.of", "propagate.study.value"}
# Old spellings and their replacements.
RENAMED = {"Separater": "separator", "uniqueness": "unique", "requiredness": "required",
           "dynamic_enum": "dynamic.enum", "dynamic_enum_property": "dynamic.enum.property"}

COL_CLASSES = {"character", "integer", "numeric", "logical", "date"}
NUMERIC = {"integer", "numeric"}
CORPUS_TYPES = {"dynamic_enum", "static_enum", "custom_enum", "regexp", "any"}
ENUM_TYPES = {"dynamic_enum", "static_enum", "custom_enum"}
LIST_TYPES = {"static_enum", "custom_enum"}  # allowedvalues holds a label list
PROPERTIES = {"descendant", "children"}
DATA_DELIMITER = "<;>"
# Older corpus.type tokens still in the registry, and what replaces them.
LEGACY_CORPUS_TYPES = {
    "binary": "static_enum (allowedvalues Yes|No, static.enum NCIT:C49488|NCIT:C49487)",
    "integer": "regexp (the pattern stays in allowedvalues)",
    "numeric": "regexp (the pattern stays in allowedvalues)",
    "character": "regexp (the pattern stays in allowedvalues)",
}
# allowedvalues an `any` field may carry: "any non-empty value".
ANY_PATTERNS = {".+", ".*"}
# Prefixes whose canonical spelling is not upper case (bioregistry / OLS usage).
MIXED_CASE_PREFIXES = {"Orphanet"}
CURIE = re.compile(r"^([A-Za-z][A-Za-z0-9.]*):([^\s|:]+)$")
UNDERSCORE_ID = re.compile(r"^[A-Za-z]+_[0-9A-Za-z]+$")  # EFO_0000408 instead of EFO:0000408


def is_na(v) -> bool:
    return v is None or str(v).strip() in ("", "NA")


def tokens(v: str) -> list[str]:
    return [t.strip() for t in str(v).split("|")]


@dataclass
class Report:
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def err(self, where: str, msg: str):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str):
        self.warnings.append(f"{where}: {msg}")


def check_curie(rep: Report, where: str, tok: str, what: str):
    if UNDERSCORE_ID.match(tok):
        rep.err(where, f"{what} {tok!r} is not a CURIE (use ':' — {tok.replace('_', ':', 1)!r})")
        return
    m = CURIE.match(tok)
    if not m:
        rep.err(where, f"{what} {tok!r} is not a CURIE (PREFIX:local)")
        return
    prefix = m.group(1)
    if prefix != prefix.upper() and prefix not in MIXED_CASE_PREFIXES:
        rep.err(where, f"{what} {tok!r}: prefix must be upper case ({prefix.upper()}:)")


def read_rows(path: str, rep: Report) -> tuple[list[str], list[dict]] | None:
    try:
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        rep.err("file", f"not UTF-8 ({e})")
        return None
    except OSError as e:
        rep.err("file", f"cannot read ({e})")
        return None
    header_line = text.splitlines()[0] if text else ""
    if not re.match(r'^\s*"?[^",]*"?\s*,', header_line):
        rep.err("file", "not a comma-separated CSV (write.table() layout?) — rewrite with write.csv(row.names = FALSE)")
        return None
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


def check_header(rep: Report, cols: list[str]):
    for c in cols:
        if c in RENAMED:
            rep.err("header", f"column {c!r} should be named {RENAMED[c]!r}")
        elif c not in CANONICAL:
            if c != c.lower() or "_" in c:
                rep.err("header", f"column {c!r} is not dotted lower case")
            else:
                rep.warn("header", f"unknown column {c!r}")
    missing = [c for c in CANONICAL if c not in OPTIONAL and c not in cols]
    if missing:
        rep.err("header", f"missing required column(s): {', '.join(missing)}")
    known = [c for c in cols if c in CANONICAL]
    expected = [c for c in CANONICAL if c in known]
    if known != expected:
        rep.warn("header", "columns are not in canonical order: " + ", ".join(expected))


def check_row(rep: Report, r: dict, seen: set):
    name = (r.get("col.name") or "").strip()
    w = f"{name or '<empty col.name>'}"
    if is_na(name):
        rep.err(w, "col.name is empty")
        return
    if name in seen:
        rep.err(w, "duplicate col.name")
    seen.add(name)

    g = lambda c: r.get(c)  # noqa: E731

    cls = g("col.class")
    if cls is not None:
        if cls == "double":
            rep.err(w, "col.class 'double' — use 'numeric'")
        elif cls not in COL_CLASSES:
            rep.err(w, f"col.class {cls!r} not in {sorted(COL_CLASSES)}")
    if g("unique") is not None and g("unique") not in ("unique", "non-unique"):
        rep.err(w, f"unique {g('unique')!r} must be 'unique' or 'non-unique'")
    if g("required") is not None and g("required") not in ("required", "optional"):
        rep.err(w, f"required {g('required')!r} must be 'required' or 'optional'")
    mv = g("multiplevalues")
    if mv is not None and mv not in ("TRUE", "FALSE"):
        rep.err(w, f"multiplevalues {mv!r} must be TRUE or FALSE")

    # corpus.type
    ct = g("corpus.type")
    types: set = set()
    if ct is not None:
        if is_na(ct):
            rep.err(w, "corpus.type is required (dynamic_enum, static_enum, custom_enum, regexp or any)")
        else:
            if ";" in ct:
                rep.err(w, f"corpus.type {ct!r}: join tokens with '|', not ';'")
            toks = [t.strip() for t in re.split(r"[|;]", ct) if t.strip()]
            # Typed legacy tokens become regexp when a pattern is given, else any.
            typed = "regexp" if not is_na(g("allowedvalues")) else "any"
            legacy = {"binary": "static_enum",
                      "integer": typed, "numeric": typed, "character": typed}
            for t in toks:
                if t in LEGACY_CORPUS_TYPES:
                    hint = (LEGACY_CORPUS_TYPES[t] if legacy[t] != "any"
                            else "any (no pattern is given)")
                    rep.err(w, f"corpus.type {t!r} is retired — use {hint}")
                elif t not in CORPUS_TYPES:
                    rep.err(w, f"corpus.type has unknown token {t!r}")
            # Read legacy tokens with their replacement meaning so the
            # allowedvalues checks below still apply.
            types = {legacy.get(t, t) for t in toks} & CORPUS_TYPES
            if "regexp" in types and types & ENUM_TYPES:
                rep.err(w, "corpus.type 'regexp' cannot be combined with enum types")
            if LIST_TYPES <= types:
                rep.err(w, "corpus.type cannot be both static_enum and custom_enum")
            if "any" in types and len(types) > 1:
                rep.warn(w, "corpus.type 'any' combined with other types")

    # allowedvalues / static.enum
    av, se = g("allowedvalues"), g("static.enum")
    labels: list[str] = []
    if av is not None:
        if types & LIST_TYPES:
            if is_na(av):
                kind = "static_enum" if "static_enum" in types else "custom_enum"
                rep.err(w, f"{kind} field has no allowedvalues labels")
            else:
                labels = tokens(av)
                if any(not t for t in labels):
                    rep.err(w, "allowedvalues has an empty label (stray '|')")
                if "|" not in av and ";" in av:
                    rep.warn(w, f"allowedvalues {av[:60]!r} has ';' but no '|' — a ';'-separated list? Use '|'")
                if len(set(labels)) != len(labels):
                    rep.warn(w, "allowedvalues has duplicate labels")
        elif "regexp" in types:
            if is_na(av):
                rep.err(w, "regexp field has no pattern in allowedvalues")
            else:
                try:
                    re.compile(av)
                except re.error as e:
                    rep.err(w, f"allowedvalues is not a valid regex ({e})")
        elif not is_na(av) and not ("any" in types and av in ANY_PATTERNS):
            rep.warn(w, f"allowedvalues {av[:60]!r} is set but corpus.type is {ct!r}; "
                        "use static_enum/custom_enum (label list) or regexp (pattern), or NA")
    codes = tokens(se) if se is not None and not is_na(se) else []
    if codes:
        if "custom_enum" in types:
            if any(not is_na(c) for c in codes):
                rep.err(w, "custom_enum has term IDs in static.enum — non-ontology lists "
                           "carry none; use static_enum if these labels have IDs")
        elif "static_enum" not in types:
            rep.err(w, "static.enum is set but corpus.type has no static_enum")
        if ";" in se:
            rep.err(w, "static.enum: separate codes with '|', not ';'")
        for c in codes:
            if not is_na(c):
                check_curie(rep, w, c, "static.enum code")
        if labels and len(codes) != len(labels):
            rep.err(w, f"static.enum has {len(codes)} code(s) for {len(labels)} label(s); "
                       "pad with NA so they pair by position")
    if "static_enum" in types and labels:
        n_missing = (len(labels) if not codes
                     else sum(is_na(c) for c in codes[:len(labels)]))
        if n_missing:
            rep.warn(w, f"{n_missing} of {len(labels)} static_enum label(s) have no term ID "
                        "in static.enum (fill them in, or use custom_enum if the list is "
                        "non-ontology by design)")

    # dynamic.enum / property
    de, prop = g("dynamic.enum"), g("dynamic.enum.property")
    if de is not None:
        if is_na(de):
            if "dynamic_enum" in types:
                rep.err(w, "dynamic_enum field has no dynamic.enum root")
        else:
            if "dynamic_enum" not in types:
                rep.err(w, "dynamic.enum is set but corpus.type has no dynamic_enum")
            if cls in NUMERIC:
                rep.err(w, f"numeric field ({cls}) has ontology root(s)")
            if ";" in de:
                rep.err(w, f"dynamic.enum {de!r}: separate roots with '|', not ';'")
            for root in [t.strip() for t in re.split(r"[|;]", de) if t.strip()]:
                check_curie(rep, w, root, "root")
    if prop is not None:
        if not is_na(de or "NA"):
            if is_na(prop):
                rep.warn(w, "dynamic.enum.property is empty (defaults to descendant) — set it explicitly")
            elif prop not in PROPERTIES:
                rep.err(w, f"dynamic.enum.property {prop!r} must be 'descendant' or 'children'")
        elif not is_na(prop):
            rep.warn(w, f"dynamic.enum.property {prop!r} set without a dynamic.enum root")

    # delimiter
    delim = g("delimiter")
    if delim is not None and mv in ("TRUE", "FALSE"):
        if mv == "TRUE" and delim != DATA_DELIMITER:
            rep.err(w, f"multi-valued field needs delimiter {DATA_DELIMITER!r} (has {delim!r})")
        if mv == "FALSE" and not is_na(delim):
            rep.warn(w, f"single-valued field has delimiter {delim!r}")

    if is_na(g("description") or "NA"):
        rep.warn(w, "description is empty")


def check_metaharmonizer(rep: Report, path: str, rows: list[dict]):
    try:
        from metaharmonizer.schema_vocab import read_schema_table
    except Exception:
        return False
    log = logging.getLogger("metaharmonizer")
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(h)
    try:
        vocab = read_schema_table(path)
    except Exception as e:
        rep.err("metaharmonizer", f"read_schema_table() failed: {e}")
        return True
    finally:
        root.removeHandler(h)
    for line in buf.getvalue().splitlines():
        if line.strip():
            rep.warn("metaharmonizer", line.strip())
    for r in rows:
        name = r.get("col.name")
        ct = r.get("corpus.type") or ""
        if any(t in ct for t in LIST_TYPES) and not is_na(r.get("allowedvalues")):
            v = vocab.get(name)
            if v is not None and not v.allowed_values:
                rep.err(name, "MetaHarmonizer drops this allowedvalues list")
    _ = log
    return True


def check_online(rep: Report, rows: list[dict]):
    try:
        from metaharmonizer._async_utils import run_async
        from metaharmonizer.knowledge_db.db_clients.ols_db import OLSDb
    except Exception:
        rep.warn("online", "MetaHarmonizer not importable; skipped OLS root checks")
        return
    ols = OLSDb()
    roots = {}
    for r in rows:
        de = r.get("dynamic.enum")
        if de and not is_na(de):
            for t in re.split(r"[|;]", de):
                t = t.strip()
                if CURIE.match(t):
                    roots.setdefault(t, []).append(r.get("col.name"))
    for root, fields in sorted(roots.items()):
        try:
            term = run_async(ols.get_term(root))
        except Exception as e:
            rep.err(",".join(fields), f"root {root} not found in OLS ({str(e)[:80]})")
            continue
        if not term:
            rep.err(",".join(fields), f"root {root} not found in OLS")
        elif term.get("is_obsolete"):
            rep.err(",".join(fields), f"root {root} ({term.get('label')!r}) is obsolete")


def lint(path: str, online: bool) -> Report:
    rep = Report(path)
    got = read_rows(path, rep)
    if got is None:
        return rep
    cols, rows = got
    check_header(rep, cols)
    seen: set = set()
    for r in rows:
        check_row(rep, r, seen)
    if not check_metaharmonizer(rep, path, rows):
        rep.warn("metaharmonizer", "not importable; skipped the read_schema_table() check")
    if online:
        check_online(rep, rows)
    return rep


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", help="schema tables (default: schema/*/*_schema*.csv)")
    p.add_argument("--online", action="store_true", help="check roots against OLS4 (network)")
    p.add_argument("--strict", action="store_true", help="fail on warnings too")
    p.add_argument("--quiet", action="store_true", help="print only the per-file summary")
    a = p.parse_args(argv)
    paths = a.paths or sorted(glob.glob("schema/*/*_schema*.csv"))
    if not paths:
        print("no schema tables found", file=sys.stderr)
        return 2
    n_err = n_warn = 0
    for path in paths:
        rep = lint(path, a.online)
        n_err += len(rep.errors)
        n_warn += len(rep.warnings)
        print(f"== {path}: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
        if not a.quiet:
            for e in rep.errors:
                print(f"  ERROR  {e}")
            for w in rep.warnings:
                print(f"  warn   {w}")
    print(f"\nTotal: {n_err} error(s), {n_warn} warning(s) in {len(paths)} file(s)")
    return 1 if n_err or (a.strict and n_warn) else 0


if __name__ == "__main__":
    sys.exit(main())
