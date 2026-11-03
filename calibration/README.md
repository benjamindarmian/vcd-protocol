# Calibration des seuils

Les seuils de `spec/products.json` ne valent que par les mesures qui les soutiennent. Ce dossier tient le
journal des mesures de référence faites avec la suite (`oracle/`), machine par machine, et les décisions de
seuil qui en découlent. Règle : un seuil A est fixé environ 10 % sous la médiane des mesures de référence sur
des machines saines ; un seuil B à 60 % du seuil A, sauf raison documentée.

Procédure : lancer `python -m oracle.run --detect --purpose calibration` (mode complet si possible), conserver
le reçu JSON dans `calibration/receipts/`, et ajouter une ligne à `log.md`.

Les mesures secondaires (réseau, stockage) sont bruitées sur les machines partagées : on les relève à chaque
passage mais on ne les utilise pour un seuil qu'après plusieurs mesures.
