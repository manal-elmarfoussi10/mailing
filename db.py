from typing import Optional

import json
import secrets
import sqlite3
import threading
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

import vault

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
  owner_name TEXT NOT NULL DEFAULT 'Admin',
  script_style TEXT NOT NULL DEFAULT 'app'
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
  step TEXT NOT NULL DEFAULT 'idle',
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

CREATE TABLE IF NOT EXISTS suppression (
  email TEXT PRIMARY KEY,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  html_body TEXT NOT NULL DEFAULT '',
  text_body TEXT NOT NULL DEFAULT '',
  from_name TEXT NOT NULL DEFAULT '',
  reply_to TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  first_name TEXT NOT NULL DEFAULT '',
  last_name TEXT NOT NULL DEFAULT '',
  company TEXT NOT NULL DEFAULT '',
  fields_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailbox_groups (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailbox_group_members (
  group_id INTEGER NOT NULL REFERENCES mailbox_groups(id) ON DELETE CASCADE,
  mailbox_id INTEGER NOT NULL,
  PRIMARY KEY (group_id, mailbox_id)
);

CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_groups (
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  PRIMARY KEY (contact_id, group_id)
);

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_tags (
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (contact_id, tag_id)
);

CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'any',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  mode TEXT NOT NULL,
  total_rows INTEGER NOT NULL,
  added INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  skipped INTEGER NOT NULL,
  invalid INTEGER NOT NULL,
  suppressed INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
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
                ("script_style", "TEXT NOT NULL DEFAULT 'app'"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE mailboxes ADD COLUMN {name} {ddl}")
            camp_cols = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)")}
            for name, ddl in (
                ("offer_id", "INTEGER"),
                ("server_name", "TEXT NOT NULL DEFAULT ''"),
                ("mailbox_ids", "TEXT NOT NULL DEFAULT ''"),
                ("step", "TEXT NOT NULL DEFAULT 'idle'"),
            ):
                if name not in camp_cols:
                    conn.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {ddl}")
            rec_cols = {row[1] for row in conn.execute("PRAGMA table_info(recipients)")}
            if "unsub_token" not in rec_cols:
                conn.execute("ALTER TABLE recipients ADD COLUMN unsub_token TEXT NOT NULL DEFAULT ''")
            offer_cols = {row[1] for row in conn.execute("PRAGMA table_info(offers)")}
            for name, ddl in (
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("status", "TEXT NOT NULL DEFAULT 'active'"),
            ):
                if name not in offer_cols:
                    conn.execute(f"ALTER TABLE offers ADD COLUMN {name} {ddl}")
            conv_cols = {row[1] for row in conn.execute("PRAGMA table_info(conversions)")}
            for name, ddl in (
                ("campaign_id", "INTEGER"),
                ("email", "TEXT NOT NULL DEFAULT ''"),
                ("occurred_at", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in conv_cols:
                    conn.execute(f"ALTER TABLE conversions ADD COLUMN {name} {ddl}")
            media_cols = {row[1] for row in conn.execute("PRAGMA table_info(media)")}
            if "size" not in media_cols:
                conn.execute("ALTER TABLE media ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
            segment_cols = {row[1] for row in conn.execute("PRAGMA table_info(segments)")}
            for name, ddl in (("group_id", "TEXT NOT NULL DEFAULT ''"), ("tag_id", "TEXT NOT NULL DEFAULT ''")):
                if segment_cols and name not in segment_cols:
                    conn.execute(f"ALTER TABLE segments ADD COLUMN {name} {ddl}")
            for row in conn.execute("SELECT id, web_app_url, token FROM mailboxes"):
                if not str(row[1]).startswith("enc1:"):
                    conn.execute(
                        "UPDATE mailboxes SET web_app_url = ?, token = ? WHERE id = ?",
                        (vault.seal(row[1]), vault.seal(row[2]), row[0]),
                    )
            ai_key = conn.execute("SELECT value FROM settings WHERE key = 'ai_api_key'").fetchone()
            if ai_key and not str(ai_key[0]).startswith("enc1:"):
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'ai_api_key'",
                    (vault.seal(ai_key[0]),),
                )
            _migrate_companies(conn)
            _backfill_contacts(conn)
            conn.commit()
        finally:
            conn.close()


def _field(fields: dict, *names: str) -> str:
    wanted = {name.replace(" ", "") for name in names}
    for key, value in fields.items():
        if str(key).lower().replace(" ", "").replace("_", "") in wanted and str(value).strip():
            return str(value).strip()
    return ""


def _backfill_contacts(conn: sqlite3.Connection) -> None:
    stamp = now()
    rows = conn.execute(
        """
        SELECT r.email, r.fields_json
        FROM recipients r
        JOIN (
          SELECT MIN(id) AS id FROM recipients GROUP BY lower(trim(email))
        ) first ON first.id = r.id
        """
    ).fetchall()
    for row in rows:
        email = str(row["email"] or "").strip().lower()
        if "@" not in email:
            continue
        try:
            fields = json.loads(row["fields_json"] or "{}")
        except json.JSONDecodeError:
            fields = {}
        if not isinstance(fields, dict):
            fields = {}
        conn.execute(
            """
            INSERT OR IGNORE INTO contacts
              (email, first_name, last_name, company, fields_json, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'campaign', ?, ?)
            """,
            (
                email,
                _field(fields, "firstname", "givenname"),
                _field(fields, "lastname", "surname", "familyname"),
                _field(fields, "company", "organization", "organisation"),
                json.dumps(fields),
                stamp,
                stamp,
            ),
        )


_company: ContextVar = ContextVar("company_id", default=None)


def bind_company(company_id: Optional[int]) -> None:
    _company.set(company_id)


def current_company() -> int:
    company_id = _company.get()
    if not company_id:
        raise PermissionError("This action belongs to a company workspace")
    return int(company_id)


def _migrate_companies(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          email TEXT NOT NULL DEFAULT '',
          contact_name TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY,
          email TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL DEFAULT '',
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL,
          company_id INTEGER,
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL,
          last_login TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_settings (
          company_id INTEGER NOT NULL,
          key TEXT NOT NULL,
          value TEXT NOT NULL,
          PRIMARY KEY (company_id, key)
        )
        """
    )
    existing = conn.execute("SELECT id FROM companies ORDER BY id LIMIT 1").fetchone()
    if existing:
        company_id = int(existing[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO companies (name, email, contact_name, phone, status, created_at)
            VALUES ('Primary company', '', '', '', 'active', ?)
            """,
            (now(),),
        )
        company_id = int(cur.lastrowid)
    for table in (
        "mailboxes", "servers", "campaigns", "contacts", "groups", "tags", "segments",
        "mailbox_groups", "offers", "conversions", "media", "templates", "imports", "events",
    ):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "company_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN company_id INTEGER NOT NULL DEFAULT {company_id}")
        conn.execute(f"UPDATE {table} SET company_id = ? WHERE company_id IS NULL OR company_id = 0", (company_id,))
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_company ON {table}(company_id)")
    suppression_cols = {row[1] for row in conn.execute("PRAGMA table_info(suppression)")}
    if "company_id" not in suppression_cols:
        conn.execute(
            """
            CREATE TABLE suppression_company (
              company_id INTEGER NOT NULL,
              email TEXT NOT NULL,
              reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              PRIMARY KEY (company_id, email)
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO suppression_company (company_id, email, reason, created_at) SELECT ?, email, reason, created_at FROM suppression",
            (company_id,),
        )
        conn.execute("DROP TABLE suppression")
        conn.execute("ALTER TABLE suppression_company RENAME TO suppression")


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
                    step = 'paused',
                    phase = 'Paused because the app restarted. Press Resume.'
                WHERE status = 'running'
                """
            )
            conn.commit()
        finally:
            conn.close()


def _open_mailbox(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    opened = dict(row)
    opened["web_app_url"] = vault.open_secret(opened.get("web_app_url") or "")
    opened["token"] = vault.open_secret(opened.get("token") or "")
    return opened


def list_mailboxes() -> list[dict]:
    return [_open_mailbox(row) for row in _rows("SELECT * FROM mailboxes WHERE company_id = ? ORDER BY id", (current_company(),))]


def list_all_mailboxes() -> list[dict]:
    return [_open_mailbox(row) for row in _rows("SELECT * FROM mailboxes ORDER BY id")]


def get_mailbox(mailbox_id: int) -> Optional[dict]:
    return _open_mailbox(_one("SELECT * FROM mailboxes WHERE id = ? AND company_id = ?", (mailbox_id, current_company())))


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
          label, web_app_url, token, practice, account_kind, account_email, server_name, owner_name, company_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (label, vault.seal(url), vault.seal(token), 1 if practice else 0, account_kind, account_email, server_name, owner_name or "Admin", current_company()),
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
        "script_style",
    }
    keys = [key for key in fields if key in allowed]
    if not keys:
        return get_mailbox(mailbox_id)
    if "web_app_url" in fields:
        fields["web_app_url"] = vault.seal(fields["web_app_url"])
    if "token" in fields:
        fields["token"] = vault.seal(fields["token"])
    sql = ", ".join(f"{key} = ?" for key in keys)
    _write(
        f"UPDATE mailboxes SET {sql} WHERE id = ? AND company_id = ?",
        tuple(fields[key] for key in keys) + (mailbox_id, current_company()),
    )
    return get_mailbox(mailbox_id)


def delete_mailbox(mailbox_id: int) -> None:
    if get_mailbox(mailbox_id) is None:
        return
    _write("DELETE FROM mailboxes WHERE id = ? AND company_id = ?", (mailbox_id, current_company()))


def list_campaigns() -> list[dict]:
    return _rows(
        """
        SELECT c.id, c.name, c.subject, c.status, c.phase, c.step, c.created_at,
          c.rotation, c.mailbox_ids, c.server_name,
          (SELECT MAX(created_at) FROM events e WHERE e.campaign_id = c.id) AS last_event,
          COALESCE(SUM(CASE WHEN r.status IN ('queued', 'sending') THEN 1 ELSE 0 END), 0) AS queued,
          COALESCE(SUM(CASE WHEN r.status = 'sent' THEN 1 ELSE 0 END), 0) AS sent,
          COALESCE(SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed
        FROM campaigns c
        LEFT JOIN recipients r ON r.campaign_id = c.id
        WHERE c.company_id = ?
        GROUP BY c.id
        ORDER BY c.id DESC
        """,
        (current_company(),),
    )


def get_campaign(campaign_id: int) -> Optional[dict]:
    return _one("SELECT * FROM campaigns WHERE id = ? AND company_id = ?", (campaign_id, current_company()))


def campaign_company(campaign_id: int) -> Optional[int]:
    row = _one("SELECT company_id FROM campaigns WHERE id = ?", (campaign_id,))
    return int(row["company_id"]) if row else None


def add_campaign(name: str) -> dict:
    campaign_id = _write(
        "INSERT INTO campaigns (name, created_at, company_id) VALUES (?, ?, ?)",
        (name, now(), current_company()),
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
        "step",
        "cursor_mailbox_id",
        "offer_id",
        "server_name",
        "mailbox_ids",
    }
    keys = [key for key in fields if key in allowed]
    if not keys or get_campaign(campaign_id) is None:
        return get_campaign(campaign_id)
    sql = ", ".join(f"{key} = ?" for key in keys)
    _write(
        f"UPDATE campaigns SET {sql} WHERE id = ? AND company_id = ?",
        tuple(fields[key] for key in keys) + (campaign_id, current_company()),
    )
    return get_campaign(campaign_id)


def delete_campaign(campaign_id: int) -> None:
    if get_campaign(campaign_id) is None:
        return
    paths = _rows(
        "SELECT path FROM attachments WHERE campaign_id = ?", (campaign_id,)
    )
    _write("DELETE FROM events WHERE campaign_id = ?", (campaign_id,))
    _write("DELETE FROM campaigns WHERE id = ? AND company_id = ?", (campaign_id, current_company()))
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
        "suppressed": found.get("suppressed", 0),
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


def take_queued(campaign_id: int, limit: int) -> list[dict]:
    if limit < 1:
        return []
    with _lock:
        conn = _connect()
        try:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM recipients
                    WHERE campaign_id = ? AND status = 'queued'
                    ORDER BY id
                    LIMIT ?
                    """,
                    (campaign_id, limit),
                )
            ]
            if not rows:
                return []
            marks = ",".join("?" for _ in rows)
            conn.execute(
                f"""
                UPDATE recipients
                SET status = 'sending'
                WHERE id IN ({marks}) AND status = 'queued'
                """,
                tuple(row["id"] for row in rows),
            )
            conn.commit()
            return rows
        finally:
            conn.close()


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


def mark_failed(recipient_id: int, attempts: int, message: str, mailbox_id: Optional[int] = None) -> None:
    _write(
        """
        UPDATE recipients
        SET status = 'failed', attempts = ?, last_error = ?, sent_at = ?, mailbox_id = COALESCE(?, mailbox_id)
        WHERE id = ?
        """,
        (attempts, message[:500], now(), mailbox_id, recipient_id),
    )


def retry_failed(campaign_id: int) -> int:
    return _requeue(campaign_id, ("failed",), None)


def requeue_sent(campaign_id: int) -> int:
    return _requeue(campaign_id, ("sent",), None)


def requeue_finished(campaign_id: int) -> int:
    return _requeue(campaign_id, ("sent", "failed"), None)


def mailbox_results(campaign_id: int) -> list:
    return _rows(
        """
        SELECT mailbox_id,
          SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM recipients
        WHERE campaign_id = ? AND mailbox_id IS NOT NULL
        GROUP BY mailbox_id
        """,
        (campaign_id,),
    )


def campaign_activity(campaign_id: int) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="seconds")
    per = _one(
        """
        SELECT COUNT(*) AS n FROM recipients
        WHERE campaign_id = ? AND status = 'sent' AND sent_at >= ?
        """,
        (campaign_id, since),
    )
    pace = _rows(
        """
        SELECT substr(sent_at, 1, 16) AS minute,
          SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM recipients
        WHERE campaign_id = ? AND sent_at IS NOT NULL AND status IN ('sent', 'failed')
        GROUP BY minute
        ORDER BY minute
        """,
        (campaign_id,),
    )
    recent = _rows(
        """
        SELECT email, status, mailbox_id, last_error
        FROM recipients
        WHERE campaign_id = ? AND status IN ('sent', 'failed') AND sent_at IS NOT NULL
        ORDER BY sent_at DESC
        LIMIT 8
        """,
        (campaign_id,),
    )
    return {
        "perMinute": int(per["n"]) if per else 0,
        "pace": pace[-60:],
        "recent": recent,
    }


def _requeue(campaign_id: int, statuses: tuple, fields_json: Optional[str]) -> int:
    with _lock:
        conn = _connect()
        try:
            marks = ",".join("?" for _ in statuses)
            if fields_json is None:
                cur = conn.execute(
                    f"""
                    UPDATE recipients
                    SET status = 'queued', attempts = 0, last_error = '', mailbox_id = NULL, sent_at = NULL
                    WHERE campaign_id = ? AND status IN ({marks})
                    """,
                    (campaign_id, *statuses),
                )
            else:
                cur = conn.execute(
                    f"""
                    UPDATE recipients
                    SET status = 'queued', attempts = 0, last_error = '', mailbox_id = NULL,
                        sent_at = NULL, fields_json = ?
                    WHERE campaign_id = ? AND status IN ({marks})
                    """,
                    (fields_json, campaign_id, *statuses),
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
                    WHERE campaign_id = ? AND status NOT IN ('sent', 'suppressed')
                    """,
                    (campaign_id,),
                )
            added = 0
            duplicate = 0
            requeued = 0
            suppressed = 0
            for email, fields_json in people:
                if _suppressed_conn(conn, email):
                    suppressed += 1
                    conn.execute(
                        """
                        UPDATE recipients
                        SET status = 'suppressed', last_error = 'Unsubscribed'
                        WHERE campaign_id = ? AND lower(email) = ? AND status != 'sent'
                        """,
                        (campaign_id, email.strip().lower()),
                    )
                    continue
                token = secrets.token_urlsafe(18)
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO recipients (campaign_id, email, fields_json, unsub_token)
                    VALUES (?, ?, ?, ?)
                    """,
                    (campaign_id, email, fields_json, token),
                )
                if cur.rowcount:
                    added += 1
                    _remember_contact(conn, email, fields_json, "campaign")
                    continue
                cur = conn.execute(
                    """
                    UPDATE recipients
                    SET status = 'queued', attempts = 0, last_error = '', mailbox_id = NULL,
                        sent_at = NULL, fields_json = ?
                    WHERE campaign_id = ? AND email = ? AND status IN ('sent', 'failed')
                    """,
                    (fields_json, campaign_id, email),
                )
                if cur.rowcount:
                    requeued += 1
                else:
                    duplicate += 1
            conn.commit()
            return {"added": added, "duplicate": duplicate, "requeued": requeued, "suppressed": suppressed}
        finally:
            conn.close()


def add_event(campaign_id: Optional[int], message: str) -> None:
    try:
        company_id = current_company()
    except PermissionError:
        company_id = campaign_company(campaign_id) if campaign_id else None
    _write(
        "INSERT INTO events (campaign_id, company_id, created_at, message) VALUES (?, ?, ?, ?)",
        (campaign_id, company_id, now(), message[:500]),
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
    return _rows("SELECT id, name FROM servers WHERE company_id = ? ORDER BY name", (current_company(),))


def add_server(name: str) -> dict:
    server_id = _write("INSERT INTO servers (name, company_id) VALUES (?, ?)", (name, current_company()))
    return _one("SELECT id, name FROM servers WHERE id = ?", (server_id,))


def delete_server(server_id: int) -> None:
    row = _one("SELECT name FROM servers WHERE id = ?", (server_id,))
    _write("DELETE FROM servers WHERE id = ?", (server_id,))
    if row:
        _write("UPDATE mailboxes SET server_name = '' WHERE server_name = ?", (row["name"],))


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _suppressed_conn(conn: sqlite3.Connection, email: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM suppression WHERE company_id = ? AND email = ?",
        (current_company(), normalize_email(email)),
    ).fetchone()
    return row is not None


def is_suppressed(email: str) -> bool:
    return _one("SELECT email FROM suppression WHERE company_id = ? AND email = ?", (current_company(), normalize_email(email))) is not None


def suppress_email(email: str, reason: str) -> None:
    address = normalize_email(email)
    if not address:
        return
    _write(
        """
        INSERT INTO suppression (company_id, email, reason, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(company_id, email) DO UPDATE SET reason = excluded.reason
        """,
        (current_company(), address, reason[:200], now()),
    )
    _write(
        """
        UPDATE recipients
        SET status = 'suppressed', last_error = 'Unsubscribed'
        WHERE lower(email) = ? AND status IN ('queued', 'sending', 'failed')
          AND campaign_id IN (SELECT id FROM campaigns WHERE company_id = ?)
        """,
        (address, current_company()),
    )


def list_suppression() -> list[dict]:
    return _rows("SELECT email, reason, created_at FROM suppression WHERE company_id = ? ORDER BY created_at DESC", (current_company(),))


def remove_suppression(email: str) -> None:
    _write("DELETE FROM suppression WHERE company_id = ? AND email = ?", (current_company(), normalize_email(email),))


def mark_suppressed(recipient_id: int) -> None:
    _write(
        """
        UPDATE recipients
        SET status = 'suppressed', last_error = 'Unsubscribed'
        WHERE id = ?
        """,
        (recipient_id,),
    )


def unsub_token_for(recipient_id: int) -> str:
    row = get_recipient(recipient_id)
    if row is None:
        return ""
    if row.get("unsub_token"):
        return row["unsub_token"]
    token = secrets.token_urlsafe(18)
    _write("UPDATE recipients SET unsub_token = ? WHERE id = ?", (token, recipient_id))
    return token


def recipient_by_token(token: str) -> Optional[dict]:
    return _one("SELECT * FROM recipients WHERE unsub_token = ?", (token,))


def list_templates() -> list[dict]:
    return _rows("SELECT id, name, subject, from_name, reply_to, created_at FROM templates WHERE company_id = ? ORDER BY id DESC", (current_company(),))


def get_template(template_id: int) -> Optional[dict]:
    return _one("SELECT * FROM templates WHERE id = ? AND company_id = ?", (template_id, current_company()))


def add_template(name: str, subject: str, html_body: str, text_body: str, from_name: str, reply_to: str) -> dict:
    template_id = _write(
        """
        INSERT INTO templates (name, subject, html_body, text_body, from_name, reply_to, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, subject, html_body, text_body, from_name, reply_to, now()),
    )
    return get_template(template_id)


def delete_template(template_id: int) -> None:
    _write("DELETE FROM templates WHERE id = ?", (template_id,))


def duplicate_campaign(campaign_id: int) -> Optional[dict]:
    source = get_campaign(campaign_id)
    if source is None:
        return None
    created = add_campaign(f"{source['name']} copy")
    update_campaign(
        created["id"],
        subject=source["subject"],
        from_name=source["from_name"],
        reply_to=source["reply_to"],
        html_body=source["html_body"],
        text_body=source["text_body"],
        delay_amount=source["delay_amount"],
        delay_unit=source["delay_unit"],
        rotation=source["rotation"],
        auto_retries=source["auto_retries"],
        offer_id=source["offer_id"],
        server_name=source["server_name"],
        mailbox_ids=source["mailbox_ids"],
        status="draft",
        phase="Idle",
        step="idle",
    )
    people = []
    for row in _rows("SELECT email, fields_json FROM recipients WHERE campaign_id = ?", (campaign_id,)):
        if is_suppressed(row["email"]):
            continue
        people.append((row["email"], row["fields_json"]))
    if people:
        insert_recipients(created["id"], people, False)
    folder = ATTACH_DIR / str(created["id"])
    for row in list_attachments(campaign_id):
        src = Path(row["path"])
        if not src.exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{secrets.token_hex(4)}_{row['filename']}"
        dest.write_bytes(src.read_bytes())
        add_attachment(created["id"], row["filename"], row["mime_type"], str(dest))
    return get_campaign(created["id"])


def get_setting(key: str, default: str = "") -> str:
    try:
        company_id = current_company()
    except PermissionError:
        row = _one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default
    row = _one("SELECT value FROM company_settings WHERE company_id = ? AND key = ?", (company_id, key))
    if row:
        return row["value"]
    global_row = _one("SELECT value FROM settings WHERE key = ?", (key,))
    if global_row and company_id == _primary_id():
        return global_row["value"]
    return default


def _primary_id() -> int:
    row = _one("SELECT id FROM companies ORDER BY id LIMIT 1")
    return int(row["id"]) if row else 0


def set_setting(key: str, value: str) -> None:
    _write(
        """
        INSERT INTO company_settings (company_id, key, value) VALUES (?, ?, ?)
        ON CONFLICT(company_id, key) DO UPDATE SET value = excluded.value
        """,
        (current_company(), key, value),
    )


def all_settings() -> dict:
    found = {}
    for key in ("delay_amount", "delay_unit", "rotation", "auto_retries", "owner_name", "public_base_url", "ai_base_url", "ai_model", "ai_api_key", "from_name", "reply_to", "import_mode", "password_hash"):
        found[key] = get_setting(key, "")
    return {
        "delay_amount": int(found.get("delay_amount") or "1"),
        "delay_unit": found.get("delay_unit") or "seconds",
        "rotation": found.get("rotation") or "drain",
        "auto_retries": int(found.get("auto_retries") or "3"),
        "owner_name": found.get("owner_name", "Admin"),
        "public_base_url": found.get("public_base_url", ""),
        "ai_base_url": found.get("ai_base_url", "https://api.openai.com/v1"),
        "ai_model": found.get("ai_model", "gpt-4o-mini"),
        "ai_ready": bool(found.get("ai_api_key")),
        "has_password": bool(found.get("password_hash")),
        "from_name": found.get("from_name", ""),
        "reply_to": found.get("reply_to", ""),
        "import_mode": found.get("import_mode", "skip"),
    }


def secret_setting(key: str) -> str:
    return vault.open_secret(get_setting(key, ""))


def list_offers() -> list[dict]:
    return _rows(
        """
        SELECT o.*,
          (SELECT COUNT(*) FROM campaigns c WHERE c.offer_id = o.id) AS campaigns,
          (SELECT COUNT(*) FROM conversions v WHERE v.offer_id = o.id) AS conversions,
          (SELECT COALESCE(SUM(amount), 0) FROM conversions v WHERE v.offer_id = o.id) AS revenue
        FROM offers o
        WHERE o.company_id = ?
        ORDER BY o.id DESC
        """,
        (current_company(),),
    )


def add_offer(name: str, url: str, note: str, description: str = "", status: str = "active") -> dict:
    offer_id = _write(
        """
        INSERT INTO offers (name, url, note, description, status, created_at, company_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, url, note, description, status if status in {"active", "paused"} else "active", now(), current_company()),
    )
    return _one("SELECT * FROM offers WHERE id = ? AND company_id = ?", (offer_id, current_company()))


def update_offer(offer_id: int, name: str, url: str, note: str, description: str, status: str) -> Optional[dict]:
    _write(
        """
        UPDATE offers
        SET name = ?, url = ?, note = ?, description = ?, status = ?
        WHERE id = ?
        """,
        (name, url, note, description, status if status in {"active", "paused"} else "active", offer_id),
    )
    return _one("SELECT * FROM offers WHERE id = ? AND company_id = ?", (offer_id, current_company()))


def delete_offer(offer_id: int) -> None:
    _write("DELETE FROM offers WHERE id = ?", (offer_id,))


def offer_detail(offer_id: int) -> Optional[dict]:
    row = _one("SELECT * FROM offers WHERE id = ? AND company_id = ?", (offer_id, current_company()))
    if not row:
        return None
    row["campaigns"] = _rows("SELECT id, name, status FROM campaigns WHERE offer_id = ? ORDER BY id DESC", (offer_id,))
    row["conversions"] = _rows(
        "SELECT id, amount, note, email, campaign_id, created_at, occurred_at FROM conversions WHERE offer_id = ? ORDER BY id DESC",
        (offer_id,),
    )
    row["revenue"] = sum(float(item["amount"] or 0) for item in row["conversions"])
    row["clicks"] = None
    return row


def add_conversion(offer_id: Optional[int], amount: float, note: str, campaign_id: Optional[int] = None, email: str = "", occurred_at: str = "") -> dict:
    stamp = occurred_at or now()
    conversion_id = _write(
        """
        INSERT INTO conversions (offer_id, amount, note, created_at, campaign_id, email, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (offer_id, amount, note, now(), campaign_id, email.strip().lower(), stamp),
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
    rows = _rows("SELECT id, filename, mime_type, size, path, created_at FROM media WHERE company_id = ? ORDER BY id DESC", (current_company(),))
    for row in rows:
        if not int(row.get("size") or 0):
            try:
                row["size"] = Path(row["path"]).stat().st_size
            except OSError:
                row["size"] = 0
        row.pop("path", None)
    used = {
        row["filename"]: row["n"]
        for row in _rows("SELECT filename, COUNT(*) AS n FROM attachments GROUP BY filename")
    }
    for row in rows:
        row["used"] = int(used.get(row["filename"], 0))
    return rows


def add_media(filename: str, mime_type: str, path: str, size: int = 0) -> dict:
    media_id = _write(
        "INSERT INTO media (filename, mime_type, path, size, created_at, company_id) VALUES (?, ?, ?, ?, ?, ?)",
        (filename, mime_type, path, size, now(), current_company()),
    )
    return _one("SELECT id, filename, mime_type, size, created_at FROM media WHERE id = ?", (media_id,))


def rename_media(media_id: int, filename: str) -> None:
    _write("UPDATE media SET filename = ? WHERE id = ?", (filename, media_id))


def delete_media(media_id: int) -> Optional[str]:
    row = get_media(media_id)
    if not row:
        return None
    _write("DELETE FROM media WHERE id = ?", (media_id,))
    return row["path"]


def get_media(media_id: int) -> Optional[dict]:
    return _one("SELECT * FROM media WHERE id = ? AND company_id = ?", (media_id, current_company()))


def search_subscribers(query: str, limit: int = 200) -> list[dict]:
    like = f"%{query.strip().lower()}%"
    return _rows(
        """
        SELECT r.id, r.email, r.status, r.attempts, r.last_error, r.campaign_id, c.name AS campaign_name
        FROM recipients r
        JOIN campaigns c ON c.id = r.campaign_id
        WHERE c.company_id = ? AND lower(r.email) LIKE ?
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (current_company(), like, limit),
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
          AND campaign_id IN (SELECT id FROM campaigns WHERE company_id = ?)
        GROUP BY mailbox_id
        """,
        (current_company(),),
    )
    by_campaign = _rows(
        """
        SELECT campaign_id,
          SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM recipients
        WHERE campaign_id IN (SELECT id FROM campaigns WHERE company_id = ?)
        GROUP BY campaign_id
        """,
        (current_company(),),
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
        "SELECT COUNT(*) AS n FROM events WHERE company_id = ? AND message LIKE '%out of daily quota%'",
        (current_company(),),
    )
    waits = _one(
        "SELECT COUNT(*) AS n FROM events WHERE company_id = ? AND message LIKE '%asked to wait%'",
        (current_company(),),
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


def sent_today() -> dict:
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start.astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = _rows(
        """
        SELECT r.status AS status, COUNT(*) AS n
        FROM recipients r
        JOIN campaigns c ON c.id = r.campaign_id
        WHERE c.company_id = ? AND r.sent_at >= ? AND r.status IN ('sent', 'failed')
        GROUP BY r.status
        """,
        (current_company(), start_utc),
    )
    found = {row["status"]: int(row["n"]) for row in rows}
    return {"sent": found.get("sent", 0), "failed": found.get("failed", 0)}


def activity_series(days: int = 30) -> list:
    span = max(1, min(int(days), 90))
    start_local = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start_local = start_local - timedelta(days=span - 1)
    rows = _rows(
        """
        SELECT sent_at, status
        FROM recipients
        WHERE sent_at >= ? AND status IN ('sent', 'failed')
        """,
        (start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),),
    )
    buckets = {}
    for offset in range(span):
        day = (start_local + timedelta(days=offset)).date().isoformat()
        buckets[day] = {"day": day, "sent": 0, "failed": 0}
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row["sent_at"])
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        day = stamp.astimezone().date().isoformat()
        if day not in buckets:
            continue
        key = "failed" if row["status"] == "failed" else "sent"
        buckets[day][key] += 1
    return list(buckets.values())


def latest_quota_check() -> str:
    row = _one(
        """
        SELECT MAX(quota_checked_at) AS at
        FROM mailboxes
        WHERE quota_checked_at IS NOT NULL AND quota_checked_at != ''
        """
    )
    return row["at"] if row and row["at"] else ""


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


def recent_events(limit: int = 8) -> list:
    return _rows(
        """
        SELECT campaign_id, created_at, message
        FROM events
        WHERE company_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (current_company(), limit),
    )


def _remember_contact(conn: sqlite3.Connection, email: str, fields_json: str, source: str) -> None:
    address = email.strip().lower()
    try:
        fields = json.loads(fields_json or "{}")
    except json.JSONDecodeError:
        fields = {}
    if not isinstance(fields, dict):
        fields = {}
    stamp = now()
    conn.execute(
        """
        INSERT OR IGNORE INTO contacts
          (email, first_name, last_name, company, fields_json, source, created_at, updated_at, company_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            address,
            _field(fields, "firstname", "givenname"),
            _field(fields, "lastname", "surname", "familyname"),
            _field(fields, "company", "organization", "organisation"),
            json.dumps(fields),
            source,
            stamp,
            stamp,
            current_company(),
        ),
    )


def _provider_name(email: str) -> str:
    domain = email.split("@")[-1]
    if domain in {"gmail.com", "googlemail.com"}:
        return "Gmail"
    if domain in {"yahoo.com", "ymail.com", "yahoo.fr"}:
        return "Yahoo"
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        return "Outlook"
    if domain in {"icloud.com", "me.com", "mac.com"}:
        return "iCloud"
    return domain or "Other"


def _provider_clause(provider: str) -> str:
    domain = "substr(c.email, instr(c.email, '@') + 1)"
    groups = {
        "gmail": "('gmail.com','googlemail.com')",
        "yahoo": "('yahoo.com','ymail.com','yahoo.fr')",
        "outlook": "('outlook.com','hotmail.com','live.com')",
        "icloud": "('icloud.com','me.com','mac.com')",
    }
    if provider in groups:
        return f" AND {domain} IN {groups[provider]}"
    if provider == "other":
        known = "('gmail.com','googlemail.com','yahoo.com','ymail.com','yahoo.fr','outlook.com','hotmail.com','live.com','icloud.com','me.com','mac.com')"
        return f" AND {domain} NOT IN {known}"
    return ""


def audience_summary() -> dict:
    total = _one("SELECT COUNT(*) AS n FROM contacts WHERE company_id = ?", (current_company(),))
    unsub = _one(
        """
        SELECT COUNT(*) AS n FROM contacts c
        JOIN suppression s ON s.email = c.email AND s.company_id = c.company_id
        WHERE c.company_id = ?
        """,
        (current_company(),),
    )
    last = _one("SELECT * FROM imports WHERE company_id = ? ORDER BY id DESC LIMIT 1", (current_company(),))
    count = int(total["n"] if total else 0)
    suppressed = int(unsub["n"] if unsub else 0)
    return {
        "total": count,
        "active": count - suppressed,
        "unsubscribed": suppressed,
        "invalid": 0,
        "duplicates": int(last["skipped"] if last else 0),
        "lastImport": dict(last) if last else None,
    }


def list_contacts(query: str, status: str, provider: str, group_id: str, tag_id: str, sort: str, page: int) -> dict:
    where = "WHERE c.company_id = ?"
    args: list = [current_company()]
    if query.strip():
        where += " AND (c.email LIKE ? OR c.first_name LIKE ? OR c.last_name LIKE ? OR c.company LIKE ?)"
        like = f"%{query.strip().lower()}%"
        args.extend([like, like, like, like])
    if status == "active":
        where += " AND s.email IS NULL"
    elif status == "unsubscribed":
        where += " AND s.email IS NOT NULL"
    where += _provider_clause(provider)
    if group_id.isdigit():
        where += " AND c.id IN (SELECT contact_id FROM contact_groups WHERE group_id = ?)"
        args.append(int(group_id))
    if tag_id.isdigit():
        where += " AND c.id IN (SELECT contact_id FROM contact_tags WHERE tag_id = ?)"
        args.append(int(tag_id))
    order = {
        "name": "c.first_name, c.email",
        "email": "c.email",
        "newest": "c.id DESC",
    }.get(sort, "c.id DESC")
    page_size = 25
    offset = max(0, page) * page_size
    total = _one(
        f"""
        SELECT COUNT(*) AS n FROM contacts c
        LEFT JOIN suppression s ON s.email = c.email AND s.company_id = c.company_id
        {where}
        """,
        tuple(args),
    )
    rows = _rows(
        f"""
        SELECT c.*, CASE WHEN s.email IS NULL THEN 'active' ELSE 'unsubscribed' END AS status,
          (SELECT group_concat(g.name, ', ') FROM groups g
            JOIN contact_groups cg ON cg.group_id = g.id WHERE cg.contact_id = c.id) AS groups,
          (SELECT group_concat(t.name, ', ') FROM tags t
            JOIN contact_tags ct ON ct.tag_id = t.id WHERE ct.contact_id = c.id) AS tags
        FROM contacts c
        LEFT JOIN suppression s ON s.email = c.email AND s.company_id = c.company_id
        {where}
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        tuple(args + [page_size, offset]),
    )
    for row in rows:
        row["provider"] = _provider_name(row["email"])
    count = int(total["n"] if total else 0)
    return {"rows": rows, "total": count, "page": page, "pages": max(1, (count + page_size - 1) // page_size)}


def save_contact(email: str, first_name: str, last_name: str, company: str, source: str, contact_id: Optional[int] = None) -> dict:
    address = email.strip().lower()
    stamp = now()
    if contact_id:
        other = _one("SELECT id FROM contacts WHERE company_id = ? AND email = ? AND id != ?", (current_company(), address, contact_id))
        if other:
            raise ValueError("That email is already in the audience.")
        _write(
            """
            UPDATE contacts
            SET email = ?, first_name = ?, last_name = ?, company = ?, updated_at = ?
            WHERE id = ?
            """,
            (address, first_name.strip(), last_name.strip(), company.strip(), stamp, contact_id),
        )
        row = _one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    else:
        existing = _one("SELECT id FROM contacts WHERE company_id = ? AND email = ?", (current_company(), address,))
        if existing:
            raise ValueError("That email is already in the audience.")
        new_id = _write(
            """
            INSERT INTO contacts (email, first_name, last_name, company, source, created_at, updated_at, company_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (address, first_name.strip(), last_name.strip(), company.strip(), source, stamp, stamp, current_company()),
        )
        row = _one("SELECT * FROM contacts WHERE id = ?", (new_id,))
    if not row:
        raise ValueError("That contact was not found.")
    return row


def delete_contacts(ids: list[int]) -> int:
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    return _write(f"DELETE FROM contacts WHERE id IN ({marks})", tuple(ids))


def list_groups() -> list[dict]:
    return _rows(
        """
        SELECT g.id, g.name, g.created_at, COUNT(cg.contact_id) AS contacts
        FROM groups g
        LEFT JOIN contact_groups cg ON cg.group_id = g.id
        WHERE g.company_id = ?
        GROUP BY g.id
        ORDER BY g.name
        """,
        (current_company(),),
    )


def add_group(name: str) -> dict:
    group_id = _write("INSERT INTO groups (name, created_at, company_id) VALUES (?, ?, ?)", (name.strip(), now(), current_company()))
    return _one("SELECT id, name, created_at FROM groups WHERE id = ?", (group_id,))


def rename_group(group_id: int, name: str) -> None:
    _write("UPDATE groups SET name = ? WHERE id = ?", (name.strip(), group_id))


def delete_group(group_id: int) -> None:
    _write("DELETE FROM groups WHERE id = ?", (group_id,))


def assign_groups(contact_ids: list[int], group_id: int, remove: bool) -> None:
    with _lock:
        conn = _connect()
        try:
            for contact_id in contact_ids:
                if remove:
                    conn.execute(
                        "DELETE FROM contact_groups WHERE contact_id = ? AND group_id = ?",
                        (contact_id, group_id),
                    )
                else:
                    conn.execute(
                        "INSERT OR IGNORE INTO contact_groups (contact_id, group_id) VALUES (?, ?)",
                        (contact_id, group_id),
                    )
            conn.commit()
        finally:
            conn.close()


def list_tags() -> list[dict]:
    return _rows(
        """
        SELECT t.id, t.name, COUNT(ct.contact_id) AS contacts
        FROM tags t
        LEFT JOIN contact_tags ct ON ct.tag_id = t.id
        WHERE t.company_id = ?
        GROUP BY t.id
        ORDER BY t.name
        """,
        (current_company(),),
    )


def add_tag(name: str) -> dict:
    tag_id = _write("INSERT INTO tags (name, created_at, company_id) VALUES (?, ?, ?)", (name.strip(), now(), current_company()))
    return _one("SELECT id, name FROM tags WHERE id = ?", (tag_id,))


def delete_tag(tag_id: int) -> None:
    _write("DELETE FROM tags WHERE id = ?", (tag_id,))


def assign_tags(contact_ids: list[int], tag_id: int, remove: bool) -> None:
    with _lock:
        conn = _connect()
        try:
            for contact_id in contact_ids:
                if remove:
                    conn.execute(
                        "DELETE FROM contact_tags WHERE contact_id = ? AND tag_id = ?",
                        (contact_id, tag_id),
                    )
                else:
                    conn.execute(
                        "INSERT OR IGNORE INTO contact_tags (contact_id, tag_id) VALUES (?, ?)",
                        (contact_id, tag_id),
                    )
            conn.commit()
        finally:
            conn.close()


def list_segments() -> list[dict]:
    rows = _rows("SELECT id, name, provider, status, group_id, tag_id, created_at FROM segments WHERE company_id = ? ORDER BY name", (current_company(),))
    for row in rows:
        counted = list_contacts("", row["status"] if row["status"] != "any" else "", row["provider"], str(row.get("group_id") or ""), str(row.get("tag_id") or ""), "newest", 0)
        row["contacts"] = counted["total"]
    return rows


def add_segment(name: str, provider: str, status: str, group_id: str = "", tag_id: str = "") -> dict:
    segment_id = _write(
        """
        INSERT INTO segments (name, provider, status, group_id, tag_id, created_at, company_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name.strip(), provider, status or "any", str(group_id or ""), str(tag_id or ""), now(), current_company()),
    )
    return _one("SELECT * FROM segments WHERE id = ?", (segment_id,))


def delete_segment(segment_id: int) -> None:
    _write("DELETE FROM segments WHERE id = ?", (segment_id,))


def classify_import(rows: list[dict]) -> dict:
    existing = {
        row["email"]
        for row in _rows("SELECT email FROM contacts WHERE company_id = ?", (current_company(),))
    }
    suppressed = {
        row["email"]
        for row in _rows("SELECT email FROM suppression WHERE company_id = ?", (current_company(),))
    }
    new = 0
    already = 0
    blocked = 0
    for row in rows:
        email = row["email"]
        if email in suppressed:
            blocked += 1
        if email in existing:
            already += 1
        elif email not in suppressed:
            new += 1
    return {"new": new, "existing": already, "suppressed": blocked}


def commit_import(rows: list[dict], mode: str, source: str, group_id: Optional[int], invalid: int, duplicate: int, total: int) -> dict:
    if mode not in {"skip", "update"}:
        mode = "skip"
    added = 0
    updated = 0
    skipped = 0
    suppressed = 0
    stamp = now()
    with _lock:
        conn = _connect()
        try:
            kept: list[int] = []
            for row in rows:
                email = row["email"].strip().lower()
                blocked = conn.execute("SELECT 1 FROM suppression WHERE company_id = ? AND email = ?", (current_company(), email)).fetchone()
                current = conn.execute("SELECT id FROM contacts WHERE company_id = ? AND email = ?", (current_company(), email)).fetchone()
                if blocked:
                    suppressed += 1
                if current and mode == "skip":
                    skipped += 1
                    kept.append(int(current["id"]))
                    continue
                fields = json.dumps(row.get("fields") or {"email": email})
                if current:
                    conn.execute(
                        """
                        UPDATE contacts
                        SET first_name = ?, last_name = ?, company = ?, fields_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (row.get("first_name") or "", row.get("last_name") or "", row.get("company") or "", fields, stamp, current["id"]),
                    )
                    updated += 1
                    kept.append(int(current["id"]))
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO contacts
                          (email, first_name, last_name, company, fields_json, source, created_at, updated_at, company_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (email, row.get("first_name") or "", row.get("last_name") or "", row.get("company") or "", fields, source, stamp, stamp, current_company()),
                    )
                    added += 1
                    kept.append(int(cur.lastrowid))
            if group_id:
                for contact_id in kept:
                    conn.execute(
                        "INSERT OR IGNORE INTO contact_groups (contact_id, group_id) VALUES (?, ?)",
                        (contact_id, group_id),
                    )
            conn.execute(
                """
                INSERT INTO imports
                  (source, mode, total_rows, added, updated, skipped, invalid, suppressed, created_at, company_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source, mode, total, added, updated, skipped + duplicate, invalid, suppressed, stamp, current_company()),
            )
            conn.commit()
        finally:
            conn.close()
    return {"added": added, "updated": updated, "skipped": skipped, "suppressed": suppressed, "invalid": invalid}


def merge_groups(source_id: int, target_id: int) -> int:
    if source_id == target_id:
        return 0
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO contact_groups (contact_id, group_id)
                SELECT contact_id, ? FROM contact_groups WHERE group_id = ?
                """,
                (target_id, source_id),
            )
            conn.execute("DELETE FROM groups WHERE id = ?", (source_id,))
            conn.commit()
        finally:
            conn.close()
    row = _one("SELECT COUNT(*) AS n FROM contact_groups WHERE group_id = ?", (target_id,))
    return int(row["n"] if row else 0)


def list_mailbox_groups() -> list[dict]:
    rows = _rows("SELECT id, name, created_at FROM mailbox_groups WHERE company_id = ? ORDER BY name", (current_company(),))
    for row in rows:
        members = _rows("SELECT mailbox_id FROM mailbox_group_members WHERE group_id = ?", (row["id"],))
        row["mailbox_ids"] = [item["mailbox_id"] for item in members]
    return rows


def save_mailbox_group(name: str, mailbox_ids: list[int], group_id: Optional[int] = None) -> dict:
    with _lock:
        conn = _connect()
        try:
            if group_id:
                conn.execute("UPDATE mailbox_groups SET name = ? WHERE id = ?", (name.strip(), group_id))
            else:
                cur = conn.execute(
                    "INSERT INTO mailbox_groups (name, created_at, company_id) VALUES (?, ?, ?)",
                    (name.strip(), now(), current_company()),
                )
                group_id = int(cur.lastrowid)
            conn.execute("DELETE FROM mailbox_group_members WHERE group_id = ?", (group_id,))
            for mailbox_id in mailbox_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO mailbox_group_members (group_id, mailbox_id) VALUES (?, ?)",
                    (group_id, mailbox_id),
                )
            conn.commit()
        finally:
            conn.close()
    return _one("SELECT id, name FROM mailbox_groups WHERE id = ?", (group_id,))


def delete_mailbox_group(group_id: int) -> None:
    _write("DELETE FROM mailbox_groups WHERE id = ?", (group_id,))


def segment_emails(segment_id: int) -> list[str]:
    row = _one("SELECT provider, status, group_id, tag_id FROM segments WHERE id = ?", (segment_id,))
    if not row:
        return []
    where = "WHERE s.email IS NULL"
    args: list = []
    if row["status"] == "unsubscribed":
        where = "WHERE s.email IS NOT NULL"
    where += _provider_clause(row["provider"] or "")
    if str(row["group_id"] or "").isdigit():
        where += " AND c.id IN (SELECT contact_id FROM contact_groups WHERE group_id = ?)"
        args.append(int(row["group_id"]))
    if str(row["tag_id"] or "").isdigit():
        where += " AND c.id IN (SELECT contact_id FROM contact_tags WHERE tag_id = ?)"
        args.append(int(row["tag_id"]))
    if row["status"] == "unsubscribed":
        return []
    found = _rows(
        f"""
        SELECT c.email FROM contacts c
        LEFT JOIN suppression s ON s.email = c.email AND s.company_id = c.company_id
        {where}
        """,
        tuple(args),
    )
    return [item["email"] for item in found]


def group_emails(group_id: int) -> list[str]:
    rows = _rows(
        """
        SELECT c.email FROM contacts c
        JOIN contact_groups cg ON cg.contact_id = c.id
        LEFT JOIN suppression s ON s.email = c.email AND s.company_id = c.company_id
        WHERE cg.group_id = ? AND s.email IS NULL
        """,
        (group_id,),
    )
    return [row["email"] for row in rows]


def search_activity(query: str, campaign_id: str, kind: str, outcome: str, page: int) -> dict:
    where = "WHERE e.company_id = ?"
    args: list = [current_company()]
    if query.strip():
        where += " AND lower(e.message) LIKE ?"
        args.append(f"%{query.strip().lower()}%")
    if campaign_id.isdigit():
        where += " AND e.campaign_id = ?"
        args.append(int(campaign_id))
    patterns = {
        "sent": "Sent %",
        "failed": "Skipped %",
        "retry": "%asked to wait%",
        "quota": "%out of daily quota%",
        "auth": "%rejected the secret%",
        "import": "Imported %",
        "started": "Started",
        "paused": "Paused",
        "resumed": "Resumed%",
        "finished": "Queue finished",
    }
    if kind in patterns:
        where += " AND e.message LIKE ?"
        args.append(patterns[kind])
    if outcome == "success":
        where += " AND (e.message LIKE 'Sent %' OR e.message IN ('Started', 'Queue finished', 'Paused'))"
    elif outcome == "failure":
        where += " AND (e.message LIKE 'Skipped %' OR e.message LIKE '%rejected the secret%' OR e.message LIKE '%out of daily quota%')"
    page_size = 30
    total = _one(f"SELECT COUNT(*) AS n FROM events e {where}", tuple(args))
    rows = _rows(
        f"""
        SELECT e.id, e.created_at, e.message, e.campaign_id, c.name AS campaign_name
        FROM events e
        LEFT JOIN campaigns c ON c.id = e.campaign_id
        {where}
        ORDER BY e.id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(args + [page_size, max(0, page) * page_size]),
    )
    count = int(total["n"] if total else 0)
    return {"rows": rows, "total": count, "page": page, "pages": max(1, (count + page_size - 1) // page_size)}


def quota_messages() -> list[str]:
    return [row["message"] for row in _rows("SELECT message FROM events WHERE message LIKE '%out of daily quota%'")]


def domain_stats() -> list[dict]:
    return _rows(
        """
        SELECT lower(substr(email, instr(email, '@') + 1)) AS domain,
          SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM recipients
        WHERE instr(email, '@') > 0
          AND campaign_id IN (SELECT id FROM campaigns WHERE company_id = ?)
        GROUP BY domain
        ORDER BY sent DESC
        LIMIT 12
        """,
        (current_company(),),
    )


def user_count() -> int:
    row = _one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"] if row else 0)


def public_user(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {key: row[key] for key in ("id", "email", "name", "role", "company_id", "status", "created_at", "last_login") if key in row}


def get_user(user_id: int) -> Optional[dict]:
    return public_user(_one("SELECT * FROM users WHERE id = ?", (user_id,)))


def user_by_email(email: str) -> Optional[dict]:
    return _one("SELECT * FROM users WHERE lower(email) = ?", (email.strip().lower(),))


def create_user(email: str, name: str, password_hash: str, role: str, company_id: Optional[int]) -> dict:
    if role not in {"super_admin", "company_user"}:
        raise ValueError("Unknown role")
    if role == "company_user" and not company_id:
        raise ValueError("A company user needs a company")
    if role == "super_admin":
        company_id = None
    user_id = _write(
        """
        INSERT INTO users (email, name, password_hash, role, company_id, status, created_at, last_login)
        VALUES (?, ?, ?, ?, ?, 'active', ?, '')
        """,
        (email.strip().lower(), name.strip(), password_hash, role, company_id, now()),
    )
    return get_user(user_id)


def touch_login(user_id: int) -> None:
    _write("UPDATE users SET last_login = ? WHERE id = ?", (now(), user_id))


def set_user_password(user_id: int, password_hash: str) -> None:
    _write("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def set_user_status(user_id: int, status: str) -> None:
    if status not in {"active", "suspended"}:
        raise ValueError("Unknown status")
    _write("UPDATE users SET status = ? WHERE id = ?", (status, user_id))


def update_user(user_id: int, name: str, email: str, company_id: Optional[int]) -> None:
    _write(
        "UPDATE users SET name = ?, email = ?, company_id = ? WHERE id = ? AND role = 'company_user'",
        (name.strip(), email.strip().lower(), company_id, user_id),
    )


def delete_user(user_id: int) -> None:
    _write("DELETE FROM users WHERE id = ? AND role != 'super_admin'", (user_id,))


def list_users() -> list[dict]:
    return _rows(
        """
        SELECT u.id, u.email, u.name, u.role, u.company_id, u.status, u.created_at, u.last_login, c.name AS company_name
        FROM users u
        LEFT JOIN companies c ON c.id = u.company_id
        ORDER BY u.id DESC
        """
    )


def get_company(company_id: int) -> Optional[dict]:
    return _one("SELECT * FROM companies WHERE id = ?", (company_id,))


def list_companies() -> list[dict]:
    return _rows(
        """
        SELECT c.*,
          (SELECT COUNT(*) FROM users u WHERE u.company_id = c.id) AS users,
          (SELECT COUNT(*) FROM campaigns p WHERE p.company_id = c.id) AS campaigns
        FROM companies c
        ORDER BY c.id DESC
        """
    )


def create_company(name: str, email: str, contact_name: str, phone: str) -> dict:
    company_id = _write(
        """
        INSERT INTO companies (name, email, contact_name, phone, status, created_at)
        VALUES (?, ?, ?, ?, 'active', ?)
        """,
        (name.strip(), email.strip().lower(), contact_name.strip(), phone.strip(), now()),
    )
    return get_company(company_id)


def update_company(company_id: int, name: str, email: str, contact_name: str, phone: str, status: str) -> None:
    if status not in {"active", "suspended"}:
        raise ValueError("Unknown status")
    _write(
        """
        UPDATE companies
        SET name = ?, email = ?, contact_name = ?, phone = ?, status = ?
        WHERE id = ?
        """,
        (name.strip(), email.strip().lower(), contact_name.strip(), phone.strip(), status, company_id),
    )


def delete_company(company_id: int) -> None:
    users = _one("SELECT COUNT(*) AS n FROM users WHERE company_id = ?", (company_id,))
    campaigns = _one("SELECT COUNT(*) AS n FROM campaigns WHERE company_id = ?", (company_id,))
    if int(users["n"] or 0) or int(campaigns["n"] or 0):
        raise ValueError("Suspend this company, or remove its users and campaigns first. Company data is not deleted automatically.")
    _write("DELETE FROM companies WHERE id = ?", (company_id,))


def admin_summary() -> dict:
    def count(sql: str) -> int:
        row = _one(sql)
        return int(row["n"] if row else 0)
    return {
        "companies": count("SELECT COUNT(*) AS n FROM companies"),
        "activeCompanies": count("SELECT COUNT(*) AS n FROM companies WHERE status = 'active'"),
        "suspendedCompanies": count("SELECT COUNT(*) AS n FROM companies WHERE status = 'suspended'"),
        "users": count("SELECT COUNT(*) AS n FROM users"),
        "activeUsers": count("SELECT COUNT(*) AS n FROM users WHERE status = 'active'"),
        "campaigns": count("SELECT COUNT(*) AS n FROM campaigns"),
        "recentCompanies": _rows("SELECT id, name, email, status, created_at FROM companies ORDER BY id DESC LIMIT 6"),
        "recentUsers": _rows(
            """
            SELECT u.name, u.email, u.last_login, u.status, c.name AS company_name
            FROM users u LEFT JOIN companies c ON c.id = u.company_id
            ORDER BY u.id DESC LIMIT 8
            """
        ),
    }


def running_campaign_id() -> Optional[int]:
    row = _one(
        "SELECT id FROM campaigns WHERE company_id = ? AND status IN ('running', 'paused') ORDER BY id DESC LIMIT 1",
        (current_company(),),
    )
    return int(row["id"]) if row else None
