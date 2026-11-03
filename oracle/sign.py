"""Contrôleur de l'oracle : vérifie un lot de mesures brut produit sur la machine mesurée, puis le signe.

La machine mesurée n'a jamais la clé. Elle renvoie un lot (python -m oracle.run --nonce <défi> ...) ; le contrôleur :
  1. vérifie le nonce (anti-rejeu) et la version de la suite ;
  2. recalcule chaque débit à partir des compteurs bruts (itérations, octets, secondes) et refuse si le résumé
     ne correspond pas aux compteurs ;
  3. recalcule le verdict avec SON exemplaire de products.json (jamais celui du lot) ;
  4. signe.

    python -m oracle.sign bundle.json --nonce <défi attendu> --out receipt.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import SUITE_VERSION
from .receipt import DEFAULT_KEY_FILE, sign_bundle, verify_receipt
from .util import load_products, now_iso
from .verdict import compute_verdict

TOL = 0.015  # 1,5 % de tolérance d'arrondi entre le résumé et le recalcul


def _close(a, b, tol=TOL) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) <= tol


def check_bundle(bundle: dict, expected_nonce: str | None, products: dict) -> tuple[list[str], list[str], dict]:
    """Retourne (erreurs bloquantes, avertissements, verdict recalculé)."""
    errors: list[str] = []
    warnings: list[str] = []
    if expected_nonce is not None and bundle.get("nonce") != expected_nonce:
        errors.append(f"nonce : attendu {expected_nonce!r}, reçu {bundle.get('nonce')!r} (rejeu ou lot étranger)")
    if bundle.get("signature"):
        errors.append("le lot est déjà signé : un lot à contrôler doit être non signé")
    if bundle.get("suite_version") != SUITE_VERSION:
        warnings.append(f"version de suite {bundle.get('suite_version')} différente du contrôleur {SUITE_VERSION}")
    m = bundle.get("measurements", {})

    # compute : tflops = 2 n^3 iters / seconds
    comp = m.get("compute", {})
    vals = []
    for d in comp.get("per_device", []):
        if d.get("status") != "ok":
            continue
        n, it, sec = d.get("matrix"), d.get("iters"), d.get("seconds")
        if not (n and it and sec):
            errors.append(f"compute GPU {d.get('device')} : compteurs bruts absents")
            continue
        recomputed = 2.0 * n ** 3 * it / sec / 1e12
        if not _close(d.get("tflops"), recomputed):
            errors.append(f"compute GPU {d.get('device')} : résumé {d.get('tflops')} TFLOPS, compteurs {recomputed:.1f}")
        if sec < 3.0:
            errors.append(f"compute GPU {d.get('device')} : fenêtre de mesure {sec} s trop courte")
        vals.append(round(recomputed, 1))
    if vals and not _close(comp.get("min_tflops"), min(vals)):
        errors.append(f"compute : min_tflops {comp.get('min_tflops')} ne correspond pas au minimum des GPU {min(vals)}")

    # hbm : gbs = 2 * buffer * iters / seconds
    hbm = m.get("hbm", {})
    hv = []
    for d in hbm.get("per_device", []):
        if d.get("status") != "ok":
            continue
        b, it, sec = d.get("buffer_bytes"), d.get("iters"), d.get("seconds")
        if not (b and it and sec):
            errors.append(f"hbm GPU {d.get('device')} : compteurs bruts absents")
            continue
        recomputed = 2.0 * b * it / sec / 1e9
        if not _close(d.get("gbs"), recomputed):
            errors.append(f"hbm GPU {d.get('device')} : résumé {d.get('gbs')} GB/s, compteurs {recomputed:.1f}")
        hv.append(round(recomputed, 1))
    if hv and not _close(hbm.get("min_gbs"), min(hv)):
        errors.append("hbm : min_gbs ne correspond pas au minimum des GPU")

    # interconnect : algbw = bytes*iters/seconds ; busbw = algbw * 2(w-1)/w
    ic = m.get("interconnect", {})
    ar = ic.get("allreduce", {}) if isinstance(ic, dict) else {}
    if ar.get("status") == "ok":
        b, it, sec, w = ar.get("bytes"), ar.get("iters"), ar.get("seconds"), ar.get("world")
        if not (b and it and sec and w):
            errors.append("interconnect : compteurs bruts absents")
        else:
            algbw = b * it / sec / 1e9
            busbw = algbw * 2 * (w - 1) / w
            if not _close(ar.get("busbw_gbs"), busbw) or not _close(ic.get("busbw_gbs"), busbw):
                errors.append(f"interconnect : résumé {ic.get('busbw_gbs')} GB/s, compteurs {busbw:.1f}")

    # stability : dégradation cohérente avec les tranches
    st = m.get("stability", {})
    for d in st.get("per_device", []) if isinstance(st, dict) else []:
        f, l, deg = d.get("tflops_first_slice"), d.get("tflops_last_slice"), d.get("degradation_pct")
        if f and l is not None and deg is not None:
            rec = max(0.0, (f - l) / f * 100)
            if abs(rec - deg) > 0.6:
                errors.append(f"stability GPU {d.get('device')} : dégradation {deg} % incohérente avec les tranches ({rec:.1f} %)")
    if st.get("status") == "ok" and st.get("seconds", 0) < 25:
        warnings.append(f"stability : charge de {st.get('seconds')} s seulement (mode rapide) ; non opposable pour un contrat")

    # timing : la durée déclarée doit tenir dans la fenêtre de mesure
    t = bundle.get("timing", {})
    try:
        from datetime import datetime
        ms = datetime.fromisoformat(t["measure_start"])
        me = datetime.fromisoformat(t["measure_end"])
        span = (me - ms).total_seconds()
        declared = sum(d.get("seconds", 0) + d.get("warmup_seconds", 0) for d in comp.get("per_device", []))
        declared += sum(d.get("seconds", 0) for d in hbm.get("per_device", []))
        declared += st.get("seconds", 0) if isinstance(st, dict) else 0
        if declared > span + 5:
            errors.append(f"timing : {declared:.0f} s de mesures déclarées dans une fenêtre de {span:.0f} s")
    except Exception:
        warnings.append("timing : horodatages illisibles")

    # verdict recalculé avec les seuils du contrôleur
    product = bundle.get("product")
    if product not in products["products"]:
        errors.append(f"produit inconnu du contrôleur : {product}")
        return errors, warnings, {}
    verdict = compute_verdict(product, products, bundle.get("identity", {}), m, bundle.get("min_class", "A"))
    if verdict.get("class") != bundle.get("verdict", {}).get("class"):
        warnings.append(f"verdict recalculé : {verdict.get('class')} (le lot disait {bundle.get('verdict', {}).get('class')})")
    return errors, warnings, verdict


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Contrôleur de l'oracle : vérifie et signe un lot de mesures")
    ap.add_argument("bundle")
    ap.add_argument("--nonce", help="défi attendu (celui transmis à la machine mesurée)")
    ap.add_argument("--out", help="chemin du reçu signé (défaut : <bundle>.signed.json)")
    ap.add_argument("--key-file", default=str(DEFAULT_KEY_FILE))
    ap.add_argument("--oracle-name", default=None)
    ap.add_argument("--force", action="store_true", help="signer malgré des erreurs (jamais pour un contrat)")
    args = ap.parse_args(argv)

    with open(args.bundle, encoding="utf-8") as f:
        bundle = json.load(f)
    products = load_products()
    errors, warnings, verdict = check_bundle(bundle, args.nonce, products)
    for e in errors:
        print(f"ERREUR  {e}")
    for w in warnings:
        print(f"avert.  {w}")
    if errors and not args.force:
        print("lot refusé : non signé")
        return 2
    bundle["verdict"] = verdict or bundle.get("verdict")
    bundle["thresholds"] = {"product": bundle.get("product"), "spec_version": products["spec_version"], **products["products"][bundle["product"]]}
    bundle["controller_checks"] = {"checked_at": now_iso(), "errors": errors, "warnings": warnings, "forced": bool(errors)}
    receipt = sign_bundle(bundle, Path(args.key_file), args.oracle_name)
    out = Path(args.out) if args.out else Path(args.bundle).with_suffix(".signed.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=1)
    ok, msg = verify_receipt(receipt)
    print(f"signé : {out} | classe {receipt['verdict'].get('class')} | conforme {receipt['verdict'].get('conforming')} | {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
