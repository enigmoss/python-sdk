"""The shipped package and its docs name no internal systems, other brands or real hosts.

Forbidden words are stored as sha256 hashes so this file does not contain them either.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = {
    "9e786cce0d95f0f608958afef7a476a21f6c6fb3dce981d2ac054b5f1e9cb921",
    "d4193e8816774fc0fd181a3eb2a024586249f31ef2850fcc10875ad9366725dd",
    "38299d69706d3c0f80584a484a1c65885f94a3c159110e468c4e45215960b621",
    "68ab67cf6b8b4f1bbeda25ca2d1e6e8929625ebe9d85d5e5379c81bef4cc16b6",
    "9e2aad8cd8b012c8a3bed83a18d7e7f2d18218e5701e41ec2dd9969fac3b57b6",
}
# tavrion.org is the public fallback host of the production API: the docs must name it.
ALLOWED_HOSTS = re.compile(
    r"(?:[a-z0-9-]+\.)*example\.(?:com|org)|shop\.example|localhost|127\.0\.0\.1|tavrion\.org"
)
URL = re.compile(r"https?://([A-Za-z0-9.-]+)")


def texts() -> dict[str, str]:
    files = [
        ROOT / "README.md",
        ROOT / "llms.txt",
        ROOT / "CHANGELOG.md",
        *sorted((ROOT / "src").rglob("*.py")),
    ]
    return {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in files}


def wheel_texts(tmp_path: Path) -> dict[str, str]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed: the wheel is checked in CI")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)], check=True, capture_output=True
    )
    (wheel,) = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        return {
            name: archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if not name.endswith("/RECORD")
        }


def violations(name: str, text: str) -> list[str]:
    found = []
    for word in set(re.findall(r"[a-z0-9]+", text.lower())):
        for candidate in (word, word.removesuffix("s")):
            if hashlib.sha256(candidate.encode()).hexdigest() in FORBIDDEN:
                found.append(
                    f"{name}: forbidden word (sha256 {hashlib.sha256(candidate.encode()).hexdigest()[:12]})"
                )
    for host in URL.findall(text):
        if not ALLOWED_HOSTS.fullmatch(host.lower().rstrip(".")):
            found.append(f"{name}: host {host}")
    return found


def test_sources_and_docs() -> None:
    problems = [p for name, text in texts().items() for p in violations(name, text)]
    assert problems == []


def test_wheel(tmp_path: Path) -> None:
    contents = wheel_texts(tmp_path)
    assert "enigmoss/llms.txt" in contents
    assert "enigmoss/py.typed" in contents
    metadata = next(text for name, text in contents.items() if name.endswith("METADATA"))
    assert "Requires-Dist" not in metadata
    assert "Requires-Python: >=3.11" in metadata
    problems = [p for name, text in contents.items() for p in violations(name, text)]
    assert problems == []
