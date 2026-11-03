"""Journal public en ajout seul, à chaîne de hachage : chaque événement contient le hash du précédent.

Disposition (répertoire `ledger/`, configurable par VCD_LEDGER_DIR) :
    ledger/events.jsonl            tous les événements, dans l'ordre, chaînés
    ledger/contracts/<id>.json     état courant de chaque contrat (dérivé des événements)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from oracle.util import canonical_json, now_iso

from . import LEDGER_VERSION

GENESIS = "0" * 64


def ledger_dir() -> Path:
    return Path(os.environ.get("VCD_LEDGER_DIR", "ledger"))


def _events_path() -> Path:
    return ledger_dir() / "events.jsonl"


def _contract_path(contract_id: str) -> Path:
    return ledger_dir() / "contracts" / f"{contract_id}.json"


def read_events() -> list[dict]:
    p = _events_path()
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def last_hash() -> str:
    ev = read_events()
    return ev[-1]["hash"] if ev else GENESIS


def append_event(contract_id: str, kind: str, data: dict, at: str | None = None) -> dict:
    ev = {
        "ledger_version": LEDGER_VERSION,
        "seq": len(read_events()) + 1,
        "ts": at or now_iso(),
        "recorded_at": now_iso(),
        "contract_id": contract_id,
        "kind": kind,
        "data": data,
        "prev_hash": last_hash(),
    }
    ev["hash"] = hashlib.sha256(canonical_json({k: v for k, v in ev.items() if k != "hash"})).hexdigest()
    p = _events_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
    return ev


def verify_chain() -> tuple[bool, str]:
    prev = GENESIS
    for i, ev in enumerate(read_events(), start=1):
        if ev.get("seq") != i:
            return False, f"séquence rompue à l'événement {i}"
        if ev.get("prev_hash") != prev:
            return False, f"chaîne rompue à l'événement {i}"
        h = hashlib.sha256(canonical_json({k: v for k, v in ev.items() if k != "hash"})).hexdigest()
        if h != ev.get("hash"):
            return False, f"hash altéré à l'événement {i}"
        prev = ev["hash"]
    return True, f"{len(read_events())} événements, chaîne intacte"


def load_contract(contract_id: str) -> dict:
    p = _contract_path(contract_id)
    if not p.exists():
        raise FileNotFoundError(f"contrat inconnu : {contract_id}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_contract(state: dict) -> None:
    p = _contract_path(state["contract"]["contract_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def list_contracts() -> list[dict]:
    d = ledger_dir() / "contracts"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            out.append(json.load(f))
    return out
