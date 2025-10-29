from __future__ import annotations

import asyncio
import json
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, JSON,
    Text, UniqueConstraint, Index, select, update, func, and_, or_, text
)
from sqlalchemy.dialects.postgresql import CIDR
from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

from settings import settings

# ───────────────────────────────── Base / Engine / Session ─────────────────────────────────

def _make_async_url(url: str) -> str:
    # превращаем postgresql:// → postgresql+asyncpg://
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url

ASYNC_DB_URL = _make_async_url(settings.DATABASE_URL)

engine = create_async_engine(ASYNC_DB_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

# ───────────────────────────────── Models ─────────────────────────────────

PeerStatus = Enum(
    "pending", "active", "revoked",
    name="peer_status",
    create_type=False,           # тип уже существует — не пересоздаём
)

class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.false())
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    peers: Mapped[List["Peer"]] = relationship(back_populates="owner")

class Peer(Base):
    __tablename__ = "peers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=True
    )
    device_name: Mapped[str] = mapped_column(Text, nullable=False)
    peer_pubkey: Mapped[str] = mapped_column(Text, nullable=False)
    peer_ip_octet: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    status: Mapped[str] = mapped_column(PeerStatus, nullable=False, server_default="active")
    rx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    tx_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    last_handshake: Mapped[Optional[Any]] = mapped_column(DateTime(timezone=True))
    conf_path: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[Optional[Any]] = mapped_column(DateTime(timezone=True))
    api_client_id: Mapped[Optional[str]] = mapped_column(Text)

    owner: Mapped[Optional[User]] = relationship(back_populates="peers")

    __table_args__ = (
        # тот же чек, что и раньше
        CheckConstraint("peer_ip_octet BETWEEN 2 AND 254", name="ck_peer_ip_octet_range"),
        # частичные уникальные индексы (WHERE status <> 'revoked')
        Index(
            "ux_peers_owner_device",
            "tg_user", "device_name",
            unique=True,
            postgresql_where=text("status <> 'revoked'")
        ),
        Index(
            "ux_peers_pubkey_active",
            "peer_pubkey",
            unique=True,
            postgresql_where=text("status <> 'revoked'")
        ),
        Index(
            "ux_peers_ip",
            "peer_ip_octet",
            unique=True,
            postgresql_where=text("status <> 'revoked'")
        ),
        Index("ix_peers_status", "status"),
        Index("ix_peers_owner", "tg_user"),
    )

class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False)
    device_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")  # pending/approved/rejected
    decided_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.tg_id"))
    decided_at: Mapped[Optional[Any]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    target_peer: Mapped[Optional[int]] = mapped_column(Integer)
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

# ───────────────────────────────── Init (без потери данных) ─────────────────────────────────

async def _ensure_pg_enum(session: AsyncSession) -> None:
    # если type уже создан — блок Do $$ просто "молча" завершится
    await session.execute(text("""
    DO $$
    BEGIN
        CREATE TYPE peer_status AS ENUM ('pending','active','revoked');
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END$$;
    """))

async def init_models() -> None:
    async with engine.begin() as conn:
        # enum — заранее, чтобы маппинг не пытался его создать сам
        await conn.execute(text("""
        DO $$
        BEGIN
            CREATE TYPE peer_status AS ENUM ('pending','active','revoked');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END$$;
        """))
        # create_all с checkfirst не трогает существующие таблицы/данные
        await conn.run_sync(Base.metadata.create_all)

# ───────────────────────────────── Session helper ─────────────────────────────────

_pool_ready = False
async def get_pool() -> AsyncSession:
    """
    drop-in совместимость с прежним названием. Возвращает новую сессию.
    Используйте как: async with get_pool() as session: ...
    """
    # оставляем сигнатуру, но теперь это сессия ORM
    # чтобы минимально менять остальной код, будем возвращать саму сессию
    global _pool_ready
    if not _pool_ready:
        # первый вызов — инициализация схемы
        async with engine.begin() as _:
            await init_models()
        _pool_ready = True
    return SessionLocal()

# ───────────────────────────────── CRUD (с сохранением старых сигнатур) ─────────────────────────────────

# users
async def upsert_user(tg_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str]) -> None:
    async with SessionLocal() as s:
        u = await s.get(User, tg_id)
        if u is None:
            u = User(tg_id=tg_id, username=username, first_name=first_name, last_name=last_name)
            s.add(u)
        else:
            # обновляем ТОЛЬКО не-None
            if username is not None:
                u.username = username
            if first_name is not None:
                u.first_name = first_name
            if last_name is not None:
                u.last_name = last_name
        await s.commit()

