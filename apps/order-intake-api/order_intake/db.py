from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS merchant (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'THB',
    timezone TEXT NOT NULL DEFAULT 'Asia/Bangkok',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
    external_ref TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (merchant_id, external_ref)
);

CREATE TABLE IF NOT EXISTS product (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    uom TEXT NOT NULL,
    price TEXT NOT NULL,
    stock_qty TEXT NOT NULL DEFAULT '0',
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (merchant_id, sku)
);

CREATE TABLE IF NOT EXISTS order_draft (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
    customer_id TEXT REFERENCES customer(id),
    customer_ref TEXT,
    source_channel TEXT NOT NULL,
    source_text TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    total TEXT NOT NULL,
    exception_reasons_json TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT NOT NULL,
    reviewer TEXT,
    decision_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (merchant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS order_item (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES order_draft(id) ON DELETE CASCADE,
    raw_text TEXT NOT NULL,
    product_id TEXT REFERENCES product(id),
    sku TEXT,
    product_name TEXT,
    quantity TEXT NOT NULL,
    uom TEXT,
    unit_price TEXT NOT NULL,
    line_total TEXT NOT NULL,
    confidence REAL NOT NULL,
    exception_reason TEXT
);

CREATE TABLE IF NOT EXISTS audit_event (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    draft_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_draft_merchant_status
    ON order_draft(merchant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_merchant_created
    ON audit_event(merchant_id, created_at DESC);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
