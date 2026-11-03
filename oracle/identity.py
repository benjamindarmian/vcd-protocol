"""Identité de la machine : GPU (NVML, puis nvidia-smi en secours), hôte, virtualisation."""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket

from .util import read_text, run_cmd, try_torch


def _s(v):
    """NVML renvoie parfois des bytes, parfois des str selon la version."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _gpus_nvml() -> dict | None:
    try:
        import pynvml
    except Exception:
        return None
    try:
        pynvml.nvmlInit()
    except Exception:
        return None
    out = {"source": "nvml", "driver_version": None, "gpus": [], "nvlink_links_active": 0}
    try:
        out["driver_version"] = _s(pynvml.nvmlSystemGetDriverVersion())
    except Exception:
        pass
    try:
        n = pynvml.nvmlDeviceGetCount()
    except Exception:
        n = 0
    total_links = 0
    for i in range(n):
        g = {"index": i}
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            g["name"] = _s(pynvml.nvmlDeviceGetName(h))
            g["memory_gb"] = round(pynvml.nvmlDeviceGetMemoryInfo(h).total / 2**30, 1)
            try:
                g["uuid"] = _s(pynvml.nvmlDeviceGetUUID(h))
            except Exception:
                pass
            try:
                g["pcie_gen_max"] = pynvml.nvmlDeviceGetMaxPcieLinkGeneration(h)
                g["pcie_width_max"] = pynvml.nvmlDeviceGetMaxPcieLinkWidth(h)
            except Exception:
                pass
            try:
                g["power_limit_w"] = round(pynvml.nvmlDeviceGetPowerManagementLimit(h) / 1000)
            except Exception:
                pass
            try:
                cur, pend = pynvml.nvmlDeviceGetEccMode(h)
                g["ecc_enabled"] = bool(cur)
            except Exception:
                pass
            try:
                cur, pend = pynvml.nvmlDeviceGetMigMode(h)
                g["mig_enabled"] = bool(cur)
            except Exception:
                pass
            links = 0
            for link in range(18):
                try:
                    if pynvml.nvmlDeviceGetNvLinkState(h, link):
                        links += 1
                except Exception:
                    break
            g["nvlink_links_active"] = links
            total_links += links
        except Exception as e:  # pragma: no cover
            g["error"] = str(e)
        out["gpus"].append(g)
    out["nvlink_links_active"] = total_links
    try:
        pynvml.nvmlShutdown()
    except Exception:
        pass
    return out


def _gpus_smi() -> dict | None:
    q = "name,memory.total,driver_version,pci.link.gen.max,pci.link.width.max,uuid,power.limit"
    txt = run_cmd(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"])
    if not txt:
        return None
    out = {"source": "nvidia-smi", "driver_version": None, "gpus": [], "nvlink_links_active": None}
    for i, line in enumerate([l for l in txt.splitlines() if l.strip()]):
        parts = [p.strip() for p in line.split(",")]
        g = {"index": i, "name": parts[0] if parts else None}
        try:
            g["memory_gb"] = round(float(parts[1]) / 1024, 1)
        except Exception:
            pass
        if len(parts) > 2:
            out["driver_version"] = parts[2]
        for k, idx in (("pcie_gen_max", 3), ("pcie_width_max", 4)):
            try:
                g[k] = int(parts[idx])
            except Exception:
                pass
        if len(parts) > 5:
            g["uuid"] = parts[5]
        try:
            g["power_limit_w"] = round(float(parts[6]))
        except Exception:
            pass
        out["gpus"].append(g)
    # Liens NVLink via nvidia-smi nvlink -s (best effort)
    nv = run_cmd(["nvidia-smi", "nvlink", "-s"])
    if nv:
        active = len(re.findall(r"Link \d+: \d+(?:\.\d+)? GB/s", nv))
        out["nvlink_links_active"] = active
    return out


def detect_family(name: str | None, aliases: dict[str, list[str]]) -> str | None:
    if not name:
        return None
    up = name.upper()
    # Les familles les plus spécifiques d'abord (H200 avant H100 : "H200" ne contient pas "H100", mais par sûreté)
    for fam in sorted(aliases, key=lambda f: -len(f)):
        for a in aliases[fam]:
            if a.upper() in up:
                return fam
    return None


def detect_form(name: str | None, nvlink_links: int | None, family: str | None) -> str | None:
    if not name:
        return None
    up = name.upper()
    if "PCIE" in up or "PCI-E" in up:
        return "PCIE"
    if "SXM" in up or "HBM3" in up or "NVL" in up:
        return "SXM"
    if family in ("RTX4090", "T4", "P100"):
        return "PCIE"
    if nvlink_links and nvlink_links > 0:
        return "SXM"
    if family in ("H100", "H200", "B200", "A100"):
        # Le nom nu "NVIDIA H100 80GB" est le plus souvent la version PCIe ; "HBM3" désigne la SXM.
        return "PCIE" if family == "H100" else "SXM"
    return None


def _virtualization() -> str:
    v = run_cmd(["systemd-detect-virt"], timeout=5)
    if v and v.strip():
        return v.strip()
    if os.path.exists("/.dockerenv"):
        return "docker"
    cg = read_text("/proc/1/cgroup") or ""
    if "docker" in cg or "kubepods" in cg or "containerd" in cg:
        return "container"
    prod = read_text("/sys/class/dmi/id/product_name")
    if prod:
        p = prod.strip()
        for k in ("KVM", "QEMU", "VMware", "VirtualBox", "Xen", "HVM", "Google Compute", "Virtual Machine"):
            if k.lower() in p.lower():
                return f"vm:{p}"
        return f"bare-metal?:{p}"
    return "unknown"


def _host() -> dict:
    cpu_model = None
    cpuinfo = read_text("/proc/cpuinfo") or ""
    m = re.search(r"model name\s*:\s*(.+)", cpuinfo)
    if m:
        cpu_model = m.group(1).strip()
    ram_gb = None
    mem = read_text("/proc/meminfo") or ""
    m = re.search(r"MemTotal:\s*(\d+) kB", mem)
    if m:
        ram_gb = round(int(m.group(1)) / 2**20, 1)
    try:
        du = shutil.disk_usage(os.getcwd())
        disk = {"total_gb": round(du.total / 2**30, 1), "free_gb": round(du.free / 2**30, 1), "path": os.getcwd()}
    except Exception:
        disk = {}
    return {
        "hostname": socket.gethostname(),
        "cpu_model": cpu_model,
        "cpu_count": os.cpu_count(),
        "ram_gb": ram_gb,
        "disk": disk,
        "kernel": platform.release(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "virtualization": _virtualization(),
    }


def collect_identity(products: dict) -> dict:
    gpu = _gpus_nvml() or _gpus_smi() or {"source": "none", "driver_version": None, "gpus": [], "nvlink_links_active": None}
    aliases = products.get("family_aliases", {})
    names = [g.get("name") for g in gpu["gpus"]]
    fams = {detect_family(n, aliases) for n in names} - {None}
    family = fams.pop() if len(fams) == 1 else (None if not fams else "MIXED")
    form = detect_form(names[0], gpu.get("nvlink_links_active"), family) if names else None
    cuda = None
    torch = try_torch()
    if torch is not None:
        try:
            cuda = torch.version.cuda
            if gpu["source"] == "none" and torch.cuda.is_available():
                gpu = {"source": "torch", "driver_version": None, "nvlink_links_active": None, "gpus": [
                    {"index": i, "name": torch.cuda.get_device_name(i),
                     "memory_gb": round(torch.cuda.get_device_properties(i).total_memory / 2**30, 1)}
                    for i in range(torch.cuda.device_count())]}
                names = [g["name"] for g in gpu["gpus"]]
                fams = {detect_family(n, aliases) for n in names} - {None}
                family = fams.pop() if len(fams) == 1 else (None if not fams else "MIXED")
                form = detect_form(names[0], None, family) if names else None
        except Exception:
            pass
    return {
        "source": gpu["source"],
        "gpu_count": len(gpu["gpus"]),
        "family": family,
        "form": form,
        "nvlink_links_active": gpu.get("nvlink_links_active"),
        "driver_version": gpu.get("driver_version"),
        "cuda_version": cuda,
        "gpus": gpu["gpus"],
        "host": _host(),
    }


def guess_product(identity: dict, products: dict) -> str | None:
    """Choisit le produit qui correspond à l'identité détectée (pour --detect)."""
    fam, form, n = identity.get("family"), identity.get("form"), identity.get("gpu_count")
    for key, p in products["products"].items():
        ident = p["identity"]
        if ident["family"] == fam and ident["gpus"] == n and (form is None or ident["form"] == form):
            return key
    # second passage sans la forme
    for key, p in products["products"].items():
        ident = p["identity"]
        if ident["family"] == fam and ident["gpus"] == n:
            return key
    return None
