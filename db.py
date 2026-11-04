from typing import Optional

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "mailing.sqlite"
ATTACH_DIR = DATA / "attachments"

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS mailboxes (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  web_app_url TEXT NOT NULL,
  token TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  practice INTEGER NOT NULL DEFAULT 0,
  quota_remaining INTEGER,
  quota_checked_at TEXT,
  skip_until_refresh INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  account_kind TEXT NOT NULL DEFAULT 'gmail',
  account_email TEXT NOT NULL DEFAULT '',
  server_name TEXT NOT NULL DEFAULT '',
  owner_name TEXT NOT NULL DEFAULT 'Admin'
);

CREATE TABLE IF NOT EXISTS servers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversions (
  id INTEGER PRIMARY KEY,
  offer_id INTEGER,
  amount REAL NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY,
  filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_sends (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER,
  mailbox_id INTEGER,
  email TEXT NOT NULL,
  outcome TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  from_name TEXT NOT NULL DEFAULT '',
  reply_to TEXT NOT NULL DEFAULT '',
  html_body TEXT NOT NULL DEFAULT '',
  text_body TEXT NOT NULL DEFAULT '',
  delay_amount INTEGER NOT NULL DEFAULT 1,
  delay_unit TEXT NOT NULL DEFAULT 'seconds',
  rotation TEXT NOT NULL DEFAULT 'drain',
  auto_retries INTEGER NOT NULL DEFAULT 3,
  status TEXT NOT NULL DEFAULT 'draft',
  phase TEXT NOT NULL DEFAULT 'Idle',
  cursor_mailbox_id INTEGER,
  created_at TEXT NOT NULL,
  offer_id INTEGER,
  server_name TEXT NOT NULL DEFAULT '',
  mailbox_ids TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipients (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  fields_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  mailbox_id INTEGER,
  sent_at TEXT,
  UNIQUE(campaign_id, email)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER,
  created_at TEXT NOT NULL,
  message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recipients_campaign ON recipients(campaign_id, status, id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init() -> None:
    DATA.mkdir(exist_ok=True)
    ATTACH_DIR.mkdir(exist_ok=True)
    with _lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(mailboxes)")}
            for name, ddl in (
                ("account_kind", "TEXT NOT NULL DEFAULT 'gmail'"),
                ("account_email", "TEXT NOT NULL DEFAULT ''"),
                ("server_name", "TEXT NOT NULL DEFAULT ''"),
                ("owner_name", "TEXT NOT NULL DEFAULT 'Admin'"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE mailboxes ADD COLUMN {name} {ddl}")
            camp_cols = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)")}
            for name, ddl in (
                ("offer_id", "INTEGER"),
                ("server_name", "TEXT NOT NULL DEFAULT ''"),
                ("mailbox_ids", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in camp_cols:
                    conn.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {ddl}")
            conn.commit()
        finally:
            conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _rows(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()


def _one(sql: str, args: tuple = ()) -> Optional[dict]:
    rows = _rows(sql, args)
    return rows[0] if rows else None


def _write(sql: str, args: tuple = ()) -> int:
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()


def reset_interrupted() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE recipients SET status = 'queued' WHERE status = 'sending'"
            )
            conn.execute(
                """
                UPDATE campaigns
                SET status = 'paused',
                    phase = 'Paused because the app restarted. Press Resume.'
                WHERE status = 'running'
                """
            )
            conn.commit()
        finally:
            conn.close()


def list_mailboxes() -> list[dict]:
    return _rows("SELECT * FROM mailboxes ORDER BY id")


def get_mailbox(mailbox_id: int) -> Optional[dict]:
    return _one("SELECT * FROM mailboxes WHERE id = ?", (mailbox_id,))


def add_mailbox(
    label: str,
    url: str,
    token: str,
    practice: bool,
    account_kind: str = "gmail",
    account_email: str = "",
    server_name: str = "",
    owner_name: str = "Admin",
) -> dict:
    mailbox_id = _write(
        """
        INSERT INTO mailboxes (
          label, web_app_url, token, practice, account_kind, account_email, server_name, owner_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (label, url, token, 1 if practice else 0, account_kind, account_email, server_name, owner_name or "Admin"),
    )
    return get_mailbox(mailbox_id)


def update_mailbox(mailbox_id: int, **fields) -> Optional[dict]:
    allowed = {
        "label",
        "web_app_url",
        "token",
        "enabled",
        "quota_remaining",
        "quota_checked_at",
        "skip_until_refresh",
        "last_error",
        "account_kind",
        "account_email",
        "server_name",
        "owner_name",
    }
    keys = [key for key in fields if key in allowed]
    if not keys:
        return get_mailbox(mailbox_id)
    sql = ", ".join(f"{key} = ?" for key in keys)
    _write(
        f"UPDATE mailboxes SET {sql} WHERE id = ?",
        tuple(fields[key] for key in keys) + (mailbox_id,),
    )
    return get_mailbox(mailbox_id)


def delete_mailbox(mailbox_id: int) -> None:
    _write("DELETE FROM mailboxes WHERE id = ?", (mailbox_id,))


def list_campaigns() -> list[dict]:
    return _rows(
        """
        SELECT c.id, c.name, c.status, c.phase, c.created_at,
          COALESCE(SUM(CASE WHEN r.status IN ('queued', 'sending') THEN 1 ELSE 0 END), 0) AS queued,
          COALESCE(SUM(CASE WHEN r.status = 'sent' THEN 1 ELSE 0 END), 0) AS sent,
          COALESCE(SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed
        FROM campaigns c
        LEFT JOIN recipients r ON r.campaign_id = c.id
        GROUP BY c.id
        ORDER BY c.id DESC
        """
    )


def get_campaign(campaign_id: int) -> Optional[dict]:
    return _one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))


def add_campaign(name: str) -> dict:
    campaign_id = _write(
        "INSERT INTO campaigns (name, created_at) VALUES (?, ?)",
        (name, now()),
    )
    return get_campaign(campaign_id)


def update_campaign(campaign_id: int, **fields) -> Optional[dict]:
    allowed = {
        "name",
        "subject",
        "from_name",
        "reply_to",
        "html_body",
        "text_body",
        "delay_amount",
        "delay_unit",
        "rotation",
        "auto_retries",
        "status",
        "phase",
        "cursor_mailbox_id",
        "offer_id",
        "server_name",
        "mailbox_ids",
    }
    keys = [key for key in fields if key in allowed]
    if not keys:
        return get_campaign(campaign_id)
    sql = ", ".join(f"{key} = ?" for key in keys)
    _write(
        f"UPDATE campaigns SET {sql} WHERE id = ?",
        tuple(fields[key] for key in keys) + (campaign_id,),
    )
    return get_campaign(campaign_id)


def delete_campaign(campaign_id: int) -> None:
    paths = _rows(
        "SELECT path FROM attachments WHERE campaign_id = ?", (campaign_id,)
    )
    _write("DELETE FROM events WHERE campaign_id = ?", (campaign_id,))
    _write("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    for row in paths:
        path = Path(row["path"])
        if path.exists():
            path.unlink()


def add_attachment(campaign_id: int, filename: str, mime_type: str, path: str) -> dict:
    attachment_id = _write(
        """
        INSERT INTO attachments (campaign_id, filename, mime_type, path)
        VALUES (?, ?, ?, ?)
        """,
        (campaign_id, filename, mime_type, path),
    )
    return _one("SELECT id, filename, mime_type FROM attachments WHERE id = ?", (attachment_id,))


def list_attachments(campaign_id: int) -> list[dict]:
    return _rows(
        "SELECT id, filename, mime_type, path FROM attachments WHERE campaign_id = ? ORDER BY id",
        (campaign_id,),
    )


def delete_attachment(attachment_id: int) -> None:
    row = _one("SELECT path FROM attachments WHERE id = ?", (attachment_id,))
    _write("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    if row:
        path = Path(row["path"])
        if path.exists():
            path.unlink()


def counts(campaign_id: int) -> dict:
    rows = _rows(
        """
        SELECT status, COUNT(*) AS n
        FROM recipients
        WHERE campaign_id = ?
        GROUP BY status
        """,
        (campaign_id,),
    )
    found = {row["status"]: row["n"] for row in rows}
    total = sum(found.values())
    return {
        "queued": found.get("queued", 0),
        "sending": found.get("sending", 0),
        "sent": found.get("sent", 0),
        "failed": found.get("failed", 0),
        "total": total,
    }


def list_recipients(campaign_id: int, status: str, limit: int, offset: int) -> list[dict]:
    if status == "all":
        return _rows(
            """
            SELECT id, email, status, attempts, last_error, mailbox_id, sent_at
            FROM recipients
            WHERE campaign_id = ?
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (campaign_id, limit, offset),
        )
    return _rows(
        """
        SELECT id, email, status, attempts, last_error, mailbox_id, sent_at
        FROM recipients
        WHERE campaign_id = ? AND status = ?
        ORDER BY id
        LIMIT ? OFFSET ?
        """,
        (campaign_id, status, limit, offset),
    )


def get_recipient(recipient_id: int) -> Optional[dict]:
    return _one("SELECT * FROM recipients WHERE id = ?", (recipient_id,))


def next_queued(campaign_id: int) -> Optional[dict]:
    return _one(
        """
        SELECT * FROM recipients
        WHERE campaign_id = ? AND status = 'queued'
        ORDER BY id
        LIMIT 1
        """,
        (campaign_id,),
    )


def claim(recipient_id: int) -> None:
    _write(
        "UPDATE recipients SET status = 'sending' WHERE id = ? AND status = 'queued'",
        (recipient_id,),
    )


def mark_sent(recipient_id: int, mailbox_id: int) -> None:
    _write(
        """
        UPDATE recipients
        SET status = 'sent', mailbox_id = ?, sent_at = ?, last_error = ''
        WHERE id = ?
        """,
        (mailbox_id, now(), recipient_id),
    )


def requeue(recipient_id: int, attempts: int, message: str) -> None:
    _write(
        """
        UPDATE recipients
        SET status = 'queued', attempts = ?, last_error = ?
        WHERE id = ?
        """,
        (attempts, message[:500], recipient_id),
    )


def mark_failed(recipient_id: int, attempts: int, message: str) -> None:
    _write(
        """
        UPDATE recipients
        SET status = 'failed', attempts = ?, last_error = ?
        WHERE id = ?
        """,
        (attempts, message[:500], recipient_id),
    )


def retry_failed(campaign_id: int) -> int:
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                UPDATE recipients
                SET status = 'queued', attempts = 0, last_error = ''
                WHERE campaign_id = ? AND status = 'failed'
                """,
                (campaign_id,),
            )
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()


def insert_recipients(campaign_id: int, people: list[tuple[str, str]], replace: bool) -> dict:
    with _lock:
        conn = _connect()
        try:
            if replace:
                conn.execute(
                    """
                    DELETE FROM recipients
                    WHERE campaign_id = ? AND status != 'sent'
                    """,
                    (campaign_id,),
                )
            added = 0
            duplicate = 0
            for email, fields_json in people:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO recipients (campaign_id, email, fields_json)
                    VALUES (?, ?, ?)
                    """,
                    (campaign_id, email, fields_json),
                )
                if cur.rowcount:
                    added += 1
                else:
                    duplicate += 1
            conn.commit()
            return {"added": added, "duplicate": duplicate}
        finally:
            conn.close()


def add_event(campaign_id: Optional[int], message: str) -> None:
    _write(
        "INSERT INTO events (campaign_id, created_at, message) VALUES (?, ?, ?)",
        (campaign_id, now(), message[:500]),
    )
    _write(
        """
        DELETE FROM events
        WHERE campaign_id IS ?
          AND id NOT IN (
            SELECT id FROM events
            WHERE campaign_id IS ?
            ORDER BY id DESC
            LIMIT 200
          )
        """,
        (campaign_id, campaign_id),
    )


def list_events(campaign_id: int, limit: int = 40) -> list[dict]:
    return _rows(
        """
        SELECT created_at, message FROM events
        WHERE campaign_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (campaign_id, limit),
    )


def list_servers() -> list[dict]:
    return _rows("SELECT id, name FROM servers ORDER BY name")


def add_server(name: str) -> dict:
    server_id = _write("INSERT INTO servers (name) VALUES (?)", (name,))
    return _one("SELECT id, name FROM servers WHERE id = ?", (server_id,))


def delete_server(server_id: int) -> None:
    row = _one("SELECT name FROM servers WHERE id = ?", (server_id,))
    _write("DELETE FROM servers WHERE id = ?", (server_id,))
    if row:
        _write("UPDATE mailboxes SET server_name = '' WHERE server_name = ?", (row["name"],))


def get_setting(key: str, default: str = "") -> str:
    row = _one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    _write(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def all_settings() -> dict:
    rows = _rows("SELECT key, value FROM settings")
    found = {row["key"]: row["value"] for row in rows}
    return {
        "delay_amount": int(found.get("delay_amount", "1")),
        "delay_unit": found.get("delay_unit", "seconds"),
        "rotation": found.get("rotation", "drain"),
        "auto_retries": int(found.get("auto_retries", "3")),
        "owner_name": found.get("owner_name", "Admin"),
    }


def list_offers() -> list[dict]:
    return _rows("SELECT * FROM offers ORDER BY id DESC")


def add_offer(name: str, url: str, note: str) -> dict:
    offer_id = _write(
        "INSERT INTO offers (name, url, note, created_at) VALUES (?, ?, ?, ?)",
        (name, url, note, now()),
    )
    return _one("SELECT * FROM offers WHERE id = ?", (offer_id,))


def delete_offer(offer_id: int) -> None:
    _write("DELETE FROM offers WHERE id = ?", (offer_id,))


def add_conversion(offer_id: Optional[int], amount: float, note: str) -> dict:
    conversion_id = _write(
        "INSERT INTO conversions (offer_id, amount, note, created_at) VALUES (?, ?, ?, ?)",
        (offer_id, amount, note, now()),
    )
    return _one("SELECT * FROM conversions WHERE id = ?", (conversion_id,))


def today_revenue() -> float:
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start.astimezone(timezone.utc).isoformat(timespec="seconds")
    row = _one(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM conversions WHERE created_at >= ?",
        (start_utc,),
    )
    return float(row["total"] if row else 0)


def list_media() -> list[dict]:
    return _rows("SELECT id, filename, mime_type, created_at FROM media ORDER BY id DESC")


def add_media(filename: str, mime_type: str, path: str) -> dict:
    media_id = _write(
        "INSERT INTO media (filename, mime_type, path, created_at) VALUES (?, ?, ?, ?)",
        (filename, mime_type, path, now()),
    )
    return _one("SELECT id, filename, mime_type, created_at FROM media WHERE id = ?", (media_id,))


def get_media(media_id: int) -> Optional[dict]:
    return _one("SELECT * FROM media WHERE id = ?", (media_id,))


def search_subscribers(query: str, limit: int = 200) -> list[dict]:
    like = f"%{query.strip().lower()}%"
    return _rows(
        """
        SELECT r.id, r.email, r.status, r.attempts, r.last_error, r.campaign_id, c.name AS campaign_name
        FROM recipients r
        JOIN campaigns c ON c.id = r.campaign_id
        WHERE lower(r.email) LIKE ?
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (like, limit),
    )


def record_test(campaign_id: int, mailbox_id: int, email: str, outcome: str) -> None:
    _write(
        """
        INSERT INTO test_sends (campaign_id, mailbox_id, email, outcome, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (campaign_id, mailbox_id, email, outcome, now()),
    )


def deliverability() -> dict:
    by_box = _rows(
        """
        SELECT mailbox_id,
          SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM recipients
        WHERE mailbox_id IS NOT NULL
        GROUP BY mailbox_id
        """
    )
    by_campaign = _rows(
        """
        SELECT campaign_id,
          SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM recipients
        GROUP BY campaign_id
        """
    )
    tests = _rows(
        """
        SELECT mailbox_id, campaign_id,
          SUM(CASE WHEN outcome = 'sent' THEN 1 ELSE 0 END) AS sent,
          SUM(CASE WHEN outcome != 'sent' THEN 1 ELSE 0 END) AS failed
        FROM test_sends
        GROUP BY mailbox_id, campaign_id
        """
    )
    quota_stops = _one(
        "SELECT COUNT(*) AS n FROM events WHERE message LIKE '%out of daily quota%'"
    )
    waits = _one(
        "SELECT COUNT(*) AS n FROM events WHERE message LIKE '%asked to wait%'"
    )
    return {
        "mailboxes": by_box,
        "campaigns": by_campaign,
        "tests": tests,
        "quotaStops": int(quota_stops["n"] if quota_stops else 0),
        "googleWaits": int(waits["n"] if waits else 0),
    }


def placeholder_names(campaign_id: int) -> list[str]:
    names = {"email", "first name", "offer url"}
    rows = _rows(
        "SELECT fields_json FROM recipients WHERE campaign_id = ? ORDER BY id LIMIT 40",
        (campaign_id,),
    )
    for row in rows:
        try:
            data = json.loads(row["fields_json"] or "{}")
        except json.JSONDecodeError:
            continue
        names.update(str(key) for key in data)
    return sorted(names)


def first_fields(campaign_id: int) -> dict:
    row = _one(
        "SELECT fields_json FROM recipients WHERE campaign_id = ? ORDER BY id LIMIT 1",
        (campaign_id,),
    )
    if not row:
        return {}
    try:
        data = json.loads(row["fields_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def sent_between(start: str, end: str) -> int:
    sql = "SELECT COUNT(*) AS n FROM recipients WHERE status = 'sent'"
    args = []
    if start:
        sql += " AND sent_at >= ?"
        args.append(start)
    if end:
        sql += " AND sent_at <= ?"
        args.append(end + "T23:59:59")
    row = _one(sql, tuple(args))
    return int(row["n"] if row else 0)


def running_campaign_id() -> Optional[int]:
    row = _one(
        "SELECT id FROM campaigns WHERE status IN ('running', 'paused') ORDER BY id DESC LIMIT 1"
    )
    return int(row["id"]) if row else None
