"""CLI de la boucle de règlement. Voir settlement/__init__.py pour les commandes."""
from __future__ import annotations

import argparse
import json
import sys

from . import engine, ledger


def _print_state(state: dict) -> None:
    c = state["contract"]
    print(f"contrat {c['contract_id']} | {c['product']} x{c['quantity']} classe ≥ {c['min_class']} | {c['window']['start']} → {c['window']['end']}")
    print(f"état : {state['status']} | notionnel {state['amounts']['notional']} {c['currency']} | collatéral {state['amounts']['collateral']} | frais {state['amounts']['fees']['total']}")
    if state.get("nonce"):
        print(f"nonce : {state['nonce']}")
    if state.get("delivery"):
        d = state["delivery"]
        print(f"livraison : {d['status']} à {d['access_ts']} (retard {d['late_minutes']} min) via {d['access']}")
    if state.get("receipt"):
        r = state["receipt"]
        print(f"reçu : {r['receipt_id']} classe {r['class']} conforme={r['conforming']} (oracle {r['oracle_key_id']})")
    if state.get("flows"):
        print("flux : " + json.dumps(state["flows"], ensure_ascii=False))
    print("historique : " + " → ".join(h["kind"] for h in state["history"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="settlement", description="Boucle de règlement VCD v0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create"); p.add_argument("contract_json")
    p = sub.add_parser("fund"); p.add_argument("id"); p.add_argument("--buyer-ref", required=True); p.add_argument("--seller-ref", required=True); p.add_argument("--at")
    p = sub.add_parser("nonce"); p.add_argument("id")
    p = sub.add_parser("start"); p.add_argument("id"); p.add_argument("--at"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("provision"); p.add_argument("id"); p.add_argument("--access", required=True); p.add_argument("--at")
    p = sub.add_parser("verify"); p.add_argument("id"); p.add_argument("--receipt", required=True); p.add_argument("--reference-price", type=float); p.add_argument("--at")
    p = sub.add_parser("settle"); p.add_argument("id"); p.add_argument("--at"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("cancel"); p.add_argument("id"); p.add_argument("--at")
    p = sub.add_parser("show"); p.add_argument("id")
    sub.add_parser("ledger")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "create":
            with open(args.contract_json, encoding="utf-8") as f:
                state = engine.create(json.load(f))
        elif args.cmd == "fund":
            state = engine.fund(args.id, args.buyer_ref, args.seller_ref, args.at)
        elif args.cmd == "nonce":
            state = engine.issue_nonce(args.id)
        elif args.cmd == "start":
            state = engine.start(args.id, args.at, args.force)
        elif args.cmd == "provision":
            state = engine.provision(args.id, args.access, args.at)
        elif args.cmd == "verify":
            with open(args.receipt, encoding="utf-8") as f:
                state = engine.verify(args.id, json.load(f), args.reference_price, args.at)
        elif args.cmd == "settle":
            state = engine.settle(args.id, args.at, args.force)
        elif args.cmd == "cancel":
            state = engine.cancel(args.id, args.at)
        elif args.cmd == "show":
            state = ledger.load_contract(args.id)
        elif args.cmd == "list":
            for s in ledger.list_contracts():
                print(f"{s['contract']['contract_id']}  {s['status']:18s} {s['contract']['product']} x{s['contract']['quantity']}  {s['contract']['window']['start']}")
            return 0
        elif args.cmd == "ledger":
            for ev in ledger.read_events():
                print(f"{ev['seq']:4d} {ev['ts']} {ev['contract_id']} {ev['kind']:18s} {ev['hash'][:12]}")
            ok, msg = ledger.verify_chain()
            print(("OK " if ok else "ECHEC ") + msg)
            return 0 if ok else 1
        _print_state(state)
        return 0
    except engine.SettlementError as e:
        print(f"refus : {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
