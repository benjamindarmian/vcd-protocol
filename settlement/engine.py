"""Machine à états du contrat (spec §6) : chaque transition vérifie ses conditions, calcule les flux et inscrit un événement."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from oracle.receipt import verify_receipt
from oracle.util import load_products, now_iso

from . import ledger
from .money import (collateral, fees, notional, parse_ts, settlement_flows_cancelled, settlement_flows_seller_default,
                    settlement_flows_settled)

TERMINAL = {"SETTLED", "DEFAULTED_SELLER", "CANCELLED_BUYER"}


class SettlementError(Exception):
    pass


def _now(at: str | None) -> datetime:
    return parse_ts(at) if at else datetime.now(timezone.utc)


def _require(state: dict, *allowed: str) -> None:
    if state["status"] not in allowed:
        raise SettlementError(f"transition interdite depuis l'état {state['status']} (attendu : {', '.join(allowed)})")


def _record(state: dict, kind: str, data: dict, at: datetime | None = None) -> dict:
    ev = ledger.append_event(state["contract"]["contract_id"], kind, data, at.isoformat() if at else None)
    state["history"].append({"seq": ev["seq"], "ts": ev["ts"], "kind": kind, "hash": ev["hash"]})
    state["updated_at"] = now_iso()
    ledger.save_contract(state)
    return ev


def validate_contract(contract: dict) -> list[str]:
    errors: list[str] = []
    try:
        import jsonschema
        from pathlib import Path
        schema = json.load(open(Path(__file__).resolve().parent.parent / "spec" / "schemas" / "contract.schema.json", encoding="utf-8"))
        v = jsonschema.Draft202012Validator(schema)
        errors += [f"schéma : {e.message}" for e in v.iter_errors(contract)]
    except ImportError:
        pass
    products = load_products()
    if contract.get("product") not in products["products"]:
        errors.append(f"produit inconnu : {contract.get('product')}")
    try:
        if parse_ts(contract["window"]["end"]) <= parse_ts(contract["window"]["start"]) + timedelta(hours=1):
            errors.append("fenêtre : durée minimale 1 heure")
    except Exception:
        errors.append("fenêtre : dates illisibles")
    if contract.get("transferable"):
        errors.append("transferable doit être false en v0.1")
    if "notional" in contract and errors == []:
        n = notional(contract)
        if abs(contract["notional"] - n) > max(0.01, 0.01 * n):
            errors.append(f"notionnel déclaré {contract['notional']} ≠ calculé {n}")
    return errors


def create(contract: dict) -> dict:
    errors = validate_contract(contract)
    if errors:
        raise SettlementError("contrat invalide : " + " ; ".join(errors))
    cid = contract["contract_id"]
    try:
        ledger.load_contract(cid)
        raise SettlementError(f"le contrat {cid} existe déjà dans le journal")
    except FileNotFoundError:
        pass
    contract = dict(contract)
    contract["notional"] = notional(contract)
    state = {
        "contract": contract,
        "status": "CREATED",
        "created_at": now_iso(),
        "amounts": {"notional": contract["notional"], "collateral": collateral(contract), "fees": fees(contract)},
        "funding": None, "nonce": None, "delivery": None, "receipt": None, "flows": None, "history": [],
    }
    ledger.save_contract(state)
    _record(state, "created", {"product": contract["product"], "min_class": contract["min_class"], "quantity": contract["quantity"],
                               "window": contract["window"], "price_per_gpu_hour": contract["price_per_gpu_hour"],
                               "currency": contract["currency"], "notional": contract["notional"], "collateral": state["amounts"]["collateral"],
                               "seller": contract["seller"]["id"], "buyer": contract["buyer"]["id"], "oracle_key_id": contract["oracle"]["key_id"]})
    return state


def fund(cid: str, buyer_ref: str, seller_ref: str, at: str | None = None) -> dict:
    state = ledger.load_contract(cid)
    _require(state, "CREATED")
    state["funding"] = {"buyer_payment_ref": buyer_ref, "buyer_amount": state["amounts"]["notional"],
                        "seller_collateral_ref": seller_ref, "seller_amount": state["amounts"]["collateral"], "at": _now(at).isoformat(),
                        "note": "v0 : séquestre enregistré, non opéré"}
    state["status"] = "FUNDED"
    _record(state, "funded", state["funding"], _now(at))
    return state


def issue_nonce(cid: str) -> dict:
    state = ledger.load_contract(cid)
    _require(state, "FUNDED", "DELIVERY_PENDING", "PROVISIONED")
    state["nonce"] = secrets.token_hex(16)
    _record(state, "nonce_issued", {"nonce": state["nonce"]})
    return state


def start(cid: str, at: str | None = None, force: bool = False) -> dict:
    state = ledger.load_contract(cid)
    _require(state, "FUNDED")
    t = _now(at)
    ws = parse_ts(state["contract"]["window"]["start"])
    if t < ws and not force:
        raise SettlementError(f"la fenêtre ouvre à {ws.isoformat()} ; il est {t.isoformat()}")
    state["status"] = "DELIVERY_PENDING"
    _record(state, "delivery_pending", {"window_start": ws.isoformat()}, t)
    return state


def provision(cid: str, access: str, at: str | None = None) -> dict:
    """Le vendeur a ouvert l'accès. Dans le délai : PROVISIONED. Au-delà du double du délai : défaut du vendeur (§6.3)."""
    state = ledger.load_contract(cid)
    _require(state, "DELIVERY_PENDING")
    c = state["contract"]
    t = _now(at)
    ws = parse_ts(c["window"]["start"])
    deadline = ws + timedelta(minutes=c["delivery_deadline_minutes"])
    late_min = max(0.0, (t - deadline).total_seconds() / 60)
    if t > ws + timedelta(minutes=2 * c["delivery_deadline_minutes"]):
        state["delivery"] = {"access": access, "access_ts": t.isoformat(), "late_minutes": round(late_min, 1), "status": "too_late"}
        state["flows"] = settlement_flows_seller_default(c, None, delivered_minutes_late=0.0)
        state["flows"]["reason"] = f"accès ouvert {round(late_min)} min après le délai (plus du double du délai de {c['delivery_deadline_minutes']} min)"
        state["status"] = "DEFAULTED_SELLER"
        _record(state, "defaulted_seller", {"delivery": state["delivery"], "flows": state["flows"]}, t)
        return state
    state["delivery"] = {"access": access, "access_ts": t.isoformat(), "late_minutes": round(late_min, 1), "status": "on_time" if late_min == 0 else "late"}
    state["status"] = "PROVISIONED"
    _record(state, "provisioned", state["delivery"], t)
    return state


