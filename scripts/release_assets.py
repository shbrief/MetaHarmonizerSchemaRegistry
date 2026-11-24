#!/usr/bin/env python3
"""Print the files a schema's GitHub release carries, one per line.

Usage: python scripts/release_assets.py schema/<dir>/meta.yaml

The release carries meta.yaml, CHANGELOG.md, the tables listed under
``tables``, and the artifacts built from them (every other tracked .csv,
.yaml or .json in the directory that is not a schema table). A
``*_schema*.csv`` left out of ``tables``, such as a working list, is not
released.
"""
import re
import subprocess
import sys
from pathlib import Path

import yaml

meta_path = Path(sys.argv[1])
d = meta_path.parent
meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
tables = set(meta.get("tables") or [])
tracked = subprocess.run(["git", "ls-files", str(d)], check=True,
                         capture_output=True, text=True).stdout.split()
for f in tracked:
    name = Path(f).name
    if Path(f).parent != d:
        continue
    if re.search(r"_schema.*\.csv$", name) and name not in tables:
        continue
    if name == "CHANGELOG.md" or re.search(r"\.(csv|ya?ml|json)$", name):
        print(f)
