"""Reçu de livraison : construction, hachage canonique, signature ed25519, vérification."""
from __future__ import annotations

import base64
import hashlib
import os
import uuid
from pathlib import Path

from . import SPEC_VERSION, SUITE_VERSION
from .util import canonical_json, now_iso

DEFAULT_KEY_FILE = Path(os.environ.get("VCD_ORACLE_KEY_FILE", str(Path.home() / ".vcd" / "oracle_key.pem")))


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        return serialization, ed25519
    except Exception:
        return None, None


def load_or_create_key(path: Path = DEFAULT_KEY_FILE):
    """Charge la clé privée ed25519 de l'oracle, ou la crée. Retourne (private_key, public_b64, key_id) ou (None, None, None)."""
    serialization, ed25519 = _crypto()
    if ed25519 is None:
        return None, None, None
    path = Path(path)
    if path.exists():
        with open(path, "rb") as f:
            priv = serialization.load_pem_private_key(f.read(), password=None)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        priv = ed25519.Ed25519PrivateKey.generate()
        pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        with open(path, "wb") as f:
            f.write(pem)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    pub_raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pub_b64 = base64.b64encode(pub_raw).decode()
    key_id = "oracle-" + hashlib.sha256(pub_raw).hexdigest()[:12]
    return priv, pub_b64, key_id


def receipt_hash(receipt: dict) -> str:
    body = {k: v for k, v in receipt.items() if k not in ("hash", "signature")}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def build_receipt(*, product: str, min_class: str, identity: dict, measurements: dict, thresholds: dict, verdict: dict,
                  timing: dict, purpose: str = "contract", contract_id: str | None = None, venue: str | None = None,
                  key_file: Path = DEFAULT_KEY_FILE, sign: bool = True, oracle_name: str | None = None) -> dict:
    priv, pub_b64, key_id = (None, None, None)
    if sign:
        priv, pub_b64, key_id = load_or_create_key(key_file)
    receipt = {
        "spec_version": SPEC_VERSION,
        "suite_version": SUITE_VERSION,
        "receipt_id": f"rcpt-{now_iso()[:10]}-{uuid.uuid4().hex[:10]}",
        "contract_id": contract_id,
        "purpose": purpose,
        "product": product,
        "min_class": min_class,
        "venue": venue,
        "timing": timing,
        "identity": identity,
        "measurements": measurements,
        "thresholds": thresholds,
        "verdict": verdict,
        "oracle": {"key_id": key_id or "unsigned", "public_key": pub_b64 or "", "name": oracle_name},
    }
    h = receipt_hash(receipt)
    receipt["hash"] = h
    if priv is not None:
        sig = priv.sign(bytes.fromhex(h))
        receipt["signature"] = base64.b64encode(sig).decode()
    else:
        receipt["signature"] = None
    return receipt


def verify_receipt(receipt: dict, expected_public_key_b64: str | None = None) -> tuple[bool, str]:
    """Recalcule le hash et vérifie la signature. Retourne (ok, message)."""
    h = receipt_hash(receipt)
    if h != receipt.get("hash"):
        return False, "hash recalculé différent : le reçu a été modifié"
    sig = receipt.get("signature")
    if not sig:
        return False, "reçu non signé (non opposable)"
    pub_b64 = receipt.get("oracle", {}).get("public_key")
    if expected_public_key_b64 and expected_public_key_b64 != pub_b64:
        return False, "la clé publique du reçu n'est pas celle attendue par le contrat"
    serialization, ed25519 = _crypto()
    if ed25519 is None:
        return False, "bibliothèque cryptography absente : impossible de vérifier"
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        pub.verify(base64.b64decode(sig), bytes.fromhex(h))
    except Exception as e:
        return False, f"signature invalide : {e}"
    return True, f"signature valide, clé {receipt['oracle'].get('key_id')}"