async def is_admin(tg_id: int) -> bool:
    async with SessionLocal() as s:
        u = await s.get(User, tg_id)
        return bool(u and u.is_admin)

# peers
async def insert_peer(
    tg_user: int,
    device_name: str,
    pubkey: str,
    ip_oct: int,
    allowed_cidr: Optional[str],
    conf_path: Optional[str],
    api_client_id: Optional[str] = None,
) -> int:
    async with SessionLocal() as s:
        p = Peer(
            tg_user=tg_user, device_name=device_name, peer_pubkey=pubkey,
            peer_ip_octet=ip_oct, allowed_cidr=allowed_cidr, conf_path=conf_path,
            api_client_id=api_client_id, status="active"
        )
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
        return pid

async def update_peer_stats(items: List[Dict[str, Any]]) -> None:
    if not items:
        return
    async with SessionLocal() as s:
        for it in items:
            await s.execute(
                update(Peer)
                .where(and_(Peer.peer_pubkey == it["pubkey"], Peer.status == "active"))
                .values(rx_bytes=it.get("rx_bytes", 0),
                        tx_bytes=it.get("tx_bytes", 0),
                        last_handshake=it.get("last_handshake"))
            )
        await s.commit()

async def revoke_peer(peer_id: int, actor_id: int) -> None:
    async with SessionLocal() as s:
        await s.execute(
            update(Peer).where(Peer.id == peer_id).values(status="revoked", revoked_at=func.now())
        )
        s.add(AuditLog(action="peer_revoked", actor_id=actor_id, target_peer=peer_id, details={}))
        await s.commit()

def _octet_from_cidr(c: Optional[str]) -> Optional[int]:
    if not c:
        return None
    try:
        ip = c.split("/", 1)[0]
        return int(ip.split(".")[-1])
    except Exception:
        return None

async def upsert_peer_skeleton(device_name: str, public_key: str, allowed_cidr: Optional[str]) -> tuple[int, str]:
    async with SessionLocal() as s:
        res = await s.execute(select(Peer).where(Peer.device_name == device_name))
        row: Optional[Peer] = res.scalar_one_or_none()
        ip_oct = _octet_from_cidr(allowed_cidr)

        if row is None:
            p = Peer(
                device_name=device_name, peer_pubkey=public_key,
                peer_ip_octet=ip_oct or 0, allowed_cidr=allowed_cidr, status="active"
            )
            s.add(p)
            await s.flush()
            pid = p.id
            await s.commit()
            return pid, "created"

        prev_cidr = str(row.allowed_cidr) if row.allowed_cidr is not None else None
        need_update = (row.peer_pubkey != public_key) or (prev_cidr != (allowed_cidr or None)) or (row.peer_ip_octet != ip_oct)

        if need_update:
            row.peer_pubkey = public_key
            row.peer_ip_octet = ip_oct or 0
            row.allowed_cidr = allowed_cidr
            await s.commit()
            return row.id, "updated"

        return row.id, "skipped"

# owners linking
async def link_peer_owner(device_name: str, tg_user: int) -> bool:
    async with SessionLocal() as s:
        res = await s.execute(select(Peer).where(and_(Peer.device_name == device_name, Peer.tg_user.is_(None))))
        row = res.scalar_one_or_none()
        if not row:
            return False
        row.tg_user = tg_user
        await s.commit()
        return True

async def link_peer_owner_by_pubkey(public_key: str, tg_user: int) -> bool:
    async with SessionLocal() as s:
        res = await s.execute(select(Peer).where(and_(Peer.peer_pubkey == public_key, Peer.tg_user.is_(None))))
        row = res.scalar_one_or_none()
        if not row:
            return False
        row.tg_user = tg_user
        await s.commit()
        return True

async def link_peer_owner_by_id(peer_id: int, tg_user: int) -> bool:
    async with SessionLocal() as s:
        p = await s.get(Peer, peer_id)
        if not p or p.tg_user is not None:
            return False
        p.tg_user = tg_user
        await s.commit()
        return True

