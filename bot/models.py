DDL = """
-- 1) Безопасно создаём enum-тип (если уже есть — молчим)
DO $$
BEGIN
    CREATE TYPE peer_status AS ENUM ('pending','active','revoked');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END$$;

-- 2) users — владельцы и админы
CREATE TABLE IF NOT EXISTS users (
    tg_id         BIGINT PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3) peers — ПИРЫ WireGuard
-- tg_user допускает NULL, чтобы можно было импортировать «скелеты» из wg0.conf
CREATE TABLE IF NOT EXISTS peers (
    id              SERIAL PRIMARY KEY,
    tg_user         BIGINT REFERENCES users(tg_id) ON DELETE CASCADE,  -- NULLable!
    device_name     TEXT        NOT NULL,
    peer_pubkey     TEXT        NOT NULL,
    peer_ip_octet   INTEGER     NOT NULL,
    allowed_cidr    CIDR        NOT NULL,
    status          peer_status NOT NULL DEFAULT 'active',
    rx_bytes        BIGINT      NOT NULL DEFAULT 0,
    tx_bytes        BIGINT      NOT NULL DEFAULT 0,
    last_handshake  TIMESTAMPTZ,
    conf_path       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    api_client_id   TEXT
);

-- Уникальность
CREATE UNIQUE INDEX IF NOT EXISTS ux_peers_owner_device
  ON peers(tg_user, device_name) WHERE status <> 'revoked';
CREATE UNIQUE INDEX IF NOT EXISTS ux_peers_pubkey_active
  ON peers(peer_pubkey)          WHERE status <> 'revoked';
CREATE UNIQUE INDEX IF NOT EXISTS ux_peers_ip
  ON peers(peer_ip_octet)        WHERE status <> 'revoked';

-- Быстрые выборки
CREATE INDEX IF NOT EXISTS ix_peers_status ON peers(status);
CREATE INDEX IF NOT EXISTS ix_peers_owner  ON peers(tg_user);

-- Немного целостности (идемпотентно добавляем CHECK)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE c.conname = 'ck_peer_ip_octet_range'
          AND t.relname = 'peers'
          AND n.nspname = 'public'
    ) THEN
        ALTER TABLE public.peers
            ADD CONSTRAINT ck_peer_ip_octet_range
            CHECK (peer_ip_octet BETWEEN 2 AND 254) NOT VALID;
    END IF;
END$$;

-- 4) заявки на доступ
CREATE TABLE IF NOT EXISTS access_requests (
    id           SERIAL PRIMARY KEY,
    tg_user      BIGINT      NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    device_name  TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
    decided_by   BIGINT      REFERENCES users(tg_id),
    decided_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5) аудит
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    action      TEXT        NOT NULL,  -- request_approved, peer_revoked, conf_resent, ...
    actor_id    BIGINT,
    target_peer INTEGER,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6) VIEW для админки

-- Активные пиры с владельцами
CREATE OR REPLACE VIEW v_peers_active AS
SELECT p.id, p.device_name, p.peer_pubkey, p.peer_ip_octet, p.allowed_cidr,
       p.rx_bytes, p.tx_bytes, p.last_handshake, p.created_at,
       u.tg_id AS owner_tg, u.username, u.first_name, u.last_name
FROM peers p
LEFT JOIN users u ON u.tg_id = p.tg_user
WHERE p.status = 'active';

-- Счётчики по статусам
CREATE OR REPLACE VIEW v_peer_counters AS
SELECT
  sum((p.status='active')::int)  AS active,
  sum((p.status='revoked')::int) AS revoked,
  sum((p.status='pending')::int) AS pending
FROM peers p;
"""
