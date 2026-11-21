#!/usr/bin/env python3
"""Build dist/smart-grocery-saver-<version>.zip for Skill Market upload and check the contest limits
(≤ 30 files, ≤ 40 MB). Usage: python3 tools/package_skill.py [--include-example-outputs]"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "smart-grocery-saver"
MAX_FILES, MAX_BYTES = 30, 40 * 1024 * 1024
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}


def collect(include_outputs):
    files = []
    for p in sorted(SKILL.rglob("*")):
        rel = p.relative_to(SKILL)
        if not p.is_file() or SKIP_PARTS & set(rel.parts) or p.suffix in (".pyc", ".pyo"):
            continue
        if not include_outputs and rel.parts[:2] == ("examples", "output"):
            continue
        files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-example-outputs", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    version = json.loads((SKILL / "skill.json").read_text(encoding="utf-8"))["version"]
    files = collect(a.include_example_outputs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / ("smart-grocery-saver-%s.zip" % version)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, Path("smart-grocery-saver") / f.relative_to(SKILL))
    size = zpath.stat().st_size
    for f in files:
        print("  " + str(f.relative_to(SKILL)))
    print("%s: %d files, %.1f KB (limits: %d files, %d MB)" % (zpath.relative_to(ROOT), len(files), size / 1024,
                                                              MAX_FILES, MAX_BYTES // 1024 // 1024))
    if len(files) > MAX_FILES or size > MAX_BYTES:
        print("ERROR: package exceeds the contest submission limits", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
