#!/usr/bin/env python3
"""Release checks for every schema in the registry (run by CI, also locally).

For each ``schema/<dir>/meta.yaml``:

1. **meta**: ``meta.yaml`` has ``schema``, a SemVer ``version``, an ISO
   ``date``, and ``tables``/``build`` entries that exist.
2. **parse**: MetaHarmonizer's ``read_schema_table()`` reads every table
   (skipped with a notice when ``metaharmonizer`` is not importable).
3. **artifacts**: rebuilding each ``build`` entry with
   ``build_schema_artifacts.py --no-alias`` reproduces the committed
   ``.linkml.yaml``, ``_target_attrs.csv`` and ``_target_attrs_allowed_values.json``
   byte for byte. Run the builder and commit its output when this fails.
4. **lint**: ``lint_schema.py`` on every table. Errors fail the check only when
   ``meta.yaml`` says ``lint: blocking``; with ``lint: report`` they are shown.
5. **version** (with ``--base REF``): if anything under ``schema/<dir>/``
   changed since ``REF``, ``version`` must be higher than at ``REF`` and
   ``CHANGELOG.md`` must have a ``## [<version>]`` heading.

The table format itself is checked by ``Rscript scripts/format_schema.R --check``.

Usage::

    python scripts/check_schemas.py                       # checks 1-4
    python scripts/check_schemas.py --base origin/main    # plus the version check
    python scripts/check_schemas.py --skip-artifacts      # without rebuilding

Exit status: 0 all checks pass, 1 a check failed, 2 usage or IO error.
"""
from __future__ import annotations

import argparse
import filecmp
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ARTIFACTS = ("{p}.linkml.yaml", "{p}_target_attrs.csv",
             "{p}_target_attrs_allowed_values.json")
NOT_RELEASED = {"CHANGELOG.md", "README.md"}  # editing these alone needs no bump


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, msg: str) -> None:
        print(f"  ok    {msg}")

    def note(self, msg: str) -> None:
        print(f"  note  {msg}")

    def fail(self, msg: str) -> None:
        self.failures += 1
        print(f"  FAIL  {msg}")


def semver(v: str) -> tuple[int, int, int]:
    return tuple(int(x) for x in SEMVER.match(v).groups())  # type: ignore[union-attr]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


def check_meta(d: Path, meta: dict, rep: Report) -> bool:
    good = True
    for key in ("schema", "version", "date", "tables"):
        if key not in meta:
            rep.fail(f"meta.yaml: missing '{key}'")
            good = False
    if not good:
        return False
    if not SEMVER.match(str(meta["version"])):
        rep.fail(f"meta.yaml: version {meta['version']!r} is not MAJOR.MINOR.PATCH")
        good = False
    try:
        date.fromisoformat(str(meta["date"]))
    except ValueError:
        rep.fail(f"meta.yaml: date {meta['date']!r} is not YYYY-MM-DD")
        good = False
    if meta.get("lint", "report") not in {"report", "blocking"}:
        rep.fail(f"meta.yaml: lint {meta.get('lint')!r} must be report or blocking")
        good = False
    for t in list(meta["tables"]) + list((meta.get("build") or {}).keys()):
        if not (d / t).exists():
            rep.fail(f"meta.yaml: table {t} does not exist")
            good = False
    if good:
        rep.ok(f"meta.yaml: {meta['schema']} {meta['version']} ({meta['date']})")
    return good


def check_parse(d: Path, meta: dict, rep: Report) -> None:
    try:
        from metaharmonizer.schema_vocab import read_schema_table
    except ImportError:
        rep.note("metaharmonizer not importable; read_schema_table() check skipped")
        return
    for t in meta["tables"]:
        try:
            n = len(read_schema_table(str(d / t)))
            rep.ok(f"read_schema_table({t}): {n} fields")
        except Exception as e:  # noqa: BLE001 - any parse failure is a failure
            rep.fail(f"read_schema_table({t}) raised {type(e).__name__}: {e}")


