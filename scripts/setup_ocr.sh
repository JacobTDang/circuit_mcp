#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OCR_ENV=${CIRCUIT_MCP_OCR_ENV:-"$ROOT/.venv-ocr.nosync"}
MODEL_NAME=${CIRCUIT_MCP_OCR_REPOSITORY:-"wanderkid/unimernet_small"}
MODEL_DIR=${CIRCUIT_MCP_OCR_MODEL:-"$ROOT/models/unimernet_small"}

command -v uv >/dev/null 2>&1 || {
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
}

uv venv --python 3.12 "$OCR_ENV"
uv pip install --python "$OCR_ENV/bin/python" "unimernet==0.2.3"
mkdir -p "$MODEL_DIR"
"$OCR_ENV/bin/hf" download "$MODEL_NAME" --local-dir "$MODEL_DIR"

echo "UniMERNet environment: $OCR_ENV"
echo "UniMERNet model:       $MODEL_DIR"
echo "Run ocr_status(load_model=true) through MCP to verify MPS."
