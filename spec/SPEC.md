# Contrat de livraison vérifiable de compute — Spécification v0.1

*Nom de travail du protocole : Verified Compute Delivery (VCD).*
*Statut : brouillon public, version 0.1.0, novembre 2026. Licence : CC BY 4.0 (texte et schémas), Apache-2.0 (code de référence).*

## 0. Ce que ce document fixe

Une heure de compute n'a pas de prix parce qu'elle n'a pas d'unité : la même « H100 » vaut 1,92 ou 3,20 dollars selon le vendeur, l'engagement et ce qui est réellement livré. Ce document définit l'unité et le contrat qui permettent de la vendre, de la livrer et de la régler sans que les parties aient à se faire confiance :

1. **des produits** (ce qu'on commande), définis par une identité matérielle *et* par des seuils de travail mesuré ;
2. **des classes de qualité** (A, B, C, FAIL), attribuées à une livraison par une mesure, pas par une déclaration ;
3. **un contrat de livraison** : qui livre quoi, quand, à quel prix, avec quel collatéral, et ce qui se passe si la livraison n'est pas conforme ;
4. **un reçu de livraison** signé par un oracle de mesure, qui constate la conformité ;
5. **les règles de règlement** : la vie d'un contrat et le traitement de chaque incident.

Le protocole ne possède ni machines ni argent. Il fait respecter des contrats.

## 1. Vocabulaire

| Terme | Définition |
|---|---|
| Vendeur | Celui qui s'engage à livrer la ressource (neocloud, hôte, revendeur d'usage, agent) |
| Acheteur | Celui qui paie la ressource (équipe, agent, teneur de marché) |
| Oracle | Un service indépendant qui se connecte à la ressource livrée, exécute la suite de conformité et signe un reçu |
| Escrow | Le tiers (contrat intelligent ou prestataire agréé) qui détient le paiement de l'acheteur et le collatéral du vendeur jusqu'au règlement |
| Produit | Une classe de ressource normalisée (section 2), par exemple `H100.SXM.8` |
| Classe | Le résultat de la mesure d'une livraison : A, B, C ou FAIL (section 3) |
| Fenêtre | La période de livraison, bornée en UTC |
| Reçu | Le document signé par l'oracle décrivant la mesure et le verdict (section 5) |

## 2. Produits

Un produit est identifié par `FAMILLE.FORME.NOMBRE` : la famille de GPU (`H100`, `H200`, `B200`, `A100`, `RTX4090`, `T4`, `P100`), la forme (`SXM` ou `PCIE`), le nombre de GPU livrés dans un même nœud (`1` ou `8`). Les produits à 8 GPU exigent une interconnexion NVLink mesurée.

Les produits et leurs seuils sont dans `products.json`. Pour chaque produit :

- **identité** : famille, forme, nombre de GPU, mémoire minimale par GPU, NVLink requis ou non ;
- **seuils de travail**, chacun avec une valeur A et une valeur B : calcul soutenu (TFLOPS en BF16, ou FP16 sur les GPU sans BF16 natif, ce que le reçu précise), bande passante mémoire (GB/s), interconnexion (GB/s de bande passante de bus en all-reduce), stabilité (dégradation maximale en % sur la durée de charge), réseau (Mbit/s descendant), stockage (MB/s en écriture).

Les seuils de la v0.1 sont **provisoires** : ils viennent des performances publiées et de mesures communautaires. Ils doivent être recalés sur des machines de référence mesurées avec la suite elle-même avant d'être opposables. Toute modification de seuil incrémente la version mineure de la spécification et s'applique seulement aux contrats conclus après publication.

Un produit se définit par ce qui est mesuré, pas par le nom du matériel : un nœud vendu comme `H100.SXM.8` dont l'interconnexion mesure 40 GB/s n'est pas un `H100.SXM.8` de classe A, quel que soit l'autocollant.

## 3. Classes

La classe d'une livraison est calculée par l'oracle à partir du reçu, selon la règle suivante, appliquée dans l'ordre :

