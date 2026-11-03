# Suite de conformité VCD (code de référence)

Mesure une machine GPU livrée, la compare aux seuils du produit commandé (`spec/products.json`)
et produit un **reçu signé** (`spec/schemas/receipt.schema.json`). C'est l'oracle de la spécification
`spec/SPEC.md`. Le même programme sert au règlement d'un contrat (on mesure la machine payée avant de
donner les clés) et à l'indice (on mesure chaque semaine dix minutes louées chez chaque fournisseur).

## Ce qu'elle mesure

| Étape | Mesure | Durée (complet / rapide) |
|---|---|---|
| identité | modèle, nombre, mémoire des GPU, pilote, SXM/PCIe, liens NVLink, ECC, MIG ; CPU, RAM, disque, virtualisation | 1 s |
| compute | matmul 8192² BF16, TFLOPS soutenus, GPU par GPU | 15 s / 5 s par GPU |
| hbm | copie device-to-device 1 GiB, GB/s (lecture + écriture) | 5 s / 2 s par GPU |
| interconnect | all-reduce NCCL sur tous les GPU du nœud (bande passante de bus) + copie pair à pair | ~5 s |
| network | latence, débit descendant (100 Mo) et montant (25 Mo) vers un point public | ~10 s |
| storage | écriture puis lecture d'1 GiB, MB/s | ~5 s |
| stability | tous les GPU en charge, échantillonnage NVML (fréquences, température, puissance, bridage) ; dégradation dernière minute vs première | 300 s / 30 s |

Le verdict applique la règle de la spec §3 : FAIL si l'identité ne correspond pas, sinon A/B/C selon les
mesures primaires (compute, hbm, interconnect si le produit l'exige, stability) ; les mesures secondaires
(network, storage) plafonnent à B si elles sont sous le seuil A. Le reçu contient toutes les mesures, les
seuils appliqués, le verdict, et une signature ed25519 de son hash canonique.

## Lancer

Sur la machine à mesurer (torch avec CUDA doit être présent ; `pynvml` et `cryptography` s'installent en une seconde) :

```bash
pip install nvidia-ml-py cryptography
python -m oracle.run --product H100.SXM.8 --min-class A --contract spec/examples/contract.example.json
python -m oracle.run --detect --purpose index --venue runpod --mode quick     # mesure d'indice
python -m oracle.verify receipts/rcpt-....json                                # vérifier un reçu
```

Options utiles : `--order-ts` / `--access-ts` pour calculer le délai de livraison, `--skip network,storage`,
`--stability-seconds 120`, `--out chemin.json`, `--json`. Code de retour : 0 si conforme, 1 sinon.

Sans GPU (pour valider l'installation) : `python -m oracle.run --allow-cpu --mode quick` produit un reçu
FAIL signé, marqué « non opposable ».

### Sur un GPU gratuit (Kaggle, Colab) pour calibrer

Une cellule suffit (Kaggle : activer l'accélérateur GPU T4 ou P100 ; Colab : exécution > modifier le type) :

```python
!git clone -q https://github.com/benjamindarmian/vcd-protocol.git vcd && cd vcd && pip -q install nvidia-ml-py cryptography && python -m oracle.run --detect --purpose calibration --mode quick --skip network
```

Le produit `T4.PCIE.1` ou `P100.PCIE.1` est déduit automatiquement. Le but n'est pas la note : c'est de
vérifier que la suite identifie correctement la machine et donne des chiffres cohérents avec les
performances connues (T4 : environ 20 à 25 TFLOPS BF16/FP16 mesurés, 250 GB/s de mémoire).

## Clé de l'oracle

À la première exécution, une clé ed25519 est créée dans `~/.vcd/oracle_key.pem` (ou `VCD_ORACLE_KEY_FILE`).
La clé publique et son identifiant figurent dans chaque reçu ; un contrat référence cette clé publique.
Qui possède la clé publique peut vérifier un reçu ; qui possède la clé privée peut en émettre : elle ne
doit jamais quitter la machine de l'oracle.

## Seuils

Les seuils de `spec/products.json` sont provisoires (performances publiées et mesures communautaires).
Le travail de calibration consiste à exécuter la suite sur des machines de référence connues et à recaler
les valeurs A et B produit par produit, puis à incrémenter la version de la spécification.

## Limites de la v0.1

- interconnexion mesurée à l'intérieur d'un nœud seulement (pas de multi-nœuds) ;
- conformité constatée à la livraison, pas en continu pendant la fenêtre ;
- le test réseau dépend d'un point de mesure public et peut être bloqué par un pare-feu (il est alors « non mesuré », ce qui ne fait pas perdre la classe A) ;
- la lecture disque peut être partiellement servie par le cache si le système refuse `posix_fadvise`.
