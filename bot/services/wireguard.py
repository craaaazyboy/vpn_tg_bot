import asyncssh, re, datetime as dt, logging
from typing import Tuple, List, Dict, Optional
from settings import settings

WG_IFACE = "wg0"
NET_PREFIX = "10.66.66"

async def _ssh():
    return await asyncssh.connect(
        settings.WG_SSH_HOST,
        username=settings.WG_SSH_USER,
        client_keys=[settings.WG_SSH_KEY],
        known_hosts=None,
    )

async def get_next_free_ip(conn) -> int:
    result = await conn.run(
        f"grep 'AllowedIPs' /etc/wireguard/{WG_IFACE}.conf | "
        "grep -oP '10\\.66\\.66\\.\\K\\d+' | sort -n | tail -1", check=False
    )
    last = (result.stdout or "").strip()
    return int(last)+1 if last.isdigit() else 2

async def add_peer(device_name:str) -> Tuple[str,int,str,str]:
    """
    Возвращает: (pubkey, ip_octet, allowed_cidr, conf_path)
    Работает через angristan/wireguard-install.sh (пункт 1: Add).
    """
    async with await _ssh() as conn:
        before = set((await conn.run(f"wg show {WG_IFACE} peers", check=False)).stdout.split())
        ip_octet = await get_next_free_ip(conn)

        cmd = f"printf '1\n{device_name}\n{ip_octet}\n{ip_octet}\n' | bash ~/wireguard-install.sh"
        await conn.run(cmd, check=True)

        after = set((await conn.run(f"wg show {WG_IFACE} peers", check=True)).stdout.split())
        new_peers = list(after - before)
        if not new_peers:
            raise RuntimeError("Не удалось получить новый публичный ключ пира")
        pubkey = new_peers[0]

        conf_path = f"/root/{WG_IFACE}-client-{device_name}.conf"
        allowed_cidr = f"{NET_PREFIX}.{ip_octet}/32"
        return pubkey, ip_octet, allowed_cidr, conf_path

async def fetch_client_conf_and_qr(conf_path:str) -> tuple[str, bytes]:
    async with await _ssh() as conn:
        conf = (await conn.run(f"cat {conf_path}", check=True)).stdout
        qr_png = (await conn.run(f"qrencode -t png -o - < {conf_path}", encoding=None, check=True)).stdout
        return conf, qr_png

