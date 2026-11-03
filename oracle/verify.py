"""Vérifie un reçu : hash canonique et signature ed25519.

    python -m oracle.verify receipts/rcpt-....json [--pubkey <base64 attendue>]
"""
from __future__ import annotations

import argparse
import json
import sys

from .receipt import verify_receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("receipt")
    ap.add_argument("--pubkey", help="clé publique ed25519 attendue (base64), par exemple celle du contrat")
    args = ap.parse_args(argv)
    with open(args.receipt, encoding="utf-8") as f:
        r = json.load(f)
    ok, msg = verify_receipt(r, args.pubkey)
    v = r.get("verdict", {})
    print(f"{'OK' if ok else 'ECHEC'} — {msg}")
    print(f"produit {r.get('product')} | classe {v.get('class')} | conforme : {v.get('conforming')} | {r.get('timing', {}).get('measure_end')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
