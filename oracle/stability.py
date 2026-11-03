"""Stabilité sous charge : tous les GPU en matmul simultané pendant N secondes, avec échantillonnage NVML
(fréquences, température, puissance, raisons de bridage). Dégradation = débit de la dernière tranche vs la première."""
from __future__ import annotations

import threading
import time

from .util import try_torch

# Masques NVML des raisons de bridage (nvmlClocksThrottleReasons / ClocksEventReasons)
THROTTLE_MASKS = {
    "sw_power_cap": 0x4,
    "hw_slowdown": 0x8,
    "sw_thermal": 0x20,
    "hw_thermal": 0x40,
    "hw_power_brake": 0x80,
}
BAD_MASK = sum(THROTTLE_MASKS.values())


def _load_worker(torch, index: int, n: int, stop: threading.Event, log: list, lock: threading.Lock):
    dev = torch.device(f"cuda:{index}")
    try:
        with torch.cuda.device(dev):
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            size = n
            while True:
                try:
                    a = torch.randn(size, size, device=dev, dtype=dtype)
                    b = torch.randn(size, size, device=dev, dtype=dtype)
                    c = torch.empty(size, size, device=dev, dtype=dtype)
                    break
                except RuntimeError:
                    torch.cuda.empty_cache()
                    size //= 2
                    if size < 1024:
                        raise
            flop_per_iter = 2.0 * size ** 3
            t_prev = time.perf_counter()
            while not stop.is_set():
                for _ in range(10):
                    torch.matmul(a, b, out=c)
                torch.cuda.synchronize(dev)
                t = time.perf_counter()
                with lock:
                    log.append((index, t_prev, t, 10 * flop_per_iter))
                t_prev = t
    except Exception as e:
        with lock:
            log.append((index, time.perf_counter(), time.perf_counter(), 0.0, f"error:{e}"[:200]))


def _sample_nvml(handles, pynvml) -> list[dict]:
    rows = []
    for i, h in enumerate(handles):
        row = {"device": i, "t": time.perf_counter()}
        try:
            row["sm_mhz"] = pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_SM)
            row["mem_mhz"] = pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_MEM)
        except Exception:
            pass
        try:
            row["temp_c"] = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            pass
        try:
            row["power_w"] = round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000, 1)
        except Exception:
            pass
        try:
            fn = getattr(pynvml, "nvmlDeviceGetCurrentClocksEventReasons", None) or getattr(pynvml, "nvmlDeviceGetCurrentClocksThrottleReasons")
            row["throttle_mask"] = int(fn(h))
        except Exception:
            pass
        try:
            row["util_pct"] = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
        except Exception:
            pass
        rows.append(row)
    return rows


def measure_stability(seconds: float = 300.0, sample_every: float = 2.0, n: int = 8192) -> dict:
    torch = try_torch()
    if torch is None or not torch.cuda.is_available():
        return {"status": "n/a", "reason": "CUDA indisponible"}
    count = torch.cuda.device_count()
    handles, pynvml = [], None
    try:
        import pynvml as _p
        _p.nvmlInit()
        pynvml = _p
        handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
    except Exception:
        pynvml = None
    stop = threading.Event()
    log: list = []
    lock = threading.Lock()
    threads = [threading.Thread(target=_load_worker, args=(torch, i, n, stop, log, lock), daemon=True) for i in range(count)]
    samples: list[dict] = []
    t_start = time.perf_counter()
    for th in threads:
        th.start()
    try:
        while time.perf_counter() - t_start < seconds:
            if pynvml:
                try:
                    samples.extend(_sample_nvml(handles, pynvml))
                except Exception:
                    pass
            time.sleep(sample_every)
    finally:
        stop.set()
        for th in threads:
            th.join(timeout=60)
        if pynvml:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    t_end = time.perf_counter()
    total = t_end - t_start
    slice_len = min(60.0, total / 4) if total > 0 else 1.0
    per_device = []
    for i in range(count):
        rows = [r for r in log if r[0] == i and len(r) == 4]
        err = [r[4] for r in log if r[0] == i and len(r) == 5]
        def tflops_between(a, b):
            fl = sum(r[3] for r in rows if r[1] >= a and r[2] <= b)
            return fl / (b - a) / 1e12 if b > a else 0.0
        first = tflops_between(t_start + 5, t_start + 5 + slice_len)  # on ignore les 5 premières secondes (montée en charge)
        last = tflops_between(t_end - slice_len, t_end)
        deg = max(0.0, (first - last) / first * 100) if first > 0 else None
        s = [r for r in samples if r["device"] == i]
        thr = [r.get("throttle_mask", 0) & BAD_MASK for r in s if "throttle_mask" in r]
        d = {
            "device": i,
            "tflops_first_slice": round(first, 1),
            "tflops_last_slice": round(last, 1),
            "degradation_pct": round(deg, 1) if deg is not None else None,
            "samples": len(s),
            "max_temp_c": max((r["temp_c"] for r in s if "temp_c" in r), default=None),
            "min_sm_mhz": min((r["sm_mhz"] for r in s if "sm_mhz" in r), default=None),
            "max_power_w": max((r["power_w"] for r in s if "power_w" in r), default=None),
            "throttle_share": round(sum(1 for m in thr if m) / len(thr), 3) if thr else None,
            "throttle_reasons": sorted({name for m in thr for name, bit in THROTTLE_MASKS.items() if m & bit}),
        }
        if err:
            d["error"] = err[0]
        per_device.append(d)
    degs = [d["degradation_pct"] for d in per_device if d["degradation_pct"] is not None]
    shares = [d["throttle_share"] for d in per_device if d["throttle_share"] is not None]
    return {
        "status": "ok" if degs else "error",
        "seconds": round(total, 1),
        "slice_seconds": round(slice_len, 1),
        "per_device": per_device,
        "max_degradation_pct": max(degs) if degs else None,
        "max_throttle_share": max(shares) if shares else None,
        "nvml": pynvml is not None,
    }