async def wg_dump_stats() -> List[Dict]:
    """
    wg show wg0 dump
    peer line format:
    <pubkey>\t<preshared>\t<endpoint>\t<allowed_ips>\t<latest_handshake>\t<rx>\t<tx>\t<keepalive>
    """
    async with await _ssh() as conn:
        out = (await conn.run(f"wg show {WG_IFACE} dump", check=True)).stdout.strip().splitlines()
    items = []
    for line in out[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        pubkey = parts[0]
        hs = int(parts[4])
        rx = int(parts[5]); tx = int(parts[6])
        last_hs = dt.datetime.utcfromtimestamp(hs) if hs > 0 else None
        items.append({"pubkey": pubkey, "rx_bytes": rx, "tx_bytes": tx, "last_handshake": last_hs})
    return items

def _sh_escape(s: str) -> str:
    return s.replace("'", r"'\''")

async def _find_index_by_device_name(conn, device_name: str) -> Optional[int]:
    """
    Возвращает 1-based индекс клиента в меню Revoke по имени (### Client <NAME>) или None.
    """
    target = _sh_escape(device_name)
    find_index_script = r"""
set -euo pipefail
CONF=""
if   [ -f /etc/wireguard/wg0.conf ]; then CONF="/etc/wireguard/wg0.conf";
elif [ -f /config/wg0.conf ];       then CONF="/config/wg0.conf";
else echo "wg0.conf not found" >&2; exit 98; fi

awk -v TARGET="$TARGET_NAME" '
  BEGIN { i=0 }
  /^[[:space:]]*###[[:space:]]+Client[[:space:]]+/ {
    line=$0
    sub(/^[[:space:]]*###[[:space:]]+Client[[:space:]]+/, "", line)
    gsub(/[[:space:]\r]+$/, "", line)
    i++
    if (line==TARGET) { print i; found=1; exit 0 }
  }
  END { if (!found) exit 2 }
' "$CONF"
"""
    # записываем и запускаем временный скрипт с переменной окружения TARGET_NAME
    await conn.run(f"cat > /tmp/find_peer_index.sh <<'EOF'\n{find_index_script}\nEOF", check=True)
    res = await conn.run(f"TARGET_NAME='{target}' bash /tmp/find_peer_index.sh", check=False)
    if res.exit_status == 0 and res.stdout.strip().isdigit():
        return int(res.stdout.strip())
    logging.error("find_index failed for '%s': rc=%s, stderr=%s", device_name, res.exit_status, res.stderr)
    return None

async def ssh_revoke_by_device_or_pubkey(device_name: Optional[str], pubkey: Optional[str]) -> None:
    """
    Удаляет peer через wireguard-install.sh (п.3 Revoke).
    Предпочтительно по device_name (как в ### Client <NAME>), иначе пытается сопоставить по pubkey.
    """
    async with await _ssh() as conn:
        idx = None
        if device_name:
            idx = await _find_index_by_device_name(conn, device_name)

        # если по имени не нашли, пробуем сопоставить имя через pubkey (парсим wg0.conf)
        if idx is None and pubkey:
            res = await conn.run("cat /etc/wireguard/wg0.conf", check=True)
            current_name = None
            current_key = None
            i = 0
            for line in res.stdout.splitlines():
                m1 = re.match(r"^###\s+Client\s+(.+)$", line)
                if m1:
                    current_name = m1.group(1).strip()
                    current_key = None
                    i += 1
                    continue
                m2 = re.match(r"^\s*PublicKey\s*=\s*(.+)\s*$", line)
                if m2 and current_name:
                    current_key = m2.group(1).strip()
                    if current_key == pubkey:
                        idx = i
                        device_name = current_name
                        break

        if idx is None:
            raise RuntimeError("Не удалось найти индекс клиента для Revoke")

        # Revoke: пункт 3 + индекс
        cmd_revoke = f"printf '3\n{idx}\n' | bash ~/wireguard-install.sh"
        res2 = await conn.run(cmd_revoke, check=False)
        if res2.exit_status != 0:
            raise RuntimeError(f"wireguard-install.sh вернул код {res2.exit_status}: {res2.stderr}")

async def list_peers_from_wgconf() -> list[dict]:
    """
    Читает /etc/wireguard/wg0.conf, возвращает список:
    [{device_name, public_key, allowed_cidr}]
    """
    pattern_client = re.compile(r"^###\s+Client\s+(.+)$")
    pattern_pub    = re.compile(r"^\s*PublicKey\s*=\s*(.+)\s*$")
    pattern_ips    = re.compile(r"^\s*AllowedIPs\s*=\s*([^,\s]+)")

    items: list[dict] = []
    cur: dict = {}

    async with await _ssh() as conn:
        res = await conn.run("cat /etc/wireguard/wg0.conf", check=True)

    for line in res.stdout.splitlines():
        m1 = pattern_client.match(line)
        if m1:
            if cur.get("device_name") and cur.get("public_key"):
                items.append(cur)
            cur = {"device_name": m1.group(1).strip()}
            continue

        m2 = pattern_pub.match(line)
        if m2 and cur:
            cur["public_key"] = m2.group(1).strip()
            continue

        m3 = pattern_ips.match(line)
        if m3 and cur:
            cur["allowed_cidr"] = m3.group(1).strip()
            continue

    if cur.get("device_name") and cur.get("public_key"):
        items.append(cur)

    logging.info("Импорт из wg0.conf: найдено %d пиров", len(items))
    return items