# lookups for admin
async def owners_by_pubkeys(pubkeys: List[str]) -> Dict[str, Dict[str, Any]]:
    if not pubkeys:
        return {}
    async with SessionLocal() as s:
        res = await s.execute(
            select(Peer.peer_pubkey, Peer.device_name,
                   User.tg_id, User.username, User.first_name, User.last_name)
            .select_from(Peer).join(User, User.tg_id == Peer.tg_user, isouter=True)
            .where(Peer.peer_pubkey.in_(pubkeys))
        )
        rows = res.all()
        return {
            r[0]: {
                "device_name": r[1],
                "tg_id": r[2],
                "username": r[3],
                "first_name": r[4],
                "last_name": r[5],
            } for r in rows
        }

async def get_peer_by_id(peer_id: int):
    async with SessionLocal() as s:
        res = await s.execute(
            select(Peer, User.username, User.first_name, User.last_name)
            .join(User, User.tg_id == Peer.tg_user, isouter=True)
            .where(Peer.id == peer_id)
        )
        row = res.first()
        if not row:
            return None
        # имитируем старый dict-like ответ
        p: Peer = row[0]
        return {
            "id": p.id, "tg_user": p.tg_user, "device_name": p.device_name, "peer_pubkey": p.peer_pubkey,
            "peer_ip_octet": p.peer_ip_octet, "allowed_cidr": str(p.allowed_cidr) if p.allowed_cidr else None,
            "status": p.status, "rx_bytes": p.rx_bytes, "tx_bytes": p.tx_bytes,
            "last_handshake": p.last_handshake, "conf_path": p.conf_path, "created_at": p.created_at,
            "revoked_at": p.revoked_at, "api_client_id": p.api_client_id,
            "username": row.username, "first_name": row.first_name, "last_name": row.last_name
        }

async def list_peers_like(query: str, limit: int = 25):
    q = (query or "").strip()
    if not q:
        return []
    async with SessionLocal() as s:
        res = await s.execute(
            select(Peer.id, Peer.device_name, Peer.allowed_cidr,
                   User.tg_id, User.username, User.first_name, User.last_name)
            .join(User, User.tg_id == Peer.tg_user, isouter=True)
            .where(func.lower(Peer.device_name).like(func.concat("%", func.lower(q), "%")))
            .order_by(Peer.id.desc())
            .limit(limit)
        )
        # вернём список dict, как раньше
        return [
            {
                "id": r.id,
                "device_name": r.device_name,
                "allowed_cidr": str(r.allowed_cidr) if r.allowed_cidr else None,
                "tg_id": r.tg_id, "username": r.username, "first_name": r.first_name, "last_name": r.last_name,
            }
            for r in res.mappings().all()
        ]

async def find_candidates_by_device(guess: str, limit: int = 6):
    g = (guess or "").strip()
    if not g:
        return []
    async with SessionLocal() as s:
        # формула «похожести» из прежнего SQL
        pos_expr = func.position(func.lower(g), func.lower(Peer.device_name))
        exact = (func.lower(Peer.device_name) == func.lower(g))
        like = (pos_expr > 0)
        res = await s.execute(
            select(Peer.id, Peer.device_name, Peer.allowed_cidr)
            .where(
                and_(
                    Peer.tg_user.is_(None),
                    or_(
                        exact,
                        func.lower(Peer.device_name).like(func.concat("%", func.lower(g), "%")),
                        func.lower(g).like(func.concat("%", func.lower(Peer.device_name), "%")),
                    )
                )
            )
            .order_by(
                exact.desc(),
                like.desc(),
                pos_expr.asc(),
                func.abs(func.length(Peer.device_name) - func.length(g)).asc(),
                Peer.id.desc(),
            )
            .limit(limit)
        )
        return [
            {"id": r.id, "device_name": r.device_name, "allowed_cidr": str(r.allowed_cidr) if r.allowed_cidr else None}
            for r in res.mappings().all()
        ]

# access_requests
async def create_access_request(tg_user: int, device_name: str) -> int:
    async with SessionLocal() as s:
        ar = AccessRequest(tg_user=tg_user, device_name=device_name, status="pending")
        s.add(ar)
        await s.flush()
        rid = ar.id
        await s.commit()
        return rid

async def get_access_request(req_id: int):
    async with SessionLocal() as s:
        res = await s.execute(
            select(AccessRequest, User.username, User.first_name, User.last_name)
            .join(User, User.tg_id == AccessRequest.tg_user, isouter=True)
            .where(AccessRequest.id == req_id)
        )
        row = res.first()
        if not row:
            return None
        ar: AccessRequest = row[0]
        return {
            "id": ar.id, "tg_user": ar.tg_user, "device_name": ar.device_name, "status": ar.status,
            "decided_by": ar.decided_by, "decided_at": ar.decided_at, "created_at": ar.created_at,
            "username": row.username, "first_name": row.first_name, "last_name": row.last_name,
        }

