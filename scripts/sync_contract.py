"""Copies the shared contract snapshot into tests/fixtures/contract.

    python scripts/sync_contract.py [<contract directory>]

The default source is ../contract next to this repository. Tests never read the network.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "fixtures" / "contract"


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else ROOT.parent / "contract"
    files = sorted(source.glob("*.json"))
    if not files:
        print(f"No *.json contract files in {source}", file=sys.stderr)
        return 2
    TARGET.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copyfile(path, TARGET / path.name)
        print(f"copied {path.name}")
    source_file = source / "SOURCE"
    if source_file.is_file():
        shutil.copyfile(source_file, TARGET / "SOURCE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
