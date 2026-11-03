#!/usr/bin/env bash
# Exécute la suite de conformité VCD sur une machine fraîchement livrée.
# Usage : bash run_oracle_remote.sh [--product H100.SXM.8 --min-class A | --detect] [autres options de oracle.run]
# Variables : VCD_REPO (dépôt git), VCD_DIR (dossier de travail), VCD_NONCE (défi du contrôleur : le lot est alors non signé)
set -euo pipefail
REPO="${VCD_REPO:-https://github.com/benjamindarmian/vcd-protocol.git}"
DIR="${VCD_DIR:-$HOME/vcd}"
if [ ! -d "$DIR/oracle" ]; then
  git clone -q "$REPO" "$DIR"
else
  (cd "$DIR" && git pull -q || true)
fi
cd "$DIR"
python3 -m pip install -q nvidia-ml-py cryptography >/dev/null 2>&1 || python3 -m pip install -q --break-system-packages nvidia-ml-py cryptography
if ! python3 -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
  echo "torch avec CUDA absent : installation (quelques minutes)…" >&2
  python3 -m pip install -q torch --index-url https://download.pytorch.org/whl/cu124 || python3 -m pip install -q --break-system-packages torch --index-url https://download.pytorch.org/whl/cu124
fi
ARGS=("$@")
if [ ${#ARGS[@]} -eq 0 ]; then ARGS=(--detect --purpose calibration); fi
python3 -m oracle.run "${ARGS[@]}"
