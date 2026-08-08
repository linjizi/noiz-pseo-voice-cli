#!/usr/bin/env bash
set -euo pipefail

# Build and publish noiz-pseo-voice-cli to PyPI.
# Requires: PYPI_API_TOKEN (PyPI API token) and the dev extras (build, twine).
#
# Usage:
#   PYPI_API_TOKEN=... scripts/publish-pypi.sh

cd "$(dirname "$0")/.."

if [[ -z "${PYPI_API_TOKEN:-}" ]]; then
  echo "PYPI_API_TOKEN is required" >&2
  exit 1
fi

python -m build
python -m twine upload --non-interactive --username __token__ --password "$PYPI_API_TOKEN" dist/*
echo "Published $(ls dist | tr '\n' ' ')"
