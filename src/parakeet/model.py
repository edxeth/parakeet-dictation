"""Model readiness helpers for Parakeet."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Mapping


MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"
_MODEL_CACHE_DIRNAME = "models--nvidia--parakeet-tdt-0.6b-v3"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _candidate_cache_roots(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> list[Path]:
    source = os.environ if env is None else env
    home_dir = Path.home() if home is None else home
    roots: list[Path] = []

    hf_hub_cache = source.get("HF_HUB_CACHE") or source.get("HUGGINGFACE_HUB_CACHE")
    if hf_hub_cache:
        roots.append(Path(hf_hub_cache).expanduser())

    hf_home = source.get("HF_HOME")
    if hf_home:
        roots.append(Path(hf_home).expanduser() / "hub")

    xdg_cache_home = source.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        roots.append(Path(xdg_cache_home).expanduser() / "huggingface" / "hub")

    roots.append(home_dir.expanduser() / ".cache" / "huggingface" / "hub")
    return _dedupe_paths(roots)


def _find_model_cache_dir(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path | None:
    for root in _candidate_cache_roots(env, home=home):
        candidate = root / _MODEL_CACHE_DIRNAME
        if candidate.exists():
            return candidate
    return None


def _has_snapshot(cache_dir: Path) -> bool:
    snapshots_dir = cache_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return False
    try:
        return any(child.is_dir() for child in snapshots_dir.iterdir())
    except OSError:
        return False


def _check_import_readiness() -> tuple[bool, str | None]:
    try:
        importlib.import_module("torch")
        importlib.import_module("nemo.collections.asr")
    except Exception as exc:  # pragma: no cover - exercised in tests via monkeypatch
        return False, str(exc)
    return True, None


def check_model_cache(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> dict[str, Any]:
    cache_dir = _find_model_cache_dir(env, home=home)
    cache_present = bool(cache_dir and _has_snapshot(cache_dir))
    import_ready, import_error = _check_import_readiness()

    if cache_present and import_ready:
        detail = "Local cache and model imports look ready"
    elif not cache_present and import_ready:
        detail = "Local Parakeet cache snapshot is missing"
    elif cache_present and not import_ready:
        detail = "Local cache exists, but model imports failed"
    else:
        detail = "Local cache is missing and model imports failed"

    return {
        "checked": True,
        "cache_present": cache_present,
        "cache_path": str(cache_dir) if cache_dir is not None else None,
        "model_id": MODEL_ID,
        "import_ready": import_ready,
        "import_error": import_error,
        "detail": detail,
    }
