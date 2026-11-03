"""Stockage local : écriture puis lecture séquentielles d'un fichier temporaire, en MB/s."""
from __future__ import annotations

import os
import tempfile
import time

CHUNK = 8 << 20  # 8 MiB


def measure_storage(path: str | None = None, quick: bool = False) -> dict:
    total = (256 << 20) if quick else (1 << 30)
    directory = path or os.environ.get("VCD_STORAGE_DIR") or os.getcwd()
    try:
        fd, fname = tempfile.mkstemp(prefix=".vcd_storage_", dir=directory)
    except Exception as e:
        return {"status": "n/a", "reason": f"répertoire non inscriptible : {e}", "path": directory}
    buf = os.urandom(CHUNK)
    out = {"status": "ok", "path": directory, "bytes": total}
    try:
        t0 = time.perf_counter()
        written = 0
        while written < total:
            written += os.write(fd, buf)
        os.fsync(fd)
        t_w = time.perf_counter() - t0
        out["write_mbs"] = round(written / t_w / 1e6, 1)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            out["read_cache_dropped"] = True
        except Exception:
            out["read_cache_dropped"] = False
        os.close(fd)
        fd = None
        t0 = time.perf_counter()
        read = 0
        with open(fname, "rb", buffering=0) as f:
            while True:
                b = f.read(CHUNK)
                if not b:
                    break
                read += len(b)
        t_r = time.perf_counter() - t0
        out["read_mbs"] = round(read / t_r / 1e6, 1)
    except Exception as e:
        out["status"] = "error"
        out["reason"] = str(e)[:200]
    finally:
        try:
            if fd is not None:
                os.close(fd)
        except Exception:
            pass
        try:
            os.remove(fname)
        except Exception:
            pass
    return out
