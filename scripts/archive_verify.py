"""Verify archived files still match the manifest; no network access."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path("workspaces/orangecut/archive/manifest.json"))
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    root = args.manifest.parent.parent.parent.parent
    checked = missing = bad = 0
    for entry in manifest["entries"]:
        for row in entry.get("files", []):
            path = Path(entry["new_path"]) / row["path"]
            checked += 1
            if not path.exists():
                print(f"MISSING {path}")
                missing += 1
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != row["sha256"]:
                print(f"CHANGED {path}")
                bad += 1
    print(f"checked {checked} archived files: {missing} missing, {bad} changed")
    return 0 if not missing and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
