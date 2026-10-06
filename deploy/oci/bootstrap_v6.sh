#!/usr/bin/env bash
set -euo pipefail

echo "== Trader Agent V6 · preparación OCI Ubuntu =="
if [[ $EUID -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

$SUDO apt-get update
$SUDO apt-get install -y git curl ca-certificates docker.io docker-compose-v2
$SUDO systemctl enable --now docker
$SUDO usermod -aG docker "$USER" || true

echo
echo "Docker:"
$SUDO docker --version
$SUDO docker compose version

echo
echo "Preflight Binance público:"
python3 deploy/oci/preflight_v6.py || {
  echo
  echo "Preflight bloqueado. NO configure API keys y NO active TRADER_ACCESS_CONFIRMED."
  exit 2
}

echo
echo "Preflight exitoso."
echo "Siguiente: cp deploy/oci/v6.env.example .env"
echo "Edite .env localmente en la VM; nunca lo suba a GitHub."
