# app/db.py
from __future__ import annotations
import os
import sqlite3
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

DB_PATH = os.environ.get("HILO_DB_PATH", "news.db")

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def get_conn() -> sqlite3.Connection:
    # check_same_thread=False so FastAPI threads can share the handle safely.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_schema() -> None:
    conn = get_conn()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            provider   TEXT NOT NULL,
            type       TEXT NOT NULL,
            title      TEXT NOT NULL,
            url        TEXT NOT NULL UNIQUE,
            summary    TEXT,
            imageUrl   TEXT,
            publishedUtc TEXT NOT NULL,
            createdAt  TEXT NOT NULL
        );
        """)
        conn.commit()
    finally:
        conn.close()

def upsert_items(items: List[Dict[str, Any]]) -> int:
    """Insert normalized items, ignore duplicates by URL."""
    if not items:
        return 0
    conn = get_conn()
    try:
        now = _utc_now_iso()
        cur = conn.cursor()
        cur.executemany("""
            INSERT OR IGNORE INTO items
            (provider, type, title, url, summary, imageUrl, publishedUtc, createdAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                (it.get("provider") or "").strip(),
                (it.get("type") or "fan").strip(),
                (it.get("title") or "").strip(),
                (it.get("url") or "").strip(),
                (it.get("summary") or "").strip(),
                (it.get("imageUrl") or None),
                (it.get("publishedUtc") or "").strip(),
                now,
            )
            for it in items
            if it.get("url") and it.get("title") and it.get("publishedUtc")
        ])
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()

def load_items(since_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load historical items (optionally since a date), newest first."""
    conn = get_conn()
    try:
        if since_iso:
            rows = conn.execute(
                "SELECT provider,type,title,url,summary,imageUrl,publishedUtc "
                "FROM items WHERE publishedUtc >= ? ORDER BY publishedUtc DESC",
                (since_iso,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT provider,type,title,url,summary,imageUrl,publishedUtc "
                "FROM items ORDER BY publishedUtc DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