async def list_access_requests(status: str, limit: int, offset: int):
    async with SessionLocal() as s:
        res = await s.execute(
            select(AccessRequest, User.username, User.first_name, User.last_name)
            .join(User, User.tg_id == AccessRequest.tg_user, isouter=True)
            .where(AccessRequest.status == status)
            .order_by(AccessRequest.id.desc())
            .limit(limit).offset(offset)
        )
        rows = []
        for r in res.all():
            ar: AccessRequest = r[0]
            rows.append({
                "id": ar.id, "tg_user": ar.tg_user, "device_name": ar.device_name, "status": ar.status,
                "decided_by": ar.decided_by, "decided_at": ar.decided_at, "created_at": ar.created_at,
                "username": r.username, "first_name": r.first_name, "last_name": r.last_name,
            })
        return rows

async def count_access_requests(status: str) -> int:
    async with SessionLocal() as s:
        res = await s.execute(select(func.count()).select_from(AccessRequest).where(AccessRequest.status == status))
        return int(res.scalar() or 0)

async def decide_access_request(req_id: int, decided_by: int, status: str) -> None:
    assert status in ("approved", "rejected")
    async with SessionLocal() as s:
        await s.execute(
            update(AccessRequest)
            .where(and_(AccessRequest.id == req_id, AccessRequest.status == "pending"))
            .values(status=status, decided_by=decided_by, decided_at=func.now())
        )
        await s.commit()

async def count_peers(status: str) -> int:
    async with SessionLocal() as s:
        res = await s.execute(
            select(func.count()).select_from(Peer).where(Peer.status == status)
        )
        return int(res.scalar() or 0)

async def list_peers_page(status: str, limit: int, offset: int):
    """
    Страница пиров по статусу с данными владельца.
    Возврат — список dict в формате, который уже ждут хендлеры.
    """
    async with SessionLocal() as s:
        res = await s.execute(
            select(Peer.id, Peer.device_name, Peer.allowed_cidr,
                   User.tg_id, User.username, User.first_name, User.last_name)
            .join(User, User.tg_id == Peer.tg_user, isouter=True)
            .where(Peer.status == status)
            .order_by(Peer.id.desc())
            .limit(limit).offset(offset)
        )
        return [
            {
                "id": r.id,
                "device_name": r.device_name,
                "allowed_cidr": str(r.allowed_cidr) if r.allowed_cidr else None,
                "tg_id": r.tg_id,
                "username": r.username,
                "first_name": r.first_name,
                "last_name": r.last_name,
            }
            for r in res.mappings().all()
        ]
    

async def list_user_peers(tg_user: int):
    """
    Вернёт устройства пользователя в прежнем формате dict-списка:
    [{id, device_name, allowed_cidr, status, last_handshake}]
    """
    async with SessionLocal() as s:
        res = await s.execute(
            select(Peer.id, Peer.device_name, Peer.allowed_cidr, Peer.status, Peer.last_handshake)
            .where(Peer.tg_user == tg_user)
            .order_by(Peer.id)
        )
        return [
            {
                "id": r.id,
                "device_name": r.device_name,
                "allowed_cidr": str(r.allowed_cidr) if r.allowed_cidr else None,
                "status": r.status,
                "last_handshake": r.last_handshake,
            }
            for r in res.mappings().all()
        ]
    
async def get_peer_owned_by(peer_id: int, owner_tg_id: int):
    """
    Возвращает peer строго если он принадлежит owner_tg_id.
    Формат dict совместим с карточками.
    """
    async with SessionLocal() as s:
        res = await s.execute(
            select(
                Peer.id, Peer.tg_user, Peer.device_name, Peer.peer_pubkey, Peer.peer_ip_octet,
                Peer.allowed_cidr, Peer.status, Peer.rx_bytes, Peer.tx_bytes,
                Peer.last_handshake, Peer.conf_path, Peer.created_at, Peer.revoked_at, Peer.api_client_id
            ).where(and_(Peer.id == peer_id, Peer.tg_user == owner_tg_id))
        )
        r = res.mappings().first()
        if not r:
            return None
        # приводим к единообразию: строки/тип CIDR
        d = dict(r)
        if d.get("allowed_cidr") is not None:
            d["allowed_cidr"] = str(d["allowed_cidr"])
        return d