| Classe | Règle |
|---|---|
| **FAIL** | identité non conforme (famille, forme, nombre de GPU ou mémoire sous le minimum), machine inaccessible dans le délai, ou suite de conformité interrompue |
| **A** | identité conforme et toutes les mesures *primaires* au seuil A (calcul, mémoire, interconnexion si le produit l'exige, stabilité) ; les mesures *secondaires* (réseau, stockage) au seuil A ou non mesurées pour cause externe documentée |
| **B** | identité conforme et toutes les mesures primaires au moins au seuil B |
| **C** | identité conforme et au moins une mesure primaire sous le seuil B |

Un contrat précise une **classe minimale**. La livraison est **conforme** si la classe mesurée est supérieure ou égale à la classe minimale (A > B > C ; FAIL n'est jamais conforme).

## 4. Le contrat de livraison

Schéma : `schemas/contract.schema.json`. Exemple : `examples/contract.example.json`.

| Champ | Contenu | Règle |
|---|---|---|
| `contract_id` | identifiant unique | généré par l'escrow |
| `product` | un produit de `products.json` | obligatoire |
| `min_class` | A, B ou C | obligatoire |
| `quantity` | nombre d'unités du produit (nœuds) | entier ≥ 1 |
| `window.start`, `window.end` | début et fin de la fenêtre, ISO 8601 UTC | fin > début ; durée minimale 1 heure |
| `price_per_gpu_hour` | prix unitaire dans la devise du contrat | ≥ 0 |
| `currency` | `USD`, `EUR` ou `USDC` | |
| `notional` | prix × GPU × heures | calculé, vérifié par l'escrow |
| `seller`, `buyer` | identités (clé publique ou identifiant d'escrow) | vérifiées à l'admission (KYB) |
| `collateral_pct` | collatéral du vendeur en % du notionnel | ≥ 10 % ; 20 % par défaut |
| `delivery_deadline_minutes` | délai maximal entre `window.start` et l'accès effectif | 15 par défaut |
| `oracle` | identité de l'oracle (clé publique) et version de la suite exigée | obligatoire |
| `cancellation` | grille de remboursement de l'acheteur | voir 6.4 |
| `transferable` | `false` en v0.1 | |
| `spec_version` | version de ce document | obligatoire |

Le contrat est **à livraison obligatoire**. Il ne prévoit aucun règlement en espèces à la place de la livraison : le seul flux monétaire est le paiement de la ressource, et, en cas de défaut, l'indemnisation prise sur le collatéral.

## 5. Le reçu de livraison

Schéma : `schemas/receipt.schema.json`. Exemple : `examples/receipt.example.json`.

Le reçu est produit par la suite de conformité (code de référence : `oracle/`). Il contient :

- `receipt_id`, `contract_id` (facultatif pour une mesure d'indice), `spec_version`, `suite_version` ;
- `product` et `min_class` demandés ;
- `timing` : heure de commande, heure d'accès effectif, délai, début et fin de la mesure ;
- `identity` : GPU (modèle, mémoire, nombre, pilote, forme, liens NVLink), CPU, RAM, disque, virtualisation, noyau ;
- `measurements` : calcul par GPU et agrégé, bande passante mémoire, interconnexion, réseau, stockage, stabilité (séries temporelles résumées : fréquences, températures, puissance, raisons de bridage) ;
- `thresholds` : les seuils appliqués, copiés de `products.json` au moment de la mesure ;
- `verdict` : classe mesurée, conformité à la classe minimale, raisons ;
- `oracle` : identifiant de clé, clé publique ed25519 ;
- `hash` : SHA-256 du reçu canonique (JSON trié, sans espaces, sans les champs `hash` et `signature`) ;
- `signature` : signature ed25519 du `hash` par la clé de l'oracle.

Un reçu est **vérifiable par quiconque** possède la clé publique de l'oracle. Les reçus sont publiés dans un journal public en ajout seul (en v0.1 : un dépôt git, un fichier par reçu), de sorte que l'historique de livraison de chaque vendeur et la méthode de chaque oracle soient auditables.

## 6. Règlement

### 6.1 États du contrat

```
CREATED ──(acheteur paie, vendeur dépose le collatéral)──► FUNDED
FUNDED ──(window.start)──► DELIVERY_PENDING
DELIVERY_PENDING ──(accès effectif ≤ deadline)──► PROVISIONED
DELIVERY_PENDING ──(deadline dépassé)──► DEFAULTED_SELLER
PROVISIONED ──(reçu : conforme)──► ACCEPTED
PROVISIONED ──(reçu : non conforme)──► REJECTED
ACCEPTED ──(window.end)──► SETTLED
REJECTED ──► DEFAULTED_SELLER
FUNDED ──(acheteur annule avant window.start)──► CANCELLED_BUYER
```

### 6.2 Flux d'argent

| État final | Paiement de l'acheteur | Collatéral du vendeur |
|---|---|---|
| SETTLED | versé au vendeur, moins les frais du protocole et de l'oracle | restitué au vendeur |
| DEFAULTED_SELLER | remboursé intégralement à l'acheteur | indemnité à l'acheteur : le surcoût de remplacement, plafonné au collatéral, calculé sur le prix de référence du produit au moment du défaut ; le solde restitué au vendeur |
| CANCELLED_BUYER | remboursé selon la grille 6.4 ; la retenue va au vendeur | restitué au vendeur |

### 6.3 Incidents

| Incident | Traitement |
|---|---|
| Vendeur en retard mais livre avant le double du délai | PROVISIONED ; pénalité de retard égale au prix des minutes perdues, prise sur le collatéral |
| Oracle injoignable | un second oracle agréé par le contrat mesure ; à défaut, le contrat passe en ACCEPTED après la fenêtre si l'acheteur ne conteste pas, et la mesure est marquée « non constatée » dans le journal |
| Acheteur ne se connecte jamais | sans effet : la ressource a été livrée et vérifiée, le contrat se règle |
| Panne pendant la fenêtre | hors périmètre de la v0.1 (la conformité est constatée à la livraison) ; l'acheteur signale, le journal l'enregistre contre la réputation du vendeur |
| Contestation du reçu | la partie qui conteste demande une contre-mesure par un second oracle dans l'heure ; le reçu majoritaire l'emporte ; le coût de la contre-mesure va à la partie perdante |

### 6.4 Grille d'annulation par défaut

| Moment de l'annulation par l'acheteur | Remboursement |
|---|---|
| Plus de 24 heures avant `window.start` | 90 % |
| Entre 24 heures et le début de la fenêtre | 75 % |
| Pendant la fenêtre, au prorata des heures non commencées | 60 % |

Une grille différente peut être convenue dans le contrat. En v0.1, la retenue va au vendeur ; une part peut être redirigée vers le protocole par une version ultérieure.

### 6.5 Frais

Les frais sont un paramètre du contrat, exprimés en % du notionnel, prélevés sur le paiement au moment du règlement et répartis entre le protocole et l'oracle selon une clé fixée par le contrat. La v0.1 ne fixe pas leur niveau.

## 7. Oracles

Un oracle est admis s'il publie sa méthode, exécute une suite de conformité d'une version acceptée par le contrat, et signe ses reçus avec une clé dont la publique est enregistrée. Un oracle ne doit être ni vendeur ni acheteur du contrat qu'il mesure. Les reçus d'un oracle sont auditables dans le journal ; un oracle dont les reçus sont contredits par des contre-mesures perd son agrément.

La suite de conformité de référence est décrite dans `oracle/README.md`. Elle dure environ dix minutes en mode complet (cinq minutes de charge de stabilité) et deux minutes en mode rapide, réservé à la calibration.

## 8. Ce que la v0.1 ne couvre pas

- la transférabilité des positions (marché secondaire) ;
- le règlement en espèces sur un indice ;
- la conformité en continu pendant la fenêtre (uptime), seulement la conformité à la livraison ;
- les produits multi-nœuds (interconnexion inter-nœuds) ;
- la tokenisation du contrat.

Ces points sont réservés à des versions ultérieures et ne doivent pas être supposés par les implémentations.

## 9. Gouvernance du texte

Le texte et les schémas sont publiés sous CC BY 4.0. Toute personne peut implémenter la spécification sans autorisation. Les modifications se font par proposition publique ; les changements de seuils ou de règle de classe incrémentent la version mineure ; les changements de schéma incompatibles incrémentent la version majeure. Un contrat référence toujours la version exacte qui lui est applicable.
