"""Calcul soutenu : multiplications de matrices BF16 (accumulation FP32), TFLOPS par GPU."""
from __future__ import annotations

import time

from .util import try_torch


def _matmul_loop(torch, dev, n: int, seconds: float, dtype) -> dict:
    """Mesure le débit en régime établi : une phase de montée en charge (boost, horloges) est exécutée puis
    écartée, et seules les itérations de la fenêtre de mesure comptent. Sur un T4 à son plafond de puissance,
    5 s compteraient le boost (22 TFLOPS) alors que le régime établi est à 19,5."""
    a = torch.randn(n, n, device=dev, dtype=dtype)
    b = torch.randn(n, n, device=dev, dtype=dtype)
    c = torch.empty(n, n, device=dev, dtype=dtype)
    warm = max(3.0, seconds / 3)
    flop = 2.0 * n * n * n

    def run_for(duration: float):
        iters = 0
        t0 = time.perf_counter()
        elapsed = 0.0
        while elapsed < duration:
            for _ in range(10):
                torch.matmul(a, b, out=c)
            iters += 10
            torch.cuda.synchronize(dev)
            elapsed = time.perf_counter() - t0
        return iters, elapsed

    for _ in range(5):
        torch.matmul(a, b, out=c)
    torch.cuda.synchronize(dev)
    w_iters, w_elapsed = run_for(warm)
    iters, elapsed = run_for(seconds)
    a = b = c = None  # noqa: F841 - libère la mémoire GPU avant la mesure suivante
    torch.cuda.empty_cache()
    return {"tflops": round(flop * iters / elapsed / 1e12, 1), "tflops_warmup": round(flop * w_iters / w_elapsed / 1e12, 1),
            "matrix": n, "seconds": round(elapsed, 2), "warmup_seconds": round(w_elapsed, 2), "iters": iters}


def matmul_dtype(torch, index: int):
    """BF16 seulement si le GPU le calcule nativement (Ampere et plus, capacité >= 8.0) ;
    sinon FP16 : sur Turing/Volta/Pascal, torch émule le BF16 et divise le débit par dix."""
    try:
        major, _ = torch.cuda.get_device_capability(index)
    except Exception:
        major = 8
    return torch.bfloat16 if major >= 8 else torch.float16


def measure_compute_device(index: int, seconds: float = 15.0) -> dict:
    torch = try_torch()
    if torch is None or not torch.cuda.is_available():
        return {"device": index, "status": "n/a", "reason": "CUDA indisponible"}
    dev = torch.device(f"cuda:{index}")
    dtype = matmul_dtype(torch, index)
    for n in (8192, 4096, 2048):
        try:
            with torch.cuda.device(dev):
                r = _matmul_loop(torch, dev, n, seconds, dtype)
            r.update({"device": index, "status": "ok", "dtype": str(dtype).replace("torch.", "")})
            return r
        except RuntimeError as e:  # OOM ou autre
            msg = str(e)
            torch.cuda.empty_cache()
            if "out of memory" in msg.lower() and n > 2048:
                continue
            return {"device": index, "status": "error", "reason": msg[:200]}
    return {"device": index, "status": "error", "reason": "OOM même en 2048"}


def measure_compute(seconds: float = 15.0, devices: list[int] | None = None) -> dict:
    torch = try_torch()
    if torch is None or not torch.cuda.is_available():
        return {"status": "n/a", "reason": "CUDA indisponible", "per_device": []}
    devices = devices if devices is not None else list(range(torch.cuda.device_count()))
    per = [measure_compute_device(i, seconds) for i in devices]
    vals = [p["tflops"] for p in per if p.get("status") == "ok"]
    return {
        "status": "ok" if vals else "error",
        "per_device": per,
        "min_tflops": min(vals) if vals else None,
        "mean_tflops": round(sum(vals) / len(vals), 1) if vals else None,
        "max_tflops": max(vals) if vals else None,
        "seconds_per_device": seconds,
    }
