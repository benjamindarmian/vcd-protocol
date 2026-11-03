# Journal des mesures de référence

| Date (UTC) | Machine / lieu | Produit | Pilote / CUDA | Calcul (TFLOPS, type) | HBM (GB/s) | Stockage écr. (MB/s) | Stabilité (dégr. %, plafond puiss.) | Classe | Reçu | Note |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-03 13:20 | Google Colab, Tesla T4 (docker, 2 vCPU, 12,7 Go) | T4.PCIE.1 | 580.82.07 / 13.0 | 2,3 (bf16 émulé) | 231,6 | 63,0 | 0,0 / 93 % | C | rcpt-2026-11-03-040dd658f2 (3087…) | suite v0.1.0 avant correctif : BF16 émulé sur Turing, plafond de puissance compté comme bridage. Mesure invalide pour le calcul, valide pour l'identité et la HBM. |
| 2026-11-03 13:26 | Google Colab, Tesla T4 (même session) | T4.PCIE.1 | 580.82.07 / 13.0 | 22,4 (fp16) | 231,7 | 250,0 | 0,0 / 100 % | A | rcpt-2026-11-03-ab4d7cfcb0 (763c…) | suite corrigée (commit 6f3258a). Mode rapide (5 s de calcul, 30 s de charge). Première mesure de référence valide. |

## Décisions de seuil

- **T4.PCIE.1** : calcul mesuré 22,4 TFLOPS FP16 (une mesure, mode rapide) : le seuil A = 20 tient (11 % sous la mesure), B = 12 inchangé.
  HBM 231,7 GB/s, reproductible à 0,1 près entre deux passages : seuil A = 220 confirmé, B = 150. Stockage : 63 puis 250 MB/s sur le même disque
  en six minutes, le disque Colab est partagé et bruité ; pas de décision avant d'autres mesures. À refaire en mode complet (300 s) pour la stabilité.
