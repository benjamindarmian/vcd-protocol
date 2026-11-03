"""Règles d'argent de la spec §6 : notionnel, collatéral, frais, annulation, défaut du vendeur."""
from __future__ import annotations

from datetime import datetime, timezone

GPUS_PER_PRODUCT_CACHE: dict[str, int] = {}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def gpus_per_unit(product: str) -> int:
    """`FAMILLE.FORME.NOMBRE` : le nombre est le nombre de GPU par unité (nœud)."""
    try:
        return int(product.split(".")[-1])
    except Exception:
        return 1


def window_hours(contract: dict) -> float:
    w = contract["window"]
    return (parse_ts(w["end"]) - parse_ts(w["start"])).total_seconds() / 3600


def notional(contract: dict) -> float:
    return round(contract["price_per_gpu_hour"] * gpus_per_unit(contract["product"]) * contract["quantity"] * window_hours(contract), 2)


def collateral(contract: dict) -> float:
    return round(notional(contract) * contract["collateral_pct"] / 100, 2)


def fees(contract: dict) -> dict:
    f = contract.get("fees") or {}
    n = notional(contract)
    protocol = round(n * f.get("protocol_pct", 0) / 100, 2)
    oracle = round(n * f.get("oracle_pct", 0) / 100, 2)
    return {"protocol": protocol, "oracle": oracle, "total": round(protocol + oracle, 2)}


def refund_pct_on_cancellation(contract: dict, at: datetime) -> float:
    """Grille §6.4 : remboursement de l'acheteur selon le moment de l'annulation."""
    start, end = parse_ts(contract["window"]["start"]), parse_ts(contract["window"]["end"])
    if at >= end:
        return 0.0
    if at >= start:
        base = contract.get("during_window_refund_pct", 60)
        remaining = (end - at).total_seconds() / (end - start).total_seconds()
        return round(base * remaining, 4)  # au prorata des heures non commencées
    hours_before = (start - at).total_seconds() / 3600
    grid = sorted(contract.get("cancellation") or [{"hours_before_start": 24, "refund_pct": 90}, {"hours_before_start": 0, "refund_pct": 75}],
                  key=lambda g: -g["hours_before_start"])
    for g in grid:
        if hours_before >= g["hours_before_start"]:
            return float(g["refund_pct"])
    return float(grid[-1]["refund_pct"])


def settlement_flows_settled(contract: dict) -> dict:
    n, f, c = notional(contract), fees(contract), collateral(contract)
    return {
        "outcome": "SETTLED",
        "buyer_paid": n,
        "seller_receives": round(n - f["total"], 2),
        "protocol_fee": f["protocol"],
        "oracle_fee": f["oracle"],
        "collateral_returned_to_seller": c,
        "buyer_refund": 0.0,
        "buyer_indemnity": 0.0,
    }


def settlement_flows_seller_default(contract: dict, reference_price: float | None, delivered_minutes_late: float = 0.0) -> dict:
    """§6.2 : l'acheteur est remboursé ; l'indemnité = surcoût de remplacement au prix de référence, plafonnée au collatéral."""
    n, c = notional(contract), collateral(contract)
    gpu_hours = gpus_per_unit(contract["product"]) * contract["quantity"] * window_hours(contract)
    ref = reference_price if reference_price is not None else contract["price_per_gpu_hour"]
    replacement_cost = max(0.0, (ref - contract["price_per_gpu_hour"]) * gpu_hours)
    late_penalty = contract["price_per_gpu_hour"] * gpus_per_unit(contract["product"]) * contract["quantity"] * (delivered_minutes_late / 60)
    indemnity = round(min(c, replacement_cost + late_penalty), 2)
    return {
        "outcome": "DEFAULTED_SELLER",
        "buyer_paid": n,
        "buyer_refund": n,
        "buyer_indemnity": indemnity,
        "indemnity_basis": {"reference_price": ref, "replacement_cost": round(replacement_cost, 2), "late_penalty": round(late_penalty, 2), "cap": c},
        "seller_receives": 0.0,
        "collateral_returned_to_seller": round(c - indemnity, 2),
        "protocol_fee": 0.0,
        "oracle_fee": 0.0,
    }


def settlement_flows_cancelled(contract: dict, at: datetime) -> dict:
    n, c = notional(contract), collateral(contract)
    pct = refund_pct_on_cancellation(contract, at)
    refund = round(n * pct / 100, 2)
    return {
        "outcome": "CANCELLED_BUYER",
        "buyer_paid": n,
        "buyer_refund": refund,
        "refund_pct": pct,
        "seller_receives": round(n - refund, 2),  # la retenue va au vendeur (v0.1)
        "collateral_returned_to_seller": c,
        "protocol_fee": 0.0,
        "oracle_fee": 0.0,
        "buyer_indemnity": 0.0,
    }
