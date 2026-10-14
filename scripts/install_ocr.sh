#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv-ocr/bin/python ]; then
  uv venv --python 3.11 .venv-ocr
fi
uv pip install --python .venv-ocr/bin/python 'mineru[pipeline]==2.7.1'
