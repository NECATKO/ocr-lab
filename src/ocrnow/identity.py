"""Ham OCR'ı üreten ayarlar: önbellek ve benchmark aynı kimliği kullanır."""
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ocrnow import engines, paths


def _digest(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_id(path: str | Path | None) -> dict | None:
    """Yerel ağırlığı her koşuda GB'larca okumadan, hedefi ve değişiklik damgasını kaydet."""
    try:
        real = Path(path).resolve(strict=True) if path is not None else None
        if real is not None and real.is_file():
            st = real.stat()
            return {"path": str(real), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}
    except (OSError, RuntimeError):
        pass
    return None


def tesseract_profile() -> dict:
    binary = engines._which("tesseract")
    env = paths.subprocess_env()
    prefix = env.get("TESSDATA_PREFIX")
    version = None
    data_dir = prefix
    if binary:
        try:
            version = subprocess.check_output([binary, "--version"], env=env, text=True, timeout=5,
                                              stderr=subprocess.STDOUT).splitlines()[0].strip()
            listing = subprocess.check_output([binary, "--list-langs"], env=env, text=True, timeout=5,
                                              stderr=subprocess.STDOUT)
            if not data_dir and (m := re.search(r'available languages in "([^"]+)"', listing, re.I)):
                data_dir = m[1]
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    langs = engines.TESS_LANG.split("+")
    files = {lang: _file_id(Path(data_dir) / f"{lang}.traineddata") if data_dir else None for lang in langs}
    return {"version": version, "language": engines.TESS_LANG, "data_prefix": prefix,
            "data_files": files, "binary": _file_id(binary)}


def _hf_revision(repo: str) -> str | None:
    root = paths.hf_cache() / ("models--" + repo.replace("/", "--"))
    try:
        revision = (root / "refs" / "main").read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{40}", revision) and (root / "snapshots" / revision / "config.json").is_file():
            return revision
    except (OSError, UnicodeError):
        pass
    return None


def _package_version(binary: str | None, name: str) -> str | None:
    if binary:
        pat = re.compile(rf"{re.escape(name)}-([0-9].*)\.dist-info$")
        matches = [m.group(1) for p in Path(binary).parent.parent.glob(f"lib*/python*/site-packages/{name}-*.dist-info")
                   if (m := pat.fullmatch(p.name))]
        if len(matches) == 1:
            return matches[0]
    return None


def _file_key(info: dict | None) -> dict | None:
    """Önbellek anahtarı için dosya özeti: mutlak yol ve ctime_ns hariç (klasör taşıma/chmod önbelleği bozmaz)."""
    if not isinstance(info, dict):
        return None
    return {"name": Path(info["path"]).name if info.get("path") else None,
            "size": info.get("size"), "mtime_ns": info.get("mtime_ns")}


def _tess_key(tess: dict) -> dict:
    files = tess.get("data_files")
    return {
        "version": tess.get("version"),
        "language": tess.get("language"),
        "data_files": {k: _file_key(v) for k, v in files.items()} if isinstance(files, dict) else None,
    }


@dataclass(frozen=True)
class Runtime:
    engine: str
    fields: dict
    cacheable: bool
    server_options: dict

    def profile(self, mode: str) -> dict:
        eng = engines.ENGINES[self.engine]
        fields = {**self.fields, "engine": self.engine, "mode": mode, "prompt": eng.modes[mode].prompt,
                  "max_tokens": engines.MAX_TOKENS if eng.kind == "vlm" else None,
                  "temperature": engines.TEMPERATURE if eng.kind == "vlm" else None}
        semantic = {
            "engine": self.engine,
            "mode": mode,
            "backend": fields.get("backend"),
            "model": fields.get("model"),
            "model_revision": fields.get("model_revision"),
            "backend_version": fields.get("backend_version"),
            "serve": (eng.serve.replace("$VLLM", engines.VLLM_FLAGS)
                      if eng.serve and fields.get("backend") == "managed" else None),
            "environment": fields.get("environment"),
            "prompt": fields["prompt"],
            "max_tokens": fields["max_tokens"],
            "temperature": fields["temperature"],
            "model_files": [_file_key(f) for f in fields["model_files"]] if "model_files" in fields else None,
            "tesseract": _tess_key(fields["tesseract"]),
        }
        return {**fields, "id": _digest(semantic), "cacheable": self.cacheable}


def runtime(name: str, *, tess: dict | None = None) -> Runtime:
    """Ağırlık sürümü saptanamayan harici/VLM okumaları kalıcı önbelleğe girmez."""
    eng = engines.ENGINES[name]
    tess = tess if tess is not None else tesseract_profile()
    fields = {"tesseract": tess, "backend": eng.kind, "model": None, "model_revision": None}
    cacheable = bool(tess["version"])
    if name == "tesseract":
        return Runtime(name, fields, cacheable, {})
    if name == "kraken":
        model = _file_id(engines._kraken_model())
        binary = engines._which("kraken")
        fields.update(model_files=[model], executable=_file_id(binary), backend_version=_package_version(binary, "kraken"))
        return Runtime(name, fields, cacheable and model is not None, {})
    if external := engines.served_model(engines._base(eng)):
        return Runtime(name, {**fields, "backend": "external", "model": external}, False, {"external_model": external})
    cmd = engines._serve_cmd(eng)
    binary = engines._which(cmd[0])
    fields.update(backend="managed", command=cmd, executable=_file_id(binary),
                  environment={**{k: os.environ[k] for k in ("VLLM_BATCH_INVARIANT", "VLLM_ATTENTION_BACKEND",
                                                              "VLLM_USE_V1") if k in os.environ},
                               "VLLM_USE_FLASHINFER_SAMPLER": "0", **eng.env})
    if cmd[0] == "vllm":
        repo = cmd[2]
        revision = _hf_revision(repo)
        fields.update(model=repo, model_revision=revision, backend_version=_package_version(binary, "vllm"))
        return Runtime(name, fields, cacheable and revision is not None,
                       {"revision": revision, "expect_managed": True})
    weights = [_file_id(p) for p in cmd if p.endswith(".gguf")]
    fields.update(model=name, model_files=weights)
    return Runtime(name, fields, cacheable and bool(weights) and all(weights), {"expect_managed": True})
