"""Suite de conformité de référence du protocole VCD (Verified Compute Delivery).

Mesure une machine GPU livrée, compare les mesures aux seuils du produit commandé
(spec/products.json) et produit un reçu signé (spec/schemas/receipt.schema.json).

Usage :
    python -m oracle.run --product H100.SXM.8 --min-class A
    python -m oracle.run --detect --purpose index --venue runpod --mode quick
    python -m oracle.verify receipts/<id>.json
"""

SUITE_VERSION = "0.1.0"
SPEC_VERSION = "0.1.0"
