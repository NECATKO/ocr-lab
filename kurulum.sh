#!/bin/sh
# Tüm OCR motorlarını kurar. Tekrar çalıştırmak güvenli: kurulu olan adım atlanır.
# Motor kurulumu (llama.cpp, vLLM, kraken, model ağırlıkları, sürümler) src/ocrnow/models.py'dedir.
set -e
cd "$(dirname "$0")"

printf '\n== ana ortam (.venv)\n'
[ -x .venv/bin/python ] || uv venv .venv --python 3.12 -q
VIRTUAL_ENV=.venv uv pip install -q -e ".[dev,lab]"

./ocr-now models install --hepsi
./ocr-now models
