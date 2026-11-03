"""Interconnexion entre GPU d'un même noeud : all-reduce NCCL (bande passante de bus) et copie pair à pair."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time

from .util import try_torch


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _worker(rank: int, world: int, port: int, nbytes: int, iters: int, result_path: str):
    import torch
    import torch.distributed as dist

    torch.cuda.set_device(rank)
    dist.init_process_group("nccl", init_method=f"tcp://127.0.0.1:{port}", rank=rank, world_size=world)
    x = torch.ones(nbytes // 4, device="cuda", dtype=torch.float32)
    for _ in range(3):
        dist.all_reduce(x)
    torch.cuda.synchronize()
    dist.barrier()
    t0 = time.perf_counter()
    for _ in range(iters):
        dist.all_reduce(x)
    torch.cuda.synchronize()
    t = time.perf_counter() - t0
    algbw = nbytes * iters / t / 1e9
    busbw = algbw * 2 * (world - 1) / world
    if rank == 0:
        with open(result_path, "w") as f:
            json.dump({"algbw_gbs": round(algbw, 1), "busbw_gbs": round(busbw, 1), "bytes": nbytes, "iters": iters, "seconds": round(t, 3), "world": world}, f)
    dist.barrier()
    dist.destroy_process_group()


def measure_allreduce(nbytes: int = 256 << 20, iters: int = 20) -> dict:
    torch = try_torch()
    if torch is None or not torch.cuda.is_available():
        return {"status": "n/a", "reason": "CUDA indisponible"}
    world = torch.cuda.device_count()
    if world < 2:
        return {"status": "n/a", "reason": "un seul GPU : pas d'interconnexion à mesurer", "world": world}
    try:
        import torch.distributed  # noqa: F401
        import torch.multiprocessing as mp
    except Exception as e:
        return {"status": "error", "reason": f"torch.distributed indisponible : {e}"}
    if not torch.distributed.is_nccl_available():
        return {"status": "error", "reason": "NCCL indisponible dans cette installation de torch"}
    os.environ.setdefault("NCCL_DEBUG", "WARN")
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        mp.spawn(_worker, args=(world, _free_port(), nbytes, iters, path), nprocs=world, join=True)
        with open(path) as f:
            r = json.load(f)
        r["status"] = "ok"
        return r
    except Exception as e:
        return {"status": "error", "reason": str(e)[:300], "world": world}
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def measure_p2p_copy(nbytes: int = 256 << 20, iters: int = 10) -> dict:
    """Copie unidirectionnelle GPU0 -> GPU1 (cudaMemcpyPeer), en GB/s. Mesure secondaire."""
    torch = try_torch()
    if torch is None or not torch.cuda.is_available() or torch.cuda.device_count() < 2:
        return {"status": "n/a"}
    try:
        a = torch.ones(nbytes // 4, device="cuda:0", dtype=torch.float32)
        b = torch.empty(nbytes // 4, device="cuda:1", dtype=torch.float32)
        for _ in range(2):
            b.copy_(a)
        torch.cuda.synchronize(0)
        torch.cuda.synchronize(1)
        t0 = time.perf_counter()
        for _ in range(iters):
            b.copy_(a)
        torch.cuda.synchronize(0)
        torch.cuda.synchronize(1)
        t = time.perf_counter() - t0
        try:
            can = torch.cuda.can_device_access_peer(0, 1)
        except Exception:
            can = None
        del a, b
        torch.cuda.empty_cache()
        return {"status": "ok", "gbs": round(nbytes * iters / t / 1e9, 1), "peer_access": can}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:200]}


def measure_interconnect(quick: bool = False) -> dict:
    nbytes = (64 << 20) if quick else (256 << 20)
    r = measure_allreduce(nbytes=nbytes, iters=10 if quick else 20)
    out = {"allreduce": r, "p2p": measure_p2p_copy(nbytes=nbytes, iters=5 if quick else 10)}
    out["status"] = r.get("status")
    out["busbw_gbs"] = r.get("busbw_gbs")
    out["reason"] = r.get("reason")
    return out
