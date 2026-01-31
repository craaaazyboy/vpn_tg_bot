# settings.py
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()  # поднимет переменные из .env, если он есть

@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str
    ADMIN_ID: str
    WG_SSH_HOST: str
    WG_SSH_USER: str = "root"
    WG_SSH_KEY: str = "/run/secrets/vpn_ssh_key"
    DATABASE_URL: str = ""
    BRAND_NAME: str = "VPN"
    IKEV2_SERVER_ADDR: str = ""
    IKEV2_REMOTE_ID: str = ""
    IKEV2_CA_CERT_PATH: str = ""
    IKEV2_SERVER_MANAGER: str = ""
    PUBLIC_BASE_URL: str = ""
    DOWNLOAD_TTL_SECONDS: int = 900


    @property
    def ADMIN_IDS(self) -> list[int]:
        return [int(i) for i in self.ADMIN_ID.split(",") if i.strip()]

def _get_required(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val

settings = Settings(
    BOT_TOKEN=_get_required("BOT_TOKEN"),
    ADMIN_ID=os.getenv("ADMIN_ID", ""),  # можно пустым
    WG_SSH_HOST=_get_required("WG_SSH_HOST"),
    WG_SSH_USER=os.getenv("WG_SSH_USER", "root"),
    WG_SSH_KEY=os.getenv("WG_SSH_KEY", "/run/secrets/vpn_ssh_key"),
    DATABASE_URL=os.getenv("DATABASE_URL", ""),
    IKEV2_SERVER_ADDR=os.getenv("IKEV2_SERVER_ADDR", ""),
    IKEV2_REMOTE_ID=os.getenv("IKEV2_REMOTE_ID", ""),
    IKEV2_CA_CERT_PATH=os.getenv("IKEV2_CA_CERT_PATH", ""),
    IKEV2_SERVER_MANAGER=os.getenv("IKEV2_SERVER_MANAGER", ""),
    BRAND_NAME= os.getenv("BRAND_NAME", "VPN"),
    PUBLIC_BASE_URL= os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:18080"),
    DOWNLOAD_TTL_SECONDS= int(os.getenv("DOWNLOAD_TTL_SECONDS", "900"))
)
