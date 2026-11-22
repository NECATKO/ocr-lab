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


def tesseract_profile(lang: str | None = None) -> dict:
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
    tess_lang = lang or engines.TESS_LANG
    langs = tess_lang.split("+")
    files = {lg: _file_id(Path(data_dir) / f"{lg}.traineddata") if data_dir else None for lg in langs}
    return {"version": version, "language": tess_lang, "data_prefix": prefix,
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

    def profile(self, mode: str, *, prompt_override: str | None = None) -> dict:
        eng = engines.ENGINES[self.engine]
        prompt = (prompt_override or eng.modes[mode].prompt) if eng.kind == "api" else eng.modes[mode].prompt
        fields = {**self.fields, "engine": self.engine, "mode": mode, "prompt": prompt,
                  "max_tokens": engines.MAX_TOKENS if eng.kind in ("vlm", "api") else None,
                  "temperature": engines.TEMPERATURE if eng.kind in ("vlm", "api") else None}
        semantic = {
            "engine": self.engine,
            "mode": mode,
            "backend": fields.get("backend"),
            "base_url": fields.get("base_url"),
            "model": fields.get("model"),
            "model_revision": fields.get("model_revision"),
            "backend_version": fields.get("backend_version"),
            "serve": (engines.serve_command(eng)
                      if eng.serve and fields.get("backend") == "managed" else None),
            "environment": fields.get("environment"),
            "prompt": fields["prompt"],
            "max_tokens": fields["max_tokens"],
            "temperature": fields["temperature"],
            "model_files": [_file_key(f) for f in fields["model_files"]] if "model_files" in fields else None,
            "tesseract": _tess_key(fields["tesseract"]),
        }
        return {**fields, "id": _digest(semantic), "cacheable": self.cacheable}


def runtime(name: str, *, tess: dict | None = None, lang: str | None = None,
            kraken_model: str | None = None) -> Runtime:
    """Ağırlık sürümü saptanamayan harici/VLM okumaları kalıcı önbelleğe girmez."""
    eng = engines.ENGINES[name]
    tess = tess if tess is not None else (tesseract_profile(lang=lang) if lang else tesseract_profile())
    fields = {"tesseract": tess, "backend": eng.kind, "model": None, "model_revision": None}
    cacheable = bool(tess["version"])
    if name == "tesseract":
        return Runtime(name, fields, cacheable, {"lang": lang} if lang else {})
    if name == "kraken":
        model = _file_id(engines._kraken_model(kraken_model))
        binary = engines._which("kraken")
        fields.update(model_files=[model], executable=_file_id(binary), backend_version=_package_version(binary, "kraken"))
        return Runtime(name, fields, cacheable and model is not None,
                       {"kraken_model": kraken_model} if kraken_model else {})
    if eng.kind == "api":
        cfg = engines.api_config()
        fields.update(backend="api", base_url=cfg["base_url"] or None, model=cfg["model"] or None)
        return Runtime(name, fields, bool(cfg["base_url"] and cfg["model"]), {})
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
