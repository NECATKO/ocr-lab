#!/bin/sh
# Kullanım: ./run.sh [bench.py seçenekleri]   — ör. ./run.sh --durum, ./run.sh -e tesseract
cd "$(dirname "$0")" || exit 1
PATH="$PWD/.venv-vllm/bin:$PWD/.venv-kraken/bin:$PATH"  # vllm ve kraken ayrı ortamlarda (bağımlılıkları çakışıyor)
export VLLM_USE_FLASHINFER_SAMPLER=0  # FlashInfer örnekleyici nvcc ister; CUDA toolkit yok
exec .venv/bin/python bench.py "$@"
