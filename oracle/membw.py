"""Bande passante mémoire GPU (HBM) : copie device-to-device, octets lus + écrits par seconde."""
from __future__ import annotations

import time

from .util import try_torch


def measure_hbm_device(index: int, seconds: float = 5.0) -> dict:
    torch = try_torch()
    if torch is None or not torch.cuda.is_available():
        return {"device": index, "status": "n/a", "reason": "CUDA indisponible"}
    dev = torch.device(f"cuda:{index}")
    for size in (1 << 30, 1 << 28, 1 << 26):  # 1 GiB, 256 MiB, 64 MiB
        try:
            with torch.cuda.device(dev):
                x = torch.empty(size // 4, device=dev, dtype=torch.float32)
                y = torch.empty(size // 4, device=dev, dtype=torch.float32)
                x.fill_(1.0)
                for _ in range(3):
                    y.copy_(x)
                torch.cuda.synchronize(dev)
                iters = 0
                t0 = time.perf_counter()
                elapsed = 0.0
                while elapsed < seconds:
                    for _ in range(5):
                        y.copy_(x)
                    iters += 5
                    torch.cuda.synchronize(dev)
                    elapsed = time.perf_counter() - t0
                gbs = 2.0 * size * iters / elapsed / 1e9
                del x, y
                torch.cuda.empty_cache()
            return {"device": index, "status": "ok", "gbs": round(gbs, 1), "buffer_bytes": size, "iters": iters, "seconds": round(elapsed, 2)}
        except RuntimeError as e:
            torch.cuda.empty_cache()
            if "out of memory" in str(e).lower() and size > (1 << 26):
                continue
            return {"device": index, "status": "error", "reason": str(e)[:200]}
    return {"device": index, "status": "error", "reason": "OOM"}


def measure_hbm(seconds: float = 5.0, devices: list[int] | None = None) -> dict:
    torch = try_torch()
    if torch is None or not torch.cuda.is_available():
        return {"status": "n/a", "reason": "CUDA indisponible", "per_device": []}
    devices = devices if devices is not None else list(range(torch.cuda.device_count()))
    per = [measure_hbm_device(i, seconds) for i in devices]
    vals = [p["gbs"] for p in per if p.get("status") == "ok"]
    return {
        "status": "ok" if vals else "error",
        "per_device": per,
        "min_gbs": min(vals) if vals else None,
        "mean_gbs": round(sum(vals) / len(vals), 1) if vals else None,
    }
