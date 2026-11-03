"""Tests hors GPU de la suite de conformité : règle de classe, reçu signé, schémas."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle.util import load_products  # noqa: E402
from oracle.verdict import compute_verdict  # noqa: E402
from oracle.receipt import build_receipt, verify_receipt, receipt_hash  # noqa: E402
from oracle.identity import detect_family, detect_form, guess_product  # noqa: E402

PRODUCTS = load_products()


def h100_node_identity(n=8, mem=79.6, name="NVIDIA H100 80GB HBM3", nvlink=18):
    return {
        "source": "nvml", "gpu_count": n, "family": "H100", "form": "SXM", "nvlink_links_active": nvlink * n,
        "driver_version": "550.90", "cuda_version": "12.4",
        "gpus": [{"index": i, "name": name, "memory_gb": mem, "nvlink_links_active": nvlink} for i in range(n)],
        "host": {"hostname": "node-1", "cpu_model": "AMD EPYC 9654", "cpu_count": 192, "ram_gb": 2048, "virtualization": "bare-metal?:X13"},
    }


def good_measurements():
    return {
        "compute": {"status": "ok", "min_tflops": 612.0, "mean_tflops": 630.0, "per_device": []},
        "hbm": {"status": "ok", "min_gbs": 2890.0, "per_device": []},
        "interconnect": {"status": "ok", "busbw_gbs": 412.0, "allreduce": {}, "p2p": {}},
        "network": {"status": "ok", "down_mbps": 4100.0, "up_mbps": 2200.0, "latency_ms": 3.1},
        "storage": {"status": "ok", "write_mbs": 1900.0, "read_mbs": 3100.0},
        "stability": {"status": "ok", "max_degradation_pct": 2.4, "max_throttle_share": 0.0, "max_thermal_share": 0.0, "max_power_cap_share": 0.0, "per_device": []},
    }


def test_class_a_when_everything_passes():
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), good_measurements(), "A")
    assert v["class"] == "A" and v["conforming"]


def test_identity_mismatch_is_fail():
    ident = h100_node_identity(n=7)
    v = compute_verdict("H100.SXM.8", PRODUCTS, ident, good_measurements(), "C")
    assert v["class"] == "FAIL" and not v["conforming"]
    assert any("7 GPU" in r for r in v["reasons"])


def test_wrong_family_is_fail():
    ident = h100_node_identity(name="NVIDIA A100-SXM4-80GB")
    ident["family"] = "A100"
    v = compute_verdict("H100.SXM.8", PRODUCTS, ident, good_measurements(), "C")
    assert v["class"] == "FAIL"


def test_slow_interconnect_downgrades():
    m = good_measurements()
    m["interconnect"]["busbw_gbs"] = 42.0  # PCIe sans NVLink
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    assert v["class"] == "C" and not v["conforming"]
    assert any("interconnexion" in r for r in v["reasons"])


def test_primary_between_b_and_a_gives_b():
    m = good_measurements()
    m["compute"]["min_tflops"] = 480.0
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "B")
    assert v["class"] == "B" and v["conforming"]
    v2 = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    assert not v2["conforming"]


def test_secondary_caps_at_b_only():
    m = good_measurements()
    m["network"]["down_mbps"] = 90.0  # sous B, mais secondaire
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    assert v["class"] == "B"


def test_unmeasured_secondary_is_only_a_warning():
    m = good_measurements()
    m["network"] = {"status": "n/a", "reason": "bloqué"}
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    assert v["class"] == "A"
    assert any("réseau" in w for w in v["warnings"])


def test_unmeasured_primary_gives_c():
    m = good_measurements()
    m["stability"] = {"status": "error", "reason": "interrompu"}
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    assert v["class"] == "C"


def test_thermal_throttling_caps_stability_at_b():
    m = good_measurements()
    m["stability"]["max_thermal_share"] = 0.6
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    assert v["class"] == "B"
    assert any("thermique" in r for r in v["reasons"])


def test_power_cap_is_normal_and_keeps_class_a():
    m = good_measurements()
    m["stability"]["max_power_cap_share"] = 0.93  # un GPU à son TDP, comme un T4 sous charge
    m["stability"]["max_throttle_share"] = 0.93
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    assert v["class"] == "A"
    assert any("plafond de puissance" in w for w in v["warnings"])


def test_single_gpu_product_ignores_interconnect():
    ident = h100_node_identity(n=1, nvlink=0)
    m = good_measurements()
    m["interconnect"] = {"status": "n/a", "reason": "un seul GPU"}
    v = compute_verdict("H100.SXM.1", PRODUCTS, ident, m, "A")
    assert v["class"] == "A"


def test_family_and_form_detection():
    aliases = PRODUCTS["family_aliases"]
    assert detect_family("NVIDIA H100 80GB HBM3", aliases) == "H100"
    assert detect_family("NVIDIA H200", aliases) == "H200"
    assert detect_family("Tesla T4", aliases) == "T4"
    assert detect_family("NVIDIA GeForce RTX 4090", aliases) == "RTX4090"
    assert detect_form("NVIDIA H100 80GB HBM3", 18, "H100") == "SXM"
    assert detect_form("NVIDIA H100 PCIe", 0, "H100") == "PCIE"
    assert detect_form("NVIDIA A100-SXM4-80GB", 12, "A100") == "SXM"
    assert detect_form("Tesla T4", 0, "T4") == "PCIE"


def test_guess_product():
    assert guess_product(h100_node_identity(), PRODUCTS) == "H100.SXM.8"
    ident = {"family": "T4", "form": "PCIE", "gpu_count": 1}
    assert guess_product(ident, PRODUCTS) == "T4.PCIE.1"
    assert guess_product({"family": "H100", "form": "SXM", "gpu_count": 3}, PRODUCTS) is None


def test_receipt_sign_verify_and_tamper(tmp_path):
    key = tmp_path / "k.pem"
    ident = h100_node_identity()
    m = good_measurements()
    v = compute_verdict("H100.SXM.8", PRODUCTS, ident, m, "A")
    r = build_receipt(product="H100.SXM.8", min_class="A", identity=ident, measurements=m,
                      thresholds=PRODUCTS["products"]["H100.SXM.8"], verdict=v,
                      timing={"measure_start": "2026-11-03T10:00:00+00:00", "measure_end": "2026-11-03T10:10:00+00:00", "mode": "full"},
                      key_file=key)
    ok, msg = verify_receipt(r)
    assert ok, msg
    assert r["signature"] and len(r["hash"]) == 64
    assert receipt_hash(r) == r["hash"]
    # falsification : on change le verdict après signature
    bad = copy.deepcopy(r)
    bad["verdict"]["class"] = "A"
    bad["measurements"]["compute"]["min_tflops"] = 999.0
    ok2, msg2 = verify_receipt(bad)
    assert not ok2 and "modifié" in msg2
    # mauvaise clé attendue
    ok3, _ = verify_receipt(r, expected_public_key_b64="AAAA")
    assert not ok3
    # schéma
    schema = json.load(open(ROOT / "spec" / "schemas" / "receipt.schema.json"))
    jsonschema.validate(r, schema)


def test_contract_example_matches_schema():
    schema = json.load(open(ROOT / "spec" / "schemas" / "contract.schema.json"))
    c = json.load(open(ROOT / "spec" / "examples" / "contract.example.json"))
    jsonschema.validate(c, schema)
    hours = 20
    assert abs(c["notional"] - c["price_per_gpu_hour"] * 8 * c["quantity"] * hours) < 1e-6


def test_products_file_is_consistent():
    for key, p in PRODUCTS["products"].items():
        fam, form, n = key.split(".")
        assert p["identity"]["family"] == fam and p["identity"]["form"] == form and p["identity"]["gpus"] == int(n)
        th = p["thresholds"]
        for name in ("compute_tflops", "hbm_gbs", "network_down_mbps", "storage_write_mbs"):
            assert th[name]["A"] >= th[name]["B"] > 0
        assert th["stability_max_degradation_pct"]["A"] <= th["stability_max_degradation_pct"]["B"]
        if p["identity"]["nvlink_required"]:
            assert th["interconnect_gbs"] and th["interconnect_gbs"]["A"] >= th["interconnect_gbs"]["B"]


@pytest.mark.skipif(sys.platform != "linux", reason="exécution de bout en bout sous Linux seulement")
def test_cli_cpu_end_to_end(tmp_path):
    out = tmp_path / "r.json"
    env = {"VCD_ORACLE_KEY_FILE": str(tmp_path / "k.pem"), "PATH": "/usr/bin:/bin:/usr/local/bin", "VCD_STORAGE_DIR": str(tmp_path)}
    p = subprocess.run([sys.executable, "-m", "oracle.run", "--allow-cpu", "--mode", "quick", "--skip", "network",
                        "--purpose", "calibration", "--out", str(out)], cwd=ROOT, capture_output=True, text=True, env=env, timeout=240)
    assert out.exists(), p.stderr
    r = json.load(open(out))
    assert r["verdict"]["class"] == "FAIL"  # pas de GPU : identité non conforme
    assert verify_receipt(r)[0]
    assert "VERDICT" in p.stdout
