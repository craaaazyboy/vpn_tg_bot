DDL = """
CREATE TABLE IF NOT EXISTS peers (
    id          SERIAL PRIMARY KEY,
    tg_user     BIGINT       NOT NULL,
    device_name TEXT         NOT NULL,
    peer_id     INTEGER      NOT NULL,   -- <- было UUID
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
"""