def check_artifacts(d: Path, meta: dict, rep: Report) -> None:
    for table, prefix in (meta.get("build") or {}).items():
        with tempfile.TemporaryDirectory() as tmp:
            # The builder reads meta.yaml from the table's own directory, so only
            # the output goes to the temporary directory.
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_schema_artifacts.py"),
                 "--input", str(d / table), "--outdir", tmp, "--prefix", prefix,
                 "--no-alias"], cwd=ROOT, capture_output=True, text=True)
            if r.returncode:
                rep.fail(f"build {table}: exit {r.returncode}\n{r.stderr}")
                continue
            stale = [a.format(p=prefix) for a in ARTIFACTS
                     if not (d / a.format(p=prefix)).exists()
                     or not filecmp.cmp(Path(tmp) / a.format(p=prefix),
                                        d / a.format(p=prefix), shallow=False)]
            if stale:
                rep.fail(f"build {table}: committed artifacts are stale: {', '.join(stale)}. "
                         f"Run: python scripts/build_schema_artifacts.py --input "
                         f"{(d / table).relative_to(ROOT)} --prefix {prefix} --no-alias")
            else:
                rep.ok(f"build {table}: artifacts up to date")


def check_lint(d: Path, meta: dict, rep: Report) -> None:
    blocking = meta.get("lint", "report") == "blocking"
    for t in meta["tables"]:
        r = subprocess.run([sys.executable, str(ROOT / "scripts/lint_schema.py"),
                            "--quiet", str(d / t)], cwd=ROOT,
                           capture_output=True, text=True)
        summary = next((ln for ln in r.stdout.splitlines() if ln.startswith("==")),
                       r.stdout.strip() or r.stderr.strip())
        summary = summary.replace(f"== {d / t}: ", "")
        if r.returncode == 0:
            rep.ok(f"lint {t}: {summary}")
        elif r.returncode == 1 and not blocking:
            rep.note(f"lint {t}: {summary} (lint: report, not failing)")
        else:
            rep.fail(f"lint {t}: {summary}")


def check_version(d: Path, meta: dict, base: str, rep: Report) -> None:
    rel = d.relative_to(ROOT).as_posix()
    changed = [f for f in git("diff", "--name-only", f"{base}...HEAD", "--", rel).split()
               if Path(f).name not in NOT_RELEASED]
    if not changed:
        rep.ok(f"version: no changes since {base}")
        return
    try:
        old = yaml.safe_load(git("show", f"{base}:{rel}/meta.yaml")) or {}
    except subprocess.CalledProcessError:
        rep.ok(f"version: new schema since {base} ({meta['version']})")
        old = {}
    if old:
        if not SEMVER.match(str(old.get("version", ""))):
            rep.note(f"version: {base} has no valid version; bump not checked")
        elif semver(str(meta["version"])) <= semver(str(old["version"])):
            rep.fail(f"version: {len(changed)} file(s) changed since {base} "
                     f"but version is still {meta['version']} (was {old['version']}); "
                     "bump it in meta.yaml")
        else:
            rep.ok(f"version: {old['version']} -> {meta['version']}")
    log = d / "CHANGELOG.md"
    heading = re.compile(rf"^## \[{re.escape(str(meta['version']))}\]", re.M)
    if not log.exists() or not heading.search(log.read_text(encoding="utf-8")):
        rep.fail(f"version: CHANGELOG.md has no '## [{meta['version']}]' entry")
    else:
        rep.ok(f"version: CHANGELOG.md has the {meta['version']} entry")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", help="git ref to compare against for the version check")
    p.add_argument("--skip-artifacts", action="store_true")
    a = p.parse_args(argv)

    metas = sorted((ROOT / "schema").glob("*/meta.yaml"))
    if not metas:
        print("no schema/*/meta.yaml found", file=sys.stderr)
        return 2
    rep = Report()
    for m in metas:
        d = m.parent
        print(f"== {d.relative_to(ROOT)}")
        try:
            meta = yaml.safe_load(m.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            rep.fail(f"meta.yaml does not parse: {e}")
            continue
        if not check_meta(d, meta, rep):
            continue
        check_parse(d, meta, rep)
        if not a.skip_artifacts:
            check_artifacts(d, meta, rep)
        check_lint(d, meta, rep)
        if a.base:
            check_version(d, meta, a.base, rep)
    print(f"\n{rep.failures} failing check(s) in {len(metas)} schema(s)")
    return 1 if rep.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
