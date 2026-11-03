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


# ---------- contrôleur : lot brut -> vérification -> signature ----------
from oracle.sign import check_bundle  # noqa: E402


def _bundle(tmp_path):
    ident = h100_node_identity()
    m = good_measurements()
    # compteurs bruts cohérents avec les résumés
    m["compute"]["per_device"] = [{"device": i, "status": "ok", "tflops": 612.0, "matrix": 8192, "seconds": 15.0,
                                   "warmup_seconds": 5.0, "iters": round(612.0e12 * 15.0 / (2 * 8192 ** 3))} for i in range(8)]
    for d in m["compute"]["per_device"]:
        d["tflops"] = round(2 * 8192 ** 3 * d["iters"] / 15.0 / 1e12, 1)
    m["compute"]["min_tflops"] = min(d["tflops"] for d in m["compute"]["per_device"])
    m["hbm"]["per_device"] = [{"device": i, "status": "ok", "gbs": 2890.0, "buffer_bytes": 1 << 30, "seconds": 5.0,
                               "iters": round(2890.0e9 * 5.0 / (2 * (1 << 30)))} for i in range(8)]
    for d in m["hbm"]["per_device"]:
        d["gbs"] = round(2 * (1 << 30) * d["iters"] / 5.0 / 1e9, 1)
    m["hbm"]["min_gbs"] = min(d["gbs"] for d in m["hbm"]["per_device"])
    ar = {"status": "ok", "bytes": 256 << 20, "iters": 20, "seconds": 0.0224, "world": 8}
    ar["algbw_gbs"] = round(ar["bytes"] * ar["iters"] / ar["seconds"] / 1e9, 1)
    ar["busbw_gbs"] = round(ar["algbw_gbs"] * 2 * 7 / 8, 1)
    m["interconnect"] = {"status": "ok", "busbw_gbs": ar["busbw_gbs"], "allreduce": ar, "p2p": {"status": "ok", "gbs": 240.0}}
    m["stability"]["per_device"] = [{"device": i, "tflops_first_slice": 608.0, "tflops_last_slice": 594.0, "degradation_pct": 2.3} for i in range(8)]
    m["stability"]["max_degradation_pct"] = 2.3
    m["stability"]["seconds"] = 300.0
    v = compute_verdict("H100.SXM.8", PRODUCTS, ident, m, "A")
    b = build_receipt(product="H100.SXM.8", min_class="A", identity=ident, measurements=m,
                      thresholds=PRODUCTS["products"]["H100.SXM.8"], verdict=v,
                      timing={"measure_start": "2026-11-03T10:00:00+00:00", "measure_end": "2026-11-03T10:10:00+00:00", "mode": "full"},
                      sign=False, nonce="defi-123")
    assert b["signature"] is None
    return b


def test_controller_accepts_consistent_bundle(tmp_path):
    from oracle.receipt import sign_bundle
    b = _bundle(tmp_path)
    errors, warnings, verdict = check_bundle(b, "defi-123", PRODUCTS)
    assert errors == [], errors
    assert verdict["class"] == "A"
    r = sign_bundle(b, tmp_path / "controller.pem")
    assert verify_receipt(r)[0]
    assert r["oracle"]["key_id"].startswith("oracle-")


def test_controller_refuses_summary_not_backed_by_counters(tmp_path):
    b = _bundle(tmp_path)
    b["measurements"]["compute"]["per_device"][3]["tflops"] = 999.0  # résumé gonflé, compteurs inchangés
    b["measurements"]["compute"]["min_tflops"] = 999.0
    errors, _, _ = check_bundle(b, "defi-123", PRODUCTS)
    assert any("compute GPU 3" in e for e in errors)


def test_controller_refuses_wrong_nonce_and_signed_bundle(tmp_path):
    b = _bundle(tmp_path)
    errors, _, _ = check_bundle(b, "autre-defi", PRODUCTS)
    assert any("nonce" in e for e in errors)
    b2 = dict(b)
    b2["signature"] = "deadbeef"
    errors2, _, _ = check_bundle(b2, "defi-123", PRODUCTS)
    assert any("déjà signé" in e for e in errors2)


def test_controller_recomputes_verdict_with_its_own_thresholds(tmp_path):
    b = _bundle(tmp_path)
    b["verdict"]["class"] = "A"
    b["measurements"]["interconnect"]["allreduce"]["seconds"] = 0.5  # 20 x 256 MiB en 0,5 s : ~10 GB/s busbw
    ar = b["measurements"]["interconnect"]["allreduce"]
    ar["algbw_gbs"] = round(ar["bytes"] * ar["iters"] / ar["seconds"] / 1e9, 1)
    ar["busbw_gbs"] = round(ar["algbw_gbs"] * 2 * 7 / 8, 1)
    b["measurements"]["interconnect"]["busbw_gbs"] = ar["busbw_gbs"]
    errors, warnings, verdict = check_bundle(b, "defi-123", PRODUCTS)
    assert errors == []
    assert verdict["class"] == "C"
    assert any("verdict recalculé" in w for w in warnings)


def test_cli_sign_roundtrip(tmp_path):
    b = _bundle(tmp_path)
    p = tmp_path / "bundle.json"
    json.dump(b, open(p, "w"))
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    out = subprocess.run([sys.executable, "-m", "oracle.sign", str(p), "--nonce", "defi-123", "--key-file", str(tmp_path / "c.pem"),
                          "--out", str(tmp_path / "r.json")], cwd=ROOT, capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    r = json.load(open(tmp_path / "r.json"))
    assert verify_receipt(r)[0] and r["controller_checks"]["errors"] == []
    bad = subprocess.run([sys.executable, "-m", "oracle.sign", str(p), "--nonce", "mauvais", "--key-file", str(tmp_path / "c.pem")],
                         cwd=ROOT, capture_output=True, text=True, env=env, timeout=60)
    assert bad.returncode == 2
