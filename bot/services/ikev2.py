from __future__ import annotations

import base64
import json
import secrets
import shlex
import string
import uuid

import asyncssh

from settings import settings


import re

def generate_username(tg_user: int | str, device_name: str = "", suffix_len: int = 4) -> str:
    """
    Генерирует username для strongSwan.
    Пример: u123456789_macbook_air_ab12
    """
    # tg_user может быть int (telegram id) или строка
    base = str(tg_user)

    dev = (device_name or "").strip().lower()
    dev = re.sub(r"[^a-z0-9]+", "_", dev)  # только латиница/цифры/_
    dev = dev.strip("_")[:20]              # ограничим длину

    alphabet = string.ascii_lowercase + string.digits
    suf = "".join(secrets.choice(alphabet) for _ in range(max(2, int(suffix_len))))

    if dev:
        return f"u{base}_{dev}_{suf}"
    return f"u{base}_{suf}"



def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _ssh_host() -> str:
    return settings.WG_SSH_HOST


def _ssh_user() -> str:
    return settings.WG_SSH_USER


def _ssh_key() -> str:
    return settings.WG_SSH_KEY


async def _ssh_run(cmd: str) -> str:
    host = _ssh_host()
    user = _ssh_user()
    key_path = _ssh_key()
    async with asyncssh.connect(
        host,
        username=user,
        client_keys=[key_path],
        known_hosts=None,
    ) as conn:
        res = await conn.run(cmd, check=False)
        if res.exit_status != 0:
            raise RuntimeError(
                f"SSH command failed (exit={res.exit_status}): {cmd}\nSTDERR: {res.stderr.strip()}"
            )
        return res.stdout


def _helper() -> str:
    return getattr(settings, "IKEV2_SERVER_MANAGER", None) or "/usr/local/sbin/ikev2.sh"



async def ensure_user_on_server(username: str, password: str) -> None:
    """Create or update an EAP user on the VPN server."""
    cmd = "sudo " + " ".join(
        [
            shlex.quote(_helper()),
            "ensure-user",
            shlex.quote(username),
            shlex.quote(password),
        ]
    )
    await _ssh_run(cmd)


async def revoke_user_on_server(username: str) -> None:
    cmd = "sudo " + " ".join(
        [shlex.quote(_helper()), "revoke-user", shlex.quote(username)]
    )
    await _ssh_run(cmd)


async def fetch_ca_cert_der_b64() -> str:
    """Return Base64 of CA certificate in DER form (no newlines)."""
    cmd = "sudo " + " ".join([shlex.quote(_helper()), "ca-der-b64"])
    out = (await _ssh_run(cmd)).strip()
    if not out:
        raise RuntimeError("Empty CA DER base64 received from server")
    # basic sanity check: base64 decode must work
    try:
        base64.b64decode(out + "===", validate=False)
    except Exception as e:
        raise RuntimeError(f"Invalid base64 CA cert from server: {e}")
    return out


def build_android_sswan(
    *,
    profile_name: str,
    server_addr: str,
    remote_id: str,
    username: str,
    password: str,
    ca_cert_der_b64: str,
) -> bytes:
    """Generate a .sswan JSON profile for strongSwan Android client."""
    data = {
        "uuid": str(uuid.uuid4()),
        "name": profile_name,
        "type": "ikev2-eap",
        "remote": {
            "addr": server_addr,
            "id": remote_id,
            "cert": ca_cert_der_b64,
        },
        "local": {
            "eap_id": username,
            "shared_secret": password,
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def build_ios_mobileconfig(
    *,
    profile_name: str,
    server_addr: str,
    remote_id: str,
    username: str,
    password: str,
    ca_cert_der_b64: str,
) -> bytes:
    """Generate an iOS .mobileconfig with embedded CA cert and IKEv2 (EAP) config."""

    # Apple config profiles are plist (XML). Keep it minimal and compatible.
    profile_uuid = str(uuid.uuid4())
    cert_uuid = str(uuid.uuid4())
    vpn_uuid = str(uuid.uuid4())

    # iOS expects CA cert payload in DER (base64).
    # For IKEv2+EAP iOS uses XAuthName/XAuthPassword fields.
    plist = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadType</key><string>com.apple.security.root</string>
      <key>PayloadVersion</key><integer>1</integer>
      <key>PayloadIdentifier</key><string>ikev2.ca.{cert_uuid}</string>
      <key>PayloadUUID</key><string>{cert_uuid}</string>
      <key>PayloadDisplayName</key><string>VPN CA</string>
      <key>PayloadCertificateFileName</key><string>ca-cert.crt</string>
      <key>PayloadContent</key><data>{ca_cert_der_b64}</data>
    </dict>
    <dict>
      <key>PayloadType</key><string>com.apple.vpn.managed</string>
      <key>PayloadVersion</key><integer>1</integer>
      <key>PayloadIdentifier</key><string>ikev2.vpn.{vpn_uuid}</string>
      <key>PayloadUUID</key><string>{vpn_uuid}</string>
      <key>PayloadDisplayName</key><string>{profile_name}</string>
      <key>UserDefinedName</key><string>{profile_name}</string>
      <key>VPNType</key><string>IKEv2</string>
      <key>IKEv2</key>
      <dict>
        <key>RemoteAddress</key><string>{server_addr}</string>
        <key>RemoteIdentifier</key><string>{remote_id}</string>
        <key>AuthenticationMethod</key><string>None</string>
        <key>ExtendedAuthEnabled</key><true/>
        <key>XAuthName</key><string>{username}</string>
        <key>XAuthPassword</key><string>{password}</string>
        <key>DeadPeerDetectionRate</key><string>Medium</string>
        <key>EnablePFS</key><true/>
        <key>DisableMOBIKE</key><false/>
      </dict>
    </dict>
  </array>
  <key>PayloadDisplayName</key><string>{profile_name}</string>
  <key>PayloadIdentifier</key><string>ikev2.profile.{profile_uuid}</string>
  <key>PayloadOrganization</key><string>VPN</string>
  <key>PayloadRemovalDisallowed</key><false/>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadUUID</key><string>{profile_uuid}</string>
  <key>PayloadVersion</key><integer>1</integer>
</dict>
</plist>
"""
    return plist.encode("utf-8")
