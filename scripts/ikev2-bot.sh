#!/usr/bin/env bash
set -euo pipefail

# Helper for Telegram bot to manage strongSwan IKEv2(EAP) users.
# Usage:
#   ikev2-bot.sh ensure-user <username> <password>
#   ikev2-bot.sh revoke-user <username>
#   ikev2-bot.sh ca-der-b64

SECRETS_FILE="/etc/ipsec.secrets"

find_ca_pem() {
  local p
  for p in "${IKEV2_CA_CERT_PATH:-}" \
           "/root/ca/ca-cert.crt" \
           "/etc/ipsec.d/cacerts/ca-cert.pem" \
           "/etc/ipsec.d/cacerts/ca-cert.crt"; do
    [[ -n "$p" && -f "$p" ]] && { echo "$p"; return 0; }
  done
  return 1
}

reread_secrets() {
  if command -v ipsec >/dev/null 2>&1; then
    # stroke-based strongSwan
    ipsec rereadsecrets >/dev/null 2>&1 || true
  fi
}

ensure_user() {
  local user="$1"
  local pass="$2"

  # very basic validation (avoid breaking file)
  if [[ ! "$user" =~ ^[A-Za-z0-9._-]{1,32}$ ]]; then
    echo "Invalid username format" >&2
    exit 2
  fi
  if [[ ! "$pass" =~ ^[A-Za-z0-9]{8,64}$ ]]; then
    echo "Invalid password format" >&2
    exit 2
  fi

  if [[ ! -f "$SECRETS_FILE" ]]; then
    touch "$SECRETS_FILE"
  fi
  chmod 600 "$SECRETS_FILE" || true

  # Remove old entry for this user (if any) and append fresh one
  local tmp
  tmp="$(mktemp)"
  awk -v u="$user" 'BEGIN{IGNORECASE=0} $0 ~ "^"u"[[:space:]]*:" {next} {print}' "$SECRETS_FILE" >"$tmp"
  echo "$user : EAP \"$pass\"" >>"$tmp"
  mv "$tmp" "$SECRETS_FILE"

  reread_secrets
}

revoke_user() {
  local user="$1"
  if [[ ! -f "$SECRETS_FILE" ]]; then
    exit 0
  fi
  local tmp
  tmp="$(mktemp)"
  awk -v u="$user" '$0 ~ "^"u"[[:space:]]*:" {next} {print}' "$SECRETS_FILE" >"$tmp"
  mv "$tmp" "$SECRETS_FILE"
  chmod 600 "$SECRETS_FILE" || true
  reread_secrets
}

ca_der_b64() {
  local ca
  ca="$(find_ca_pem)" || { echo "CA certificate not found" >&2; exit 1; }
  openssl x509 -in "$ca" -outform der | base64 -w0
}

cmd="${1:-}"
case "$cmd" in
  ensure-user)
    ensure_user "${2:-}" "${3:-}"
    ;;
  revoke-user)
    revoke_user "${2:-}"
    ;;
  ca-der-b64)
    ca_der_b64
    ;;
  *)
    echo "Usage: $0 ensure-user <username> <password> | revoke-user <username> | ca-der-b64" >&2
    exit 1
    ;;
esac
