"""Boucle de règlement de référence du protocole VCD (v0).

États (spec §6.1) : CREATED → FUNDED → DELIVERY_PENDING → PROVISIONED → ACCEPTED → SETTLED,
avec les sorties DEFAULTED_SELLER (retard ou reçu non conforme), REJECTED et CANCELLED_BUYER.

v0 : l'escrow est *enregistré*, pas opéré (les références de paiement sont consignées ; aucun fonds ne bouge
ici). Tous les flux d'argent sont calculés et inscrits dans un journal public à chaîne de hachage.

    python -m settlement create spec/examples/contract.example.json
    python -m settlement fund <id> --buyer-ref pay_123 --seller-ref col_456
    python -m settlement nonce <id>                       # défi à donner à l'oracle
    python -m settlement start <id> [--at ISO]
    python -m settlement provision <id> --access "ssh://buyer@10.0.0.4" [--at ISO]
    python -m settlement verify <id> --receipt receipt.json [--reference-price 2.9]
    python -m settlement settle <id> [--at ISO]
    python -m settlement cancel <id> [--at ISO]
    python -m settlement show <id> | ledger
"""

LEDGER_VERSION = "0.1.0"
