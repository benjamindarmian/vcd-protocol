# Boucle de règlement (v0)

Implémentation de référence des états et des flux d'argent de la spec §6. En v0, **le séquestre est enregistré,
pas opéré** : les références de paiement (Stripe, Lemonway, transaction on-chain…) sont consignées et tous les montants
sont calculés, mais aucun fonds ne bouge ici. Tout est inscrit dans un journal public en ajout seul, à chaîne de hachage
(`ledger/events.jsonl`), dont l'intégrité se vérifie avec `python -m settlement ledger`.

## Une fenêtre, de bout en bout

```bash
python -m settlement create spec/examples/contract.example.json            # CREATED : contrat validé, notionnel et collatéral calculés
python -m settlement fund vcd-2026-11-27-000184 --buyer-ref pay_… --seller-ref col_…   # FUNDED : paiement et collatéral enregistrés
python -m settlement nonce vcd-2026-11-27-000184                            # défi à transmettre à l'oracle pour la mesure
python -m settlement start vcd-2026-11-27-000184                            # DELIVERY_PENDING : la fenêtre est ouverte
python -m settlement provision vcd-2026-11-27-000184 --access "ssh://buyer@host"   # PROVISIONED, ou DEFAULTED_SELLER au-delà du double du délai
# sur la machine livrée : python -m oracle.run --contract contrat.json --nonce <défi> --out lot.json
# sur le contrôleur :     python -m oracle.sign lot.json --nonce <défi> --out recu.json
python -m settlement verify vcd-2026-11-27-000184 --receipt recu.json --reference-price 2.9   # ACCEPTED, ou DEFAULTED_SELLER si non conforme
python -m settlement settle vcd-2026-11-27-000184                           # SETTLED à la fin de la fenêtre : flux calculés
python -m settlement ledger                                                 # journal et vérification de la chaîne
```

## Ce que la boucle vérifie avant d'accepter un reçu

Signature valide **et** clé publique égale à celle du contrat ; `contract_id` du reçu ; nonce égal à celui émis pour ce
contrat ; produit et classe minimale identiques ; mesure en mode complet (le mode rapide n'est pas opposable) ; version
de spec compatible. Un reçu modifié après signature, signé par une autre clé, rejoué ou mesuré en mode rapide est refusé
et le contrat reste PROVISIONED.

## Flux d'argent (spec §6.2)

| Issue | Acheteur | Vendeur |
|---|---|---|
| SETTLED | a payé le notionnel | reçoit le notionnel moins les frais ; collatéral restitué (moins la pénalité de retard éventuelle) |
| DEFAULTED_SELLER | remboursé intégralement + indemnité = surcoût de remplacement au prix de référence, plafonnée au collatéral | perd l'indemnité sur son collatéral |
| CANCELLED_BUYER | remboursé selon la grille (90 % / 75 % / 60 % au prorata) | reçoit la retenue ; collatéral restitué |

## Ce qui manque pour un contrat réel

L'opération du séquestre (prestataire de paiement agréé ou contrat intelligent non custodial), les adaptateurs de
provisionnement automatiques par fournisseur, la contre-mesure par un second oracle en cas de contestation, et le
multi-nœuds. La structure des états et des flux ne changera pas ; seuls les connecteurs s'ajoutent.
