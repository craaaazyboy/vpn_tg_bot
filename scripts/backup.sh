#!/usr/bin/env bash
set -euo pipefail
TS=$(date +"%Y-%m-%d-%H%M%S")
mkdir -p backups

echo "[+] Dumping wg-data volume…"
docker run --rm -v $(docker compose ls -q)_wg-data:/src alpine tar czf - -C /src . >"backups/${TS}-wg-data.tar.gz"

echo "[+] Dumping pg-data volume…"
docker run --rm -v $(docker compose ls -q)_pg-data:/src alpine tar czf - -C /src . >"backups/${TS}-pg-data.tar.gz"

echo "[✓] Backups saved to ./backups"