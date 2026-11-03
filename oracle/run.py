"""CLI de la suite de conformité.

Exemples :
    python -m oracle.run --product H100.SXM.8 --min-class A --contract spec/examples/contract.example.json
    python -m oracle.run --detect --purpose index --venue vast --mode quick
    python -m oracle.run --allow-cpu --mode quick            # validation sans GPU (verdict FAIL attendu)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .util import cuda_available, load_products, now_iso
from .identity import collect_identity, guess_product
from .receipt import build_receipt, verify_receipt
from .verdict import CLASS_ORDER, compute_verdict

STEPS = ("compute", "hbm", "interconnect", "network", "storage", "stability")


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _print_summary(receipt: dict):
    ident = receipt["identity"]
    m = receipt["measurements"]
    v = receipt["verdict"]
    names = sorted({g.get("name") for g in ident.get("gpus", [])} - {None})
    print("\n=== VCD — reçu de conformité ===")
    print(f"produit demandé : {receipt['product']}  (classe minimale {receipt['min_class']})")
    print(f"détecté         : {ident.get('gpu_count')} GPU {', '.join(names) or '-'} | famille {ident.get('family')} | forme {ident.get('form')} | NVLink {ident.get('nvlink_links_active')}")
    print(f"hôte            : {ident['host'].get('cpu_model')} x{ident['host'].get('cpu_count')} | RAM {ident['host'].get('ram_gb')} Go | virt {ident['host'].get('virtualization')}")
    def val(d, k, unit=""):
        x = d.get(k) if isinstance(d, dict) else None
        return f"{x} {unit}".strip() if x is not None else f"n/a ({(d or {}).get('reason') or (d or {}).get('status')})"
    dtypes = sorted({d.get("dtype") for d in m.get("compute", {}).get("per_device", []) if d.get("dtype")})
    print(f"calcul          : {val(m.get('compute'), 'min_tflops', 'TFLOPS (GPU le plus lent)')} | moyenne {m.get('compute', {}).get('mean_tflops')} | {', '.join(dtypes) or '-'}")
    print(f"mémoire HBM     : {val(m.get('hbm'), 'min_gbs', 'GB/s')}")
    print(f"interconnexion  : {val(m.get('interconnect'), 'busbw_gbs', 'GB/s busbw all-reduce')}")
    print(f"réseau          : {val(m.get('network'), 'down_mbps', 'Mbit/s desc.')} | montant {m.get('network', {}).get('up_mbps')} | latence {m.get('network', {}).get('latency_ms')} ms")
    print(f"stockage        : {val(m.get('storage'), 'write_mbs', 'MB/s écriture')} | lecture {m.get('storage', {}).get('read_mbs')}")
    print(f"stabilité       : {val(m.get('stability'), 'max_degradation_pct', '% de dégradation')} | ralentissement thermique {m.get('stability', {}).get('max_thermal_share')} | plafond de puissance {m.get('stability', {}).get('max_power_cap_share')}")
    t = receipt["timing"]
    if t.get("delivery_delay_s") is not None:
        print(f"livraison       : {t['delivery_delay_s']} s entre commande et accès")
    print(f"VERDICT         : classe {v['class']} — {'CONFORME' if v['conforming'] else 'NON CONFORME'} (minimum {receipt['min_class']})")
    for r in v["reasons"]:
        print(f"   - {r}")
    for w in v.get("warnings", []):
        print(f"   ! {w}")
    print(f"reçu            : {receipt['receipt_id']} | hash {receipt['hash'][:16]}… | signé : {'oui' if receipt.get('signature') else 'non'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Suite de conformité VCD")
    ap.add_argument("--product", help="produit commandé, ex. H100.SXM.8")
    ap.add_argument("--detect", action="store_true", help="déduire le produit de la machine détectée (mesure d'indice ou calibration)")
    ap.add_argument("--min-class", default="A", choices=["A", "B", "C"])
    ap.add_argument("--contract", help="fichier de contrat JSON (fournit produit, classe minimale, contract_id)")
    ap.add_argument("--purpose", default=None, choices=["contract", "index", "calibration"])
    ap.add_argument("--venue", default=None, help="fournisseur mesuré (pour l'indice)")
    ap.add_argument("--mode", default="full", choices=["full", "quick"])
    ap.add_argument("--compute-seconds", type=float, default=None)
    ap.add_argument("--stability-seconds", type=float, default=None)
    ap.add_argument("--skip", default="", help="étapes à sauter, séparées par des virgules : " + ",".join(STEPS))
    ap.add_argument("--order-ts", help="heure de commande ISO 8601 (pour le délai de livraison)")
    ap.add_argument("--access-ts", help="heure d'accès effectif ISO 8601 (défaut : maintenant)")
    ap.add_argument("--out", help="chemin du reçu JSON (défaut : receipts/<receipt_id>.json)")
    ap.add_argument("--allow-cpu", action="store_true", help="exécuter sans GPU (identité, réseau, stockage ; verdict FAIL)")
    ap.add_argument("--no-sign", action="store_true", help="produire un lot de mesures non signé (à signer par le contrôleur : python -m oracle.sign)")
    ap.add_argument("--nonce", default=os.environ.get("VCD_NONCE"), help="défi fourni par le contrôleur ; implique --no-sign")
    ap.add_argument("--no-jitter", action="store_true", help="durées fixes (par défaut : durées tirées au hasard à ±20 %%)")
    ap.add_argument("--no-countermeasure", action="store_true", help="ne pas relancer les mesures primaires avant un verdict non conforme (spec §2 règle 3)")
    ap.add_argument("--oracle-name", default=os.environ.get("VCD_ORACLE_NAME"))
    ap.add_argument("--json", action="store_true", help="imprimer le reçu complet sur stdout")
    args = ap.parse_args(argv)

    products = load_products()
    quick = args.mode == "quick"
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    contract = None
    if args.contract:
        with open(args.contract, encoding="utf-8") as f:
            contract = json.load(f)
    product = args.product or (contract or {}).get("product")
    min_class = (contract or {}).get("min_class") or args.min_class
    purpose = args.purpose or ("contract" if contract else ("index" if args.venue else "calibration"))

    measure_start = now_iso()
    access_dt = _parse_ts(args.access_ts) or datetime.now(timezone.utc)
    order_dt = _parse_ts(args.order_ts)

    identity = collect_identity(products)
    if args.detect or not product:
        guessed = guess_product(identity, products)
        if guessed:
            print(f"[detect] produit déduit de la machine : {guessed}", file=sys.stderr)
            product = guessed
        elif not product:
            print(f"[detect] aucun produit ne correspond à la machine détectée ({identity.get('gpu_count')} GPU, famille {identity.get('family')}).", file=sys.stderr)
            if not args.allow_cpu:
                return 2
            product = "H100.SXM.1"  # produit arbitraire pour produire un reçu FAIL documenté
    if product not in products["products"]:
        print(f"produit inconnu : {product}. Produits : {', '.join(products['products'])}", file=sys.stderr)
        return 2

    has_gpu = cuda_available()
    if not has_gpu and not args.allow_cpu:
        print("Aucun GPU CUDA utilisable (torch absent ou CUDA indisponible). Relancer avec --allow-cpu pour une validation sans GPU.", file=sys.stderr)
        return 3

    measurements: dict = {}
    import random
    jit = (lambda x: x) if args.no_jitter else (lambda x: round(x * random.uniform(0.8, 1.2), 1))
    cs = args.compute_seconds or jit(5.0 if quick else 15.0)
    ss = args.stability_seconds or jit(30.0 if quick else 300.0)
    sign = not (args.no_sign or args.nonce)

    def step(name, fn):
        if name in skip:
            measurements[name] = {"status": "skipped"}
            return
        print(f"[{name}] …", file=sys.stderr, flush=True)
        try:
            measurements[name] = fn()
        except Exception as e:  # une étape qui casse ne doit pas empêcher le reçu
            measurements[name] = {"status": "error", "reason": str(e)[:300]}
        print(f"[{name}] {measurements[name].get('status')}", file=sys.stderr, flush=True)

    if has_gpu:
        from .compute import measure_compute
        from .membw import measure_hbm
        from .interconnect import measure_interconnect
        from .stability import measure_stability
        step("compute", lambda: measure_compute(seconds=cs))
        step("hbm", lambda: measure_hbm(seconds=2.0 if quick else 5.0))
        step("interconnect", lambda: measure_interconnect(quick=quick))
    else:
        for k in ("compute", "hbm", "interconnect"):
            measurements[k] = {"status": "n/a", "reason": "pas de GPU CUDA"}
    from .network import measure_network
    from .storage import measure_storage
    step("network", lambda: measure_network(quick=quick))
    step("storage", lambda: measure_storage(quick=quick))
    if has_gpu:
        step("stability", lambda: measure_stability(seconds=ss, sample_every=2.0))
    else:
        measurements["stability"] = {"status": "n/a", "reason": "pas de GPU CUDA"}

    verdict = compute_verdict(product, products, identity, measurements, min_class)
    measurements_first = None
    primaries_below = [k for k in ("compute", "hbm", "interconnect", "stability") if verdict.get("grades", {}).get(k) in ("B", "C")]
    if (has_gpu and not args.no_countermeasure and not verdict["conforming"] and verdict["class"] != "FAIL"
            and primaries_below and any(CLASS_ORDER[verdict["grades"][k]] < CLASS_ORDER[min_class] for k in primaries_below)):
        print(f"[contre-mesure] mesures primaires sous le seuil ({', '.join(primaries_below)}) : second passage avant verdict", file=sys.stderr, flush=True)
        measurements_first = json.loads(json.dumps(measurements))
        step("compute", lambda: measure_compute(seconds=cs))
        step("hbm", lambda: measure_hbm(seconds=2.0 if quick else 5.0))
        step("interconnect", lambda: measure_interconnect(quick=quick))
        step("stability", lambda: measure_stability(seconds=ss, sample_every=2.0))
        verdict = compute_verdict(product, products, identity, measurements, min_class)
        verdict["warnings"].append("contre-mesure effectuée : le verdict porte sur le second passage, le premier est conservé dans measurements_first")
    if not has_gpu:
        verdict["warnings"].append("exécution sans GPU (--allow-cpu) : reçu de validation, non opposable")
    timing = {
        "order_ts": order_dt.replace(microsecond=0).isoformat() if order_dt else None,
        "access_ts": access_dt.replace(microsecond=0).isoformat(),
        "delivery_delay_s": round((access_dt - order_dt).total_seconds()) if order_dt else None,
        "measure_start": measure_start,
        "measure_end": now_iso(),
        "mode": args.mode,
    }
    thresholds = {"product": product, "spec_version": products["spec_version"], **products["products"][product]}
    receipt = build_receipt(product=product, min_class=min_class, identity=identity, measurements=measurements,
                            thresholds=thresholds, verdict=verdict, timing=timing, purpose=purpose,
                            contract_id=(contract or {}).get("contract_id"), venue=args.venue,
                            sign=sign, oracle_name=args.oracle_name, nonce=args.nonce,
                            measurements_first=measurements_first)
    out = Path(args.out) if args.out else Path("receipts") / f"{receipt['receipt_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=1)
    _print_summary(receipt)
    if sign:
        ok, msg = verify_receipt(receipt)
        print(f"vérification    : {msg}")
    else:
        print("lot non signé     : à transmettre au contrôleur (python -m oracle.sign <fichier>) qui recalcule, vérifie et signe")
    print(f"fichier         : {out}")
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=1))
    return 0 if verdict["conforming"] else 1


if __name__ == "__main__":
    sys.exit(main())
