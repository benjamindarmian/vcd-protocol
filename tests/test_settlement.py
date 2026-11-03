"""Boucle de règlement : chemin heureux, défaut du vendeur, reçu non conforme, annulation, reçu étranger, chaîne du journal."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle.receipt import build_receipt, load_or_create_key  # noqa: E402
from oracle.util import load_products  # noqa: E402
from oracle.verdict import compute_verdict  # noqa: E402
from settlement import engine, ledger  # noqa: E402
from settlement.money import notional, refund_pct_on_cancellation  # noqa: E402
from settlement.money import parse_ts  # noqa: E402
from tests.test_oracle import good_measurements, h100_node_identity  # noqa: E402

PRODUCTS = load_products()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VCD_LEDGER_DIR", str(tmp_path / "ledger"))
    key = tmp_path / "oracle.pem"
    _, pub, kid = load_or_create_key(key)
    contract = json.load(open(ROOT / "spec" / "examples" / "contract.example.json"))
    contract["oracle"]["public_key"] = pub
    contract["oracle"]["key_id"] = kid
    return {"key": key, "contract": contract, "pub": pub}


def make_receipt(env, state, conforming=True, mode="full", nonce=None, key=None):
    m = good_measurements()
    if not conforming:
        m["interconnect"]["busbw_gbs"] = 40.0
        m["stability"]["seconds"] = 300.0
    v = compute_verdict("H100.SXM.8", PRODUCTS, h100_node_identity(), m, "A")
    return build_receipt(product="H100.SXM.8", min_class="A", identity=h100_node_identity(), measurements=m,
                         thresholds=PRODUCTS["products"]["H100.SXM.8"], verdict=v,
                         timing={"measure_start": "2026-11-27T08:04:45+00:00", "measure_end": "2026-11-27T08:14:51+00:00", "mode": mode},
                         contract_id=state["contract"]["contract_id"], key_file=key or env["key"],
                         nonce=nonce if nonce is not None else state.get("nonce"))


def test_happy_path_settles_with_fees(env):
    c = env["contract"]
    st = engine.create(c)
    assert st["status"] == "CREATED" and st["amounts"]["notional"] == 2688.0 and st["amounts"]["collateral"] == 537.6
    st = engine.fund(c["contract_id"], "pay_1", "col_1", at="2026-11-25T10:00:00Z")
    st = engine.issue_nonce(c["contract_id"])
    st = engine.start(c["contract_id"], at="2026-11-27T08:00:00Z")
    st = engine.provision(c["contract_id"], "ssh://buyer@10.0.0.4", at="2026-11-27T08:04:40Z")
    assert st["status"] == "PROVISIONED" and st["delivery"]["status"] == "on_time"
    r = make_receipt(env, st)
    st = engine.verify(c["contract_id"], r, at="2026-11-27T08:15:00Z")
    assert st["status"] == "ACCEPTED"
    with pytest.raises(engine.SettlementError):
        engine.settle(c["contract_id"], at="2026-11-27T20:00:00Z")  # fenêtre pas finie
    st = engine.settle(c["contract_id"], at="2026-11-28T04:00:00Z")
    f = st["flows"]
    assert st["status"] == "SETTLED"
    assert f["protocol_fee"] == 53.76 and f["oracle_fee"] == 13.44 and f["seller_receives"] == 2688.0 - 67.2
    assert f["collateral_returned_to_seller"] == 537.6
    ok, msg = ledger.verify_chain()
    assert ok, msg
    kinds = [h["kind"] for h in st["history"]]
    assert kinds == ["created", "funded", "nonce_issued", "delivery_pending", "provisioned", "accepted", "settled"]


def test_seller_too_late_defaults_with_indemnity(env):
    c = env["contract"]
    engine.create(c)
    engine.fund(c["contract_id"], "pay", "col")
    engine.start(c["contract_id"], at="2026-11-27T08:00:00Z")
    st = engine.provision(c["contract_id"], "ssh://x", at="2026-11-27T08:40:00Z")  # 40 min > 2 x 15
    assert st["status"] == "DEFAULTED_SELLER"
    assert st["flows"]["buyer_refund"] == 2688.0
    assert st["flows"]["collateral_returned_to_seller"] == 537.6  # pas de prix de référence : pas de surcoût


def test_late_but_within_grace_is_penalised_at_settlement(env):
    c = env["contract"]
    engine.create(c)
    engine.fund(c["contract_id"], "pay", "col")
    engine.start(c["contract_id"], at="2026-11-27T08:00:00Z")
    st = engine.provision(c["contract_id"], "ssh://x", at="2026-11-27T08:21:00Z")  # 6 min de retard
    assert st["status"] == "PROVISIONED" and st["delivery"]["late_minutes"] == 6.0
    st = engine.verify(c["contract_id"], make_receipt(env, st))
    st = engine.settle(c["contract_id"], at="2026-11-28T05:00:00Z")
    assert st["flows"]["late_penalty_to_buyer"] == round(2.10 * 8 * 8 * 6 / 60, 2)


def test_non_conforming_receipt_defaults_seller_with_replacement_cost(env):
    c = env["contract"]
    engine.create(c)
    engine.fund(c["contract_id"], "pay", "col")
    engine.issue_nonce(c["contract_id"])
    engine.start(c["contract_id"], at="2026-11-27T08:00:00Z")
    st = engine.provision(c["contract_id"], "ssh://x", at="2026-11-27T08:05:00Z")
    r = make_receipt(env, st, conforming=False)
    st = engine.verify(c["contract_id"], r, reference_price=2.90)
    assert st["status"] == "DEFAULTED_SELLER"
    f = st["flows"]
    # surcoût de remplacement : (2,90 - 2,10) x 64 GPU x 20 h = 1024, plafonné au collatéral 537,6
    assert f["indemnity_basis"]["replacement_cost"] == 1024.0 and f["buyer_indemnity"] == 537.6
    assert f["buyer_refund"] == 2688.0 and f["collateral_returned_to_seller"] == 0.0


def test_foreign_or_tampered_receipts_are_refused(env, tmp_path):
    c = env["contract"]
    engine.create(c)
    engine.fund(c["contract_id"], "pay", "col")
    engine.issue_nonce(c["contract_id"])
    engine.start(c["contract_id"], at="2026-11-27T08:00:00Z")
    st = engine.provision(c["contract_id"], "ssh://x", at="2026-11-27T08:05:00Z")
    # 1. signé par une autre clé que celle du contrat
    other = tmp_path / "other.pem"
    with pytest.raises(engine.SettlementError, match="clé publique"):
        engine.verify(c["contract_id"], make_receipt(env, st, key=other))
    # 2. mauvais nonce
    with pytest.raises(engine.SettlementError, match="nonce"):
        engine.verify(c["contract_id"], make_receipt(env, st, nonce="autre"))
    # 3. mode rapide
    with pytest.raises(engine.SettlementError, match="rapide"):
        engine.verify(c["contract_id"], make_receipt(env, st, mode="quick"))
    # 4. reçu modifié après signature
    r = make_receipt(env, st)
    r["verdict"]["class"] = "A"
    r["measurements"]["compute"]["min_tflops"] = 1.0
    with pytest.raises(engine.SettlementError, match="modifié"):
        engine.verify(c["contract_id"], r)
    assert ledger.load_contract(c["contract_id"])["status"] == "PROVISIONED"


def test_cancellation_grid(env):
    c = env["contract"]
    assert refund_pct_on_cancellation(c, parse_ts("2026-11-25T08:00:00Z")) == 90
    assert refund_pct_on_cancellation(c, parse_ts("2026-11-27T01:00:00Z")) == 75
    assert refund_pct_on_cancellation(c, parse_ts("2026-11-27T18:00:00Z")) == 30  # 60 % x 10 h restantes / 20 h
    engine.create(c)
    engine.fund(c["contract_id"], "pay", "col")
    st = engine.cancel(c["contract_id"], at="2026-11-25T08:00:00Z")
    assert st["status"] == "CANCELLED_BUYER" and st["flows"]["buyer_refund"] == round(2688.0 * 0.9, 2)
    assert st["flows"]["seller_receives"] == round(2688.0 * 0.1, 2)


def test_invalid_contract_is_refused(env):
    c = dict(env["contract"])
    c["notional"] = 999.0
    with pytest.raises(engine.SettlementError, match="notionnel"):
        engine.create(c)
    c = dict(env["contract"])
    c["product"] = "H100.SXM.4"
    with pytest.raises(engine.SettlementError):
        engine.create(c)
    assert notional(env["contract"]) == 2688.0


def test_ledger_detects_tampering(env):
    c = env["contract"]
    engine.create(c)
    engine.fund(c["contract_id"], "pay", "col")
    p = ledger.ledger_dir() / "events.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    ev = json.loads(lines[1])
    ev["data"]["buyer_amount"] = 1.0
    lines[1] = json.dumps(ev, ensure_ascii=False, sort_keys=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, msg = ledger.verify_chain()
    assert not ok and "altéré" in msg
