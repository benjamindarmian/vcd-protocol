"""Petits utilitaires partagés par la suite."""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_PATH = ROOT / "spec" / "products.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_products(path: Path | None = None) -> dict:
    with open(path or PRODUCTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def canonical_json(obj) -> bytes:
    """JSON canonique : clés triées, pas d'espaces, UTF-8, flottants tels quels."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def try_torch():
    """Retourne le module torch si disponible et si CUDA l'est, sinon None."""
    try:
        import torch  # noqa: F401
    except Exception:
        return None
    return torch


def cuda_available() -> bool:
    torch = try_torch()
    try:
        return bool(torch and torch.cuda.is_available())
    except Exception:
        return False


def run_cmd(args: list[str], timeout: float = 20) -> str | None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return None
        return out.stdout
    except Exception:
        return None


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self.t0
