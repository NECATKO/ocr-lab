#!/bin/sh
# Tüm OCR motorlarını kurar. Tekrar çalıştırmak güvenli: kurulu olan adım atlanır.
set -e
cd "$(dirname "$0")"
LLAMA=b11756
VLLM=0.32.0
KRAKEN=7.1.1
KRAKEN_DOI=10.5281/zenodo.10592716  # CATMuS-Print Large (çok dilli matbu Latin)

step() { printf '\n== %s\n' "$1"; }

step "tesseract"
command -v tesseract >/dev/null || { echo "sudo gerekli: sudo apt install -y tesseract-ocr tesseract-ocr-tur"; exit 1; }

step "ana ortam (.venv)"
[ -x .venv/bin/python ] || uv venv .venv --python 3.12 -q
VIRTUAL_ENV=.venv uv pip install -q -e ".[dev,lab]"

step "llama.cpp $LLAMA (CUDA 12.8)"
if [ ! -x tools/llama.cpp/llama-server ]; then
    mkdir -p tools/llama.cpp && cd tools/llama.cpp
    URL=https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA
    for f in llama cudart-llama; do curl -fsSL "$URL/$f-$LLAMA-bin-ubuntu-cuda-12.8-x64.tar.gz" | tar xz; done
    mv ./*-x64/* llama-$LLAMA/* . && rmdir ./*-x64 llama-$LLAMA
    cd ../..
fi

step "vLLM $VLLM (.venv-vllm)"
[ -x .venv-vllm/bin/python ] || uv venv .venv-vllm --python 3.12 -q
VIRTUAL_ENV=.venv-vllm uv pip install -q "vllm==$VLLM"
# Yama: vLLM 0.32 + Triton 3.7'de DeepSeek-OCR-2 kodlayıcı çekirdeği derlenmiyor (LOG2E constexpr değil)
F=.venv-vllm/lib/python3.12/site-packages/vllm/model_executor/models/deepencoder.py
grep -q "ocr-lab yaması" "$F" || sed -i 's#^from vllm.triton_utils import LOG2E, tl, triton$#&\nLOG2E = tl.constexpr(LOG2E)  \# ocr-lab yaması: Triton 3.7 constexpr olmayan global kabul etmiyor#' "$F"

step "kraken $KRAKEN (.venv-kraken) + model"
[ -x .venv-kraken/bin/python ] || uv venv .venv-kraken --python 3.12 -q
VIRTUAL_ENV=.venv-kraken uv pip install -q "kraken==$KRAKEN"
mkdir -p models
if [ ! -f models/catmus-print-large.mlmodel ]; then
    .venv-kraken/bin/kraken get $KRAKEN_DOI
    cp "$(find ~/.local/share/htrmopo -name 'catmus-print-fondue-large.mlmodel' | head -1)" models/catmus-print-large.mlmodel
fi

step "model ağırlıkları (HF önbelleği)"
for m in PaddlePaddle/PaddleOCR-VL-1.6-GGUF zai-org/GLM-OCR lightonai/LightOnOCR-2-1B deepseek-ai/DeepSeek-OCR-2 dots-studio/dots.mocr; do
    echo "  $m"; .venv-vllm/bin/hf download "$m" >/dev/null
done
G=$(.venv-vllm/bin/hf download PaddlePaddle/PaddleOCR-VL-1.6-GGUF 2>/dev/null | tail -1 | sed "s/^path=//")
ln -sf "$G/PaddleOCR-VL-1.6-GGUF.gguf" models/PaddleOCR-VL-1.6.gguf
ln -sf "$G/PaddleOCR-VL-1.6-GGUF-mmproj.gguf" models/PaddleOCR-VL-1.6-mmproj.gguf

step "durum"
./run.sh --durum
