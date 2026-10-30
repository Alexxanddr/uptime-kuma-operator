#!/bin/sh
# Complete automated validation; defaults to a disposable Kind cluster.
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
    uv venv --python 3.11 .venv
fi
uv pip sync --python .venv/bin/python requirements-dev.txt
exec .venv/bin/python scripts/check.py "$@"
