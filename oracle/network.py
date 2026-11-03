"""Réseau : débit descendant et montant, latence, contre un point de mesure public (Cloudflare par défaut)."""
from __future__ import annotations

import os
import statistics
import time
import urllib.request

DOWN_URL = os.environ.get("VCD_NET_DOWN_URL", "https://speed.cloudflare.com/__down?bytes={bytes}")
UP_URL = os.environ.get("VCD_NET_UP_URL", "https://speed.cloudflare.com/__up")
PING_URL = os.environ.get("VCD_NET_PING_URL", "https://speed.cloudflare.com/__down?bytes=0")
UA = "vcd-oracle/0.1"


def _download(nbytes: int, timeout: float) -> float:
    url = DOWN_URL.format(bytes=nbytes)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.perf_counter()
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
    t = time.perf_counter() - t0
    return got * 8 / t / 1e6  # Mbit/s


def _upload(nbytes: int, timeout: float) -> float:
    data = os.urandom(1 << 20) * (nbytes // (1 << 20))
    req = urllib.request.Request(UP_URL, data=data, method="POST", headers={"User-Agent": UA, "Content-Type": "application/octet-stream"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()
    t = time.perf_counter() - t0
    return len(data) * 8 / t / 1e6


def _latency(n: int = 5, timeout: float = 10) -> float:
    samples = []
    for _ in range(n):
        req = urllib.request.Request(PING_URL, headers={"User-Agent": UA})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def measure_network(quick: bool = False, timeout: float = 60) -> dict:
    down_bytes = (25 << 20) if quick else (100 << 20)
    up_bytes = (5 << 20) if quick else (25 << 20)
    out = {"status": "ok", "endpoint": DOWN_URL.split("?")[0]}
    try:
        out["latency_ms"] = round(_latency(timeout=timeout / 4), 1)
    except Exception as e:
        out["latency_ms"] = None
        out["latency_error"] = str(e)[:160]
    try:
        out["down_mbps"] = round(_download(down_bytes, timeout), 1)
    except Exception as e:
        out["down_mbps"] = None
        out["down_error"] = str(e)[:160]
    try:
        out["up_mbps"] = round(_upload(up_bytes, timeout), 1)
    except Exception as e:
        out["up_mbps"] = None
        out["up_error"] = str(e)[:160]
    if out["down_mbps"] is None:
        out["status"] = "n/a"
        out["reason"] = "point de mesure injoignable depuis cette machine (cause externe possible : pare-feu, proxy)"
    return out
