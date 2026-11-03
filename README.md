# VCD — Verified Compute Delivery

*Spécification ouverte du contrat de livraison vérifiable de compute, et suite de conformité de référence.*

**English summary.** An hour of GPU compute has no price because it has no unit: the "same" H100 sells for
1.92 to 3.20 USD depending on the vendor, the commitment and what is actually delivered. VCD defines
(1) products described by measured work rather than hardware names, (2) quality classes A/B/C/FAIL assigned
by measurement, (3) a physically-settled delivery contract with escrow and seller collateral, (4) a signed
delivery receipt issued by an independent oracle, and (5) settlement rules. The reference conformance suite
(`oracle/`) measures a delivered machine in about ten minutes and emits an ed25519-signed receipt. Spec text
is in French for now; an English translation is planned. Specification: CC BY 4.0. Code: Apache-2.0.

## Contenu

| Dossier | Quoi |
|---|---|
| `spec/SPEC.md` | la spécification v0.1 : produits, classes, contrat, reçu, règlement, incidents, annulation, gouvernance |
| `spec/products.json` | les produits et leurs seuils (provisoires, à calibrer) |
| `spec/schemas/` | schémas JSON du contrat et du reçu |
| `spec/examples/` | un contrat d'exemple et un reçu signé d'exemple (mesures synthétiques) |
| `oracle/` | la suite de conformité : identité, calcul, mémoire, interconnexion, réseau, stockage, stabilité, verdict, reçu ; `oracle.sign` : contrôleur qui vérifie les compteurs bruts et signe |
| `settlement/` | la boucle de règlement v0 : états du contrat, flux d'argent, journal public à chaîne de hachage |
| `calibration/` | journal des mesures de référence et des décisions de seuil |
| `notebooks/` | calibration de la suite sur un GPU gratuit (Kaggle, Colab) |
| `scripts/run_oracle_remote.sh` | lancer la suite sur une machine fraîchement livrée |
| `tests/` | tests hors GPU : règle de classe, falsification de reçu, schémas, bout en bout |

## Démarrer

```bash
pip install -r requirements.txt
python -m oracle.run --product H100.SXM.8 --min-class A --contract spec/examples/contract.example.json   # règlement
python -m oracle.run --detect --purpose index --venue runpod --mode quick                               # indice
python -m oracle.run --allow-cpu --mode quick                                                            # sans GPU
python -m oracle.verify receipts/<id>.json
```

Sur un GPU gratuit, une cellule suffit (voir `notebooks/`) :

```python
!git clone -q https://github.com/benjamindarmian/vcd-protocol.git vcd && cd vcd && pip -q install nvidia-ml-py cryptography && python -m oracle.run --detect --purpose calibration --mode quick --skip network
```

## Pourquoi

Les précédents sont connus : les grades de Chicago ont précédé les contrats à terme sur le blé, les garanties de
capacité certifiées par RTE ont précédé le marché de capacité électrique, OpenRTB a précédé la publicité
programmatique. À chaque fois, l'unité et la mesure sont venues avant le marché. Le compute n'a pas encore
d'unité mesurée ni de règlement en nature : les indices existants moyennent des prix affichés, et les dérivés
qui s'y adossent n'ont pas de marché physique dessous. VCD est la pièce manquante : un contrat que n'importe
quel vendeur peut honorer, que n'importe quel oracle peut constater, et que n'importe quelle place peut régler.

## État

Version 0.1.0, novembre 2026. Les seuils de `products.json` sont provisoires ; la première étape est de les
recaler sur des machines de référence mesurées avec la suite. Les contributions (mesures, seuils, cas de
règlement, traduction) se font par issue ou pull request.
