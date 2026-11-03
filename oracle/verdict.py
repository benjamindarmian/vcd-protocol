"""Règle de classe (spec §3) : identité, mesures primaires, mesures secondaires -> A / B / C / FAIL."""
from __future__ import annotations

CLASS_ORDER = {"A": 3, "B": 2, "C": 1, "FAIL": 0}
PRIMARY = ("compute", "hbm", "interconnect", "stability")


def _grade(value, thresholds: dict | None, higher_is_better: bool = True) -> str | None:
    """A si value atteint le seuil A, B si le seuil B, sinon C. None si non mesuré ou non applicable."""
    if thresholds is None or value is None:
        return None
    a, b = thresholds["A"], thresholds["B"]
    if higher_is_better:
        return "A" if value >= a else ("B" if value >= b else "C")
    return "A" if value <= a else ("B" if value <= b else "C")


def _min_class(grades: list[str]) -> str:
    return min(grades, key=lambda g: CLASS_ORDER[g]) if grades else "C"


def compute_verdict(product_key: str, products: dict, identity: dict, measurements: dict, min_class: str = "A") -> dict:
    p = products["products"][product_key]
    ident_req, th = p["identity"], p["thresholds"]
    reasons: list[str] = []
    warnings: list[str] = []

    # 1. Identité
    fail = False
    n = identity.get("gpu_count", 0)
    if n != ident_req["gpus"]:
        fail = True
        reasons.append(f"identité : {n} GPU détectés, {ident_req['gpus']} attendus")
    fam = identity.get("family")
    if fam != ident_req["family"]:
        fail = True
        reasons.append(f"identité : famille détectée {fam or 'inconnue'}, {ident_req['family']} attendue")
    form = identity.get("form")
    if form is None:
        warnings.append("identité : forme (SXM/PCIe) non déterminée, la mesure d'interconnexion fait foi")
    elif form != ident_req["form"]:
        fail = True
        reasons.append(f"identité : forme détectée {form}, {ident_req['form']} attendue")
    mems = [g.get("memory_gb") for g in identity.get("gpus", []) if g.get("memory_gb") is not None]
    if mems and min(mems) < ident_req["min_mem_gb"]:
        fail = True
        reasons.append(f"identité : mémoire minimale {min(mems)} Go < {ident_req['min_mem_gb']} Go")
    if any(g.get("mig_enabled") for g in identity.get("gpus", [])):
        fail = True
        reasons.append("identité : MIG activé, le GPU est partitionné")
    if ident_req.get("nvlink_required") and (identity.get("nvlink_links_active") or 0) == 0:
        warnings.append("identité : aucun lien NVLink actif déclaré par le pilote ; la mesure all-reduce décide")
    if fail:
        return {"class": "FAIL", "conforming": False, "reasons": reasons, "warnings": warnings, "grades": {}}

    # 2. Mesures primaires
    grades: dict[str, str | None] = {}
    comp = measurements.get("compute", {})
    grades["compute"] = _grade(comp.get("min_tflops"), th["compute_tflops"])
    if grades["compute"] is None:
        reasons.append("calcul : non mesuré")
    elif grades["compute"] != "A":
        reasons.append(f"calcul : {comp.get('min_tflops')} TFLOPS (GPU le plus lent), seuil A {th['compute_tflops']['A']}")

    hbm = measurements.get("hbm", {})
    grades["hbm"] = _grade(hbm.get("min_gbs"), th["hbm_gbs"])
    if grades["hbm"] is None:
        reasons.append("mémoire : non mesurée")
    elif grades["hbm"] != "A":
        reasons.append(f"mémoire : {hbm.get('min_gbs')} GB/s, seuil A {th['hbm_gbs']['A']}")

    if th.get("interconnect_gbs"):
        ic = measurements.get("interconnect", {})
        grades["interconnect"] = _grade(ic.get("busbw_gbs"), th["interconnect_gbs"])
        if grades["interconnect"] is None:
            reasons.append(f"interconnexion : non mesurée ({ic.get('reason') or 'sans détail'})")
        elif grades["interconnect"] != "A":
            reasons.append(f"interconnexion : {ic.get('busbw_gbs')} GB/s en all-reduce, seuil A {th['interconnect_gbs']['A']}")

    st = measurements.get("stability", {})
    grades["stability"] = _grade(st.get("max_degradation_pct"), th["stability_max_degradation_pct"], higher_is_better=False)
    if grades["stability"] is None:
        reasons.append("stabilité : non mesurée")
    elif grades["stability"] != "A":
        reasons.append(f"stabilité : dégradation {st.get('max_degradation_pct')} % sous charge, seuil A {th['stability_max_degradation_pct']['A']} %")
    share = st.get("max_throttle_share")
    if share is not None and share > 0.2 and grades["stability"] == "A":
        grades["stability"] = "B"
        reasons.append(f"stabilité : bridage thermique ou de puissance sur {round(share * 100)} % des échantillons")

    primary_grades = [g if g is not None else "C" for k, g in grades.items() if k in PRIMARY]
    cls = _min_class(primary_grades)

    # 3. Mesures secondaires : plafonnent à B si sous le seuil A ; non mesurées = avertissement
    net = measurements.get("network", {})
    g_net = _grade(net.get("down_mbps"), th["network_down_mbps"])
    sto = measurements.get("storage", {})
    g_sto = _grade(sto.get("write_mbs"), th["storage_write_mbs"])
    grades["network"], grades["storage"] = g_net, g_sto
    for name, g, val, unit, thr in (("réseau", g_net, net.get("down_mbps"), "Mbit/s", th["network_down_mbps"]),
                                    ("stockage", g_sto, sto.get("write_mbs"), "MB/s", th["storage_write_mbs"])):
        if g is None:
            warnings.append(f"{name} : non mesuré")
        elif g != "A":
            if cls == "A":
                cls = "B"
            reasons.append(f"{name} : {val} {unit}, seuil A {thr['A']}")

    conforming = CLASS_ORDER[cls] >= CLASS_ORDER[min_class]
    if not reasons:
        reasons.append("toutes les mesures atteignent le seuil A")
    return {"class": cls, "conforming": conforming, "reasons": reasons, "warnings": warnings, "grades": grades}
