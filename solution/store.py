"""SQLite persistence for leads, provider requests, and webhook receipts."""

from __future__ import annotations

import csv
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from datetime import datetime, timedelta
from solution.scheduling import as_ist, parse_instant



SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS leads (
    campaign_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    customer_name TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'ready'
        CHECK (state IN ('ready', 'completed', 'exhausted', 'dnd_blocked')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_status TEXT NOT NULL DEFAULT '',
    last_disposition TEXT NOT NULL DEFAULT '',
    next_at TEXT,
    next_kind TEXT NOT NULL DEFAULT 'fresh'
        CHECK (next_kind IN ('callback', 'retry', 'fresh')),
    PRIMARY KEY (campaign_id, lead_id)
);

CREATE TABLE IF NOT EXISTS calls (
    request_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    phone TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'prepared'
        CHECK (state IN ('prepared', 'active', 'ended')),
    call_sid TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT '',
    disposition TEXT NOT NULL DEFAULT '',
    callback_at TEXT,
    started_at TEXT,
    ended_at TEXT,
    UNIQUE (campaign_id, lead_id, attempt_no),
    FOREIGN KEY (campaign_id, lead_id) REFERENCES leads(campaign_id, lead_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_call_per_phone
    ON calls(phone) WHERE state = 'active';
CREATE INDEX IF NOT EXISTS calls_by_lead ON calls(campaign_id, lead_id, attempt_no);
CREATE INDEX IF NOT EXISTS leads_by_schedule ON leads(state, next_at, next_kind);

CREATE TABLE IF NOT EXISTS webhook_receipts (
    call_sid TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    raw_body BLOB NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dnd_phones (
    phone TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL
);
"""


class Store:
    """Small synchronous SQLite repository; the dispatcher uses one process."""

    def __init__(self, database: str | Path):
        self.path = str(database)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize local writers and commit/rollback each state transition."""
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def seed_leads(self, fixture_dir: str | Path, campaigns: list[dict]) -> int:
        """Load fixture CSVs once; repeated startup is safe and preserves progress."""
        fixture_dir = Path(fixture_dir)
        inserted = 0
        with self.transaction() as db:
            for campaign in campaigns:
                file_path = fixture_dir / campaign["leads_file"]
                with file_path.open("r", newline="", encoding="utf-8") as source:
                    for row in csv.DictReader(source):
                        cursor = db.execute(
                            """INSERT OR IGNORE INTO leads
                               (campaign_id, lead_id, tenant_id, phone, customer_name)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                campaign["campaign_id"],
                                row["lead_id"].strip(),
                                campaign["tenant_id"],
                                row["phone"].strip(),
                                row.get("customer_name", "").strip(),
                            ),
                        )
                        inserted += cursor.rowcount
        return inserted

    def reserve_call(self, campaign_id: str, lead_id: str) -> sqlite3.Row:
        """Persist a deterministic request ID before contacting the provider.

        Reopening a prepared call returns the same row and therefore the same
        provider idempotency key after a crash.
        """
        with self.transaction() as db:
            lead = db.execute(
                "SELECT * FROM leads WHERE campaign_id=? AND lead_id=?",
                (campaign_id, lead_id),
            ).fetchone()
            if lead is None:
                raise KeyError((campaign_id, lead_id))
            if lead["state"] != "ready":
                raise ValueError(f"lead is not callable: {lead['state']}")
            attempt_no = lead["attempts"] + 1
            request_id = f"{campaign_id}:{lead_id}:{attempt_no}"
            db.execute(
                """INSERT OR IGNORE INTO calls
                   (request_id, campaign_id, lead_id, attempt_no, phone, state)
                   VALUES (?, ?, ?, ?, ?, 'prepared')""",
                (request_id, campaign_id, lead_id, attempt_no, lead["phone"]),
            )
            return db.execute(
                "SELECT * FROM calls WHERE request_id=?", (request_id,)
            ).fetchone()

    def mark_accepted(self, request_id: str, call_sid: str, started_at: str = "") -> None:
        """Record provider acceptance; only this transition consumes an attempt."""
        with self.transaction() as db:
            call = db.execute(
                "SELECT campaign_id, lead_id, attempt_no FROM calls WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if call is None:
                raise KeyError(request_id)

            db.execute(
                """UPDATE calls
                   SET state = CASE WHEN state='ended' THEN 'ended' ELSE 'active' END,
                       call_sid = COALESCE(call_sid, ?),
                       started_at = COALESCE(NULLIF(?, ''), started_at)
                   WHERE request_id=?""",
                (call_sid, started_at, request_id),
            )
            db.execute(
                """UPDATE leads SET attempts = MAX(attempts, ?)
                   WHERE campaign_id=? AND lead_id=?""",
                (call["attempt_no"], call["campaign_id"], call["lead_id"]),
            )

    def eligible_leads(self, now: datetime) -> list[sqlite3.Row]:
        """Return ready leads whose scheduled time has arrived."""
        now_text = as_ist(now).isoformat()
        with self._lock:
            return self.connection.execute(
                """SELECT * FROM leads
                   WHERE state='ready'
                     AND (next_at IS NULL OR next_at <= ?)
                     AND NOT EXISTS (
                         SELECT 1 FROM calls
                         WHERE calls.campaign_id=leads.campaign_id
                           AND calls.lead_id=leads.lead_id
                           AND calls.state='active'
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM dnd_phones
                         WHERE dnd_phones.phone=leads.phone
                     )
                   ORDER BY campaign_id, lead_id""",
                (now_text,),
            ).fetchall()

    def outstanding_calls(self) -> list[sqlite3.Row]:
        """Return calls the dispatcher needs to reconcile after a restart."""
        with self._lock:
            return self.connection.execute(
                "SELECT * FROM calls WHERE state IN ('prepared', 'active')"
            ).fetchall()

    def active_phones(self) -> set[str]:
        """Get phone numbers that currently have a locally active call."""
        with self._lock:
            rows = self.connection.execute(
                "SELECT phone FROM calls WHERE state='active'"
            ).fetchall()
            return {row["phone"] for row in rows}

    def active_count_for_tenant(self, tenant_id: str) -> int:
        """Count active calls belonging to one tenant across its campaigns."""
        with self._lock:
            row = self.connection.execute(
                """SELECT COUNT(*) AS total
                   FROM calls
                   JOIN leads USING (campaign_id, lead_id)
                   WHERE calls.state='active' AND leads.tenant_id=?""",
                (tenant_id,),
            ).fetchone()
            return row["total"]

    def record_result(
        self,
        result: dict,
        raw_body: bytes,
        received_at: datetime,
        max_attempts: dict[str, int],
        retry_minutes: dict[str, int],
    ) -> bool:
        """Save one call result and update its lead; return False for a duplicate."""
        request_id = result["request_id"]
        call_sid = result["call_sid"]
        ended_at = parse_instant(result["ended_at"])
        status = result.get("status") or ""
        disposition = result.get("disposition") or ""
        callback_at = result.get("callback_at")
        callback_time = parse_instant(callback_at) if callback_at else None

        with self.transaction() as db:
            call = db.execute(
                "SELECT * FROM calls WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if call is None:
                raise KeyError(f"Unknown provider request: {request_id}")

            receipt = db.execute(
                """INSERT OR IGNORE INTO webhook_receipts
                   (call_sid, request_id, raw_body, received_at)
                   VALUES (?, ?, ?, ?)""",
                (call_sid, request_id, raw_body, as_ist(received_at).isoformat()),
            )
            if receipt.rowcount == 0:
                return False

            db.execute(
                """UPDATE calls
                   SET state='ended', call_sid=?, status=?, disposition=?,
                       callback_at=?, started_at=?, ended_at=?
                   WHERE request_id=?""",
                (
                    call_sid,
                    status,
                    disposition,
                    callback_time.isoformat() if callback_time else None,
                    result.get("started_at", ""),
                    ended_at.isoformat(),
                    request_id,
                ),
            )
            db.execute(
                """UPDATE leads
                   SET attempts=MAX(attempts, ?), last_status=?, last_disposition=?
                   WHERE campaign_id=? AND lead_id=?""",
                (
                    call["attempt_no"],
                    status,
                    disposition,
                    call["campaign_id"],
                    call["lead_id"],
                ),
            )

            if disposition == "dnd":
                db.execute(
                    "INSERT OR IGNORE INTO dnd_phones (phone, occurred_at) VALUES (?, ?)",
                    (call["phone"], ended_at.isoformat()),
                )
                other_leads = db.execute(
                    """SELECT campaign_id, lead_id, attempts, state
                       FROM leads
                       WHERE phone=? AND NOT (campaign_id=? AND lead_id=?)""",
                    (call["phone"], call["campaign_id"], call["lead_id"]),
                ).fetchall()
                for other in other_leads:
                    if other["state"] in ("completed", "exhausted"):
                        continue
                    blocked_state = (
                        "exhausted"
                        if other["attempts"] >= max_attempts[other["campaign_id"]]
                        else "dnd_blocked"
                    )
                    db.execute(
                        """UPDATE leads SET state=?
                           WHERE campaign_id=? AND lead_id=?""",
                        (blocked_state, other["campaign_id"], other["lead_id"]),
                    )
                db.execute(
                    """UPDATE leads SET state='completed', next_at=NULL
                       WHERE campaign_id=? AND lead_id=?""",
                    (call["campaign_id"], call["lead_id"]),
                )
                return True

            if disposition in {"interested", "not_interested", "wrong_number"}:
                db.execute(
                    """UPDATE leads SET state='completed', next_at=NULL
                       WHERE campaign_id=? AND lead_id=?""",
                    (call["campaign_id"], call["lead_id"]),
                )
            elif call["attempt_no"] >= max_attempts[call["campaign_id"]]:
                db.execute(
                    """UPDATE leads SET state='exhausted', next_at=NULL
                       WHERE campaign_id=? AND lead_id=?""",
                    (call["campaign_id"], call["lead_id"]),
                )
            elif disposition == "callback_requested":
                if callback_time is None:
                    raise ValueError("callback_requested result is missing callback_at")
                db.execute(
                    """UPDATE leads SET state='ready', next_at=?, next_kind='callback'
                       WHERE campaign_id=? AND lead_id=?""",
                    (callback_time.isoformat(), call["campaign_id"], call["lead_id"]),
                )
            elif status in retry_minutes:
                retry_at = ended_at + timedelta(minutes=retry_minutes[status])
                db.execute(
                    """UPDATE leads SET state='ready', next_at=?, next_kind='retry'
                       WHERE campaign_id=? AND lead_id=?""",
                    (retry_at.isoformat(), call["campaign_id"], call["lead_id"]),
                )
            else:
                db.execute(
                    """UPDATE leads SET state='ready', next_at=?, next_kind='fresh'
                       WHERE campaign_id=? AND lead_id=?""",
                    (ended_at.isoformat(), call["campaign_id"], call["lead_id"]),
                )

            return True

    def close(self) -> None:
        with self._lock:
            self.connection.close()
