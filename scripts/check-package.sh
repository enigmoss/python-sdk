#!/bin/sh
set -eu

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
uv build --out-dir "$work"
uv venv --python 3.11 "$work/venv"
uv pip install --no-index --python "$work/venv/bin/python" "$work"/*.whl
"$work/venv/bin/python" -c 'import enigmoss, importlib.metadata as metadata; assert enigmoss.__version__ == metadata.version("enigmoss"); assert not metadata.requires("enigmoss")'