def _check_receipt_against_contract(state: dict, receipt: dict) -> list[str]:
    c = state["contract"]
    errors: list[str] = []
    ok, msg = verify_receipt(receipt, expected_public_key_b64=c["oracle"]["public_key"])
    if not ok:
        errors.append(f"reçu : {msg}")
    if receipt.get("contract_id") != c["contract_id"]:
        errors.append(f"reçu : contract_id {receipt.get('contract_id')} ≠ {c['contract_id']}")
    if state.get("nonce") and receipt.get("nonce") != state["nonce"]:
        errors.append("reçu : nonce différent de celui émis pour ce contrat (rejeu ou lot étranger)")
    if receipt.get("product") != c["product"]:
        errors.append(f"reçu : produit {receipt.get('product')} ≠ {c['product']}")
    if receipt.get("min_class") != c["min_class"]:
        errors.append(f"reçu : classe minimale {receipt.get('min_class')} ≠ {c['min_class']}")
    if receipt.get("timing", {}).get("mode") != "full":
        errors.append("reçu : mesure en mode rapide, non opposable pour un contrat (spec §7)")
    if receipt.get("spec_version", "").split(".")[:2] != c["spec_version"].split(".")[:2]:
        errors.append(f"reçu : version de spec {receipt.get('spec_version')} ≠ {c['spec_version']}")
    return errors


def verify(cid: str, receipt: dict, reference_price: float | None = None, at: str | None = None) -> dict:
    """Le reçu de l'oracle décide : conforme → ACCEPTED ; non conforme → REJECTED puis DEFAULTED_SELLER (§6.1)."""
    state = ledger.load_contract(cid)
    _require(state, "PROVISIONED")
    t = _now(at)
    errors = _check_receipt_against_contract(state, receipt)
    if errors:
        raise SettlementError("reçu refusé : " + " ; ".join(errors))
    v = receipt["verdict"]
    state["receipt"] = {"receipt_id": receipt["receipt_id"], "hash": receipt["hash"], "oracle_key_id": receipt["oracle"]["key_id"],
                        "class": v["class"], "conforming": v["conforming"], "reasons": v.get("reasons", []), "measure_end": receipt["timing"].get("measure_end")}
    if v["conforming"]:
        state["status"] = "ACCEPTED"
        _record(state, "accepted", state["receipt"], t)
        return state
    state["status"] = "REJECTED"
    _record(state, "rejected", state["receipt"], t)
    state["flows"] = settlement_flows_seller_default(state["contract"], reference_price, delivered_minutes_late=0.0)
    state["flows"]["reason"] = "livraison non conforme : " + " ; ".join(v.get("reasons", []))
    state["status"] = "DEFAULTED_SELLER"
    _record(state, "defaulted_seller", {"flows": state["flows"]}, t)
    return state


def settle(cid: str, at: str | None = None, force: bool = False) -> dict:
    state = ledger.load_contract(cid)
    _require(state, "ACCEPTED")
    t = _now(at)
    c = state["contract"]
    we = parse_ts(c["window"]["end"])
    if t < we and not force:
        raise SettlementError(f"la fenêtre se termine à {we.isoformat()} ; règlement possible ensuite")
    flows = settlement_flows_settled(c)
    late = (state.get("delivery") or {}).get("late_minutes") or 0.0
    if late:
        from .money import gpus_per_unit
        penalty = round(min(state["amounts"]["collateral"], c["price_per_gpu_hour"] * gpus_per_unit(c["product"]) * c["quantity"] * late / 60), 2)
        flows["late_penalty_to_buyer"] = penalty
        flows["collateral_returned_to_seller"] = round(flows["collateral_returned_to_seller"] - penalty, 2)
        flows["buyer_indemnity"] = penalty
    state["flows"] = flows
    state["status"] = "SETTLED"
    _record(state, "settled", flows, t)
    return state


def cancel(cid: str, at: str | None = None) -> dict:
    state = ledger.load_contract(cid)
    _require(state, "FUNDED", "DELIVERY_PENDING")
    t = _now(at)
    state["flows"] = settlement_flows_cancelled(state["contract"], t)
    state["status"] = "CANCELLED_BUYER"
    _record(state, "cancelled_buyer", state["flows"], t)
    return state
