"""Fails when the release tag does not match enigmoss.__version__.

python scripts/check_version.py v1.0.0rc1
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[1] / "src" / "enigmoss" / "_version.py"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_version.py <tag>", file=sys.stderr)
        return 2
    match = re.search(r'__version__ = "([^"]+)"', VERSION_FILE.read_text(encoding="utf-8"))
    if match is None:
        print("__version__ not found", file=sys.stderr)
        return 2
    tag = argv[1].removeprefix("refs/tags/").removeprefix("v")
    if tag != match.group(1):
        print(f"tag {argv[1]} does not match __version__ {match.group(1)}", file=sys.stderr)
        return 1
    print(f"version {tag} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
