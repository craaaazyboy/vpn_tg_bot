# utils/req_parse.py
from typing import Optional, Tuple, List
import re

RE_LINK = re.compile(r'<a\s+href="tg://user\?id=(\d+)">([^<]+)</a>')
RE_TGID_HTML = re.compile(r'tg://user\?id=(\d+)')
RE_CODE_HTML = re.compile(r'<code>([^<]+)</code>')
RE_BACKTICKS = re.compile(r'`([^`]+)`')

def split_personal_name(full: str) -> Tuple[Optional[str], Optional[str]]:
    full = (full or "").strip()
    if not full:
        return None, None
    parts = full.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]

def extract_tg_and_display_name(text: str) -> Tuple[Optional[int], Optional[str]]:
    m = RE_LINK.search(text)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m2 = RE_TGID_HTML.search(text)
    if m2:
        return int(m2.group(1)), None
    return None, None

def extract_device_name(text: str) -> Optional[str]:
    m = RE_CODE_HTML.search(text)
    if m:
        return m.group(1).strip()
    m = RE_BACKTICKS.search(text)
    if m:
        return m.group(1).strip()
    # fallback — ищем после «Запрос VPN»
    blocks = re.split(r'Запрос VPN', text, flags=re.IGNORECASE)
    for b in blocks[1:] or blocks:
        for line in b.splitlines():
            line = re.sub(r'<.*?>', '', line).strip()
            if not line:
                continue
            if line.startswith("💻"):
                return line.lstrip("💻").strip()
    return None

def extract_pairs_from_text(txt: str) -> List[tuple[int, str]]:
    pairs: List[tuple[int, str]] = []
    blocks = re.split(r'Запрос VPN', txt)
    for b in blocks:
        m1 = RE_TGID_HTML.search(b)
        m2 = RE_CODE_HTML.search(b)
        if m1 and m2:
            tg_id = int(m1.group(1))
            device = m2.group(1).strip()
            pairs.append((tg_id, device))
    return pairs
