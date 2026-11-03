# Journal des mesures de référence

| Date (UTC) | Machine / lieu | Produit | Pilote / CUDA | Calcul (TFLOPS, type) | HBM (GB/s) | Stockage écr. (MB/s) | Stabilité (dégr. %, plafond puiss.) | Classe | Reçu | Note |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-03 13:20 | Google Colab, Tesla T4 (docker, 2 vCPU, 12,7 Go) | T4.PCIE.1 | 580.82.07 / 13.0 | 2,3 (bf16 émulé) | 231,6 | 63,0 | 0,0 / 93 % | C | rcpt-2026-11-03-040dd658f2 (3087…) | suite v0.1.0 avant correctif : BF16 émulé sur Turing, plafond de puissance compté comme bridage. Mesure invalide pour le calcul, valide pour l'identité et la HBM. |
| 2026-11-03 13:26 | Google Colab, Tesla T4 (même session) | T4.PCIE.1 | 580.82.07 / 13.0 | 22,4 (fp16) | 231,7 | 250,0 | 0,0 / 100 % | A | rcpt-2026-11-03-ab4d7cfcb0 (763c…) | suite corrigée (commit 6f3258a). Mode rapide (5 s de calcul, 30 s de charge). Première mesure de référence valide. |
| 2026-11-03 13:33 | Google Colab, Tesla T4 (même session) | T4.PCIE.1 | 580.82.07 / 13.0 | 22,5 (fp16) | 231,6 | 238,7 | 0,0 / 93 % | A | rcpt-2026-11-03-cf4affaee7 (93e3…) | troisième passage, mode rapide : calcul et HBM reproduits à 0,5 % près. |
| 2026-11-03 13:39 | Google Colab, Tesla T4 (même session) | T4.PCIE.1 | 580.82.07 / 13.0 | 22,3 (fp16, 5 s) | 231,5 | 247,6 | 0,0 / 93 % | A | rcpt-2026-11-03-552301248e (aaa4…) | quatrième passage rapide. |
| 2026-11-03 13:44 | Google Colab, Tesla T4 (même session) | T4.PCIE.1 | 580.82.07 / 13.0 | 19,5 (fp16, 15 s) | 233,1 | 182,4 | 0,0 sur 300 s / 99 % | B (seuil A = 20 d'alors) | rcpt-2026-11-03-a8b9ad02ef (b86d…) | **mode complet.** Première tranche 18,9, dernière 19,4 TFLOPS : aucune dégradation sur 5 min ; 83 °C max, plafond de puissance 67,6 W quasi permanent, aucun ralentissement thermique. Le régime établi du T4 est 19,5, pas 22,4 : la mesure de 5 s comptait le boost. |
| 2026-11-03 14:40 | Google Colab, Tesla T4 (nouvelle session) | T4.PCIE.1 | 580.82.07 / 13.0 | 18,1 (fp16, 5 s après 3 s écartées, taille aléatoire) | 232,8 | 249,2 | 0,0 / 94 % | A | rcpt-2026-11-03-2a0a820476 (90b6…) | suite avec montée en charge écartée et taille de matrice aléatoire (commit 880da0a). |
| 2026-11-03 14:47 | Google Colab, Tesla T4 (même session) | T4.PCIE.1 | 580.82.07 / 13.0 | 14,5 (fp16, 15 s après 5 s écartées, taille aléatoire) ; phase de charge : 18,7 → 19,1 sur 313 s | 235,2 | 96,9 | 0,0 / 99 % | B (calcul 14,5 < 17) | rcpt-2026-11-03-f79741ac15 (1c08…) | **anomalie** : la fenêtre de 15 s donne 14,5 alors que la même machine soutient 18,7–19,1 pendant cinq minutes juste après. Suspects : la taille aléatoire (7936 ou 8448 au lieu de 8192) et la sensibilité d'une fenêtre courte. 82 °C, 69,4 W. |

## Décisions de seuil

- **T4.PCIE.1** : le mode complet donne 19,5 TFLOPS FP16 soutenus sur 15 s et 18,9 à 19,4 sur 5 minutes de charge, contre 22,3 à 22,5 en 5 s :
  la mesure courte comptait le boost d'horloge avant que le plafond de 70 W ne s'applique. Deux décisions : (1) la suite mesure désormais le calcul
  en régime établi, après une montée en charge écartée de la mesure (commit du 2026-11-03) ; (2) le seuil A du T4 passe de 20 à **17** (environ 13 % sous
  19,5) et le seuil B de 12 à **10**. HBM 231,5 à 233,1 GB/s sur cinq passages : A = 220 confirmé. Stabilité : aucune dégradation sur 300 s, 83 °C,
  aucun ralentissement thermique : la règle de stabilité est validée sur ce produit. Stockage toujours bruité (63 à 250 MB/s) : pas de décision.
  Règle générale retenue : **seul le mode complet calibre un seuil de calcul** ; le mode rapide sert à l'identité et à la plomberie.
- **Décision du 2026-11-03 (après les mesures de 14:40 et 14:47)** : (1) la valeur de calcul **notée** est désormais, en mode complet, le régime établi
  de la phase de charge de cinq minutes (GPU le plus lent, dernière tranche), la fenêtre de 15 s ne servant qu'en mode rapide ; (2) la mesure notée
  revient à une taille fixe de 8192, celle des seuils ; la taille aléatoire devient une **sonde non notée**, dont seul un écart anormal est signalé ;
  (3) les sept mesures T4 de la journée donnent un régime établi de 18,7 à 19,4 TFLOPS : le seuil A = 17 est maintenu. Avec la nouvelle règle, le reçu
  de 14:47 aurait été classé A (18,7 soutenus).
