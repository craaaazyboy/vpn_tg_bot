#!/usr/bin/env bash
set -euo pipefail
if [ $# -ne 2 ]; then
  echo "Usage: $0 <wg.tar.gz> <pg.tar.gz>"; exit 1;
fi
WG=$1
PG=$2

echo "[+] Restoring wg-data volume…"
docker volume create $(docker compose ls -q)_wg-data
cat "$WG" | docker run --rm -i -v $(docker compose ls -q)_wg-data:/dst alpine tar xzf - -C /dst

echo "[+] Restoring pg-data volume…"
docker volume create $(docker compose ls -q)_pg-data
cat "$PG" | docker run --rm -i -v $(docker compose ls -q)_pg-data:/dst alpine tar xzf - -C /dst

echo "[✓] Restore complete"