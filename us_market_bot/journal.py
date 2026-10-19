"""Append-only research ledger and conservative at-most-once delivery claims."""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_editions (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  asof TEXT NOT NULL,
  created_at TEXT NOT NULL,
  body TEXT NOT NULL,
  payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_events (
  id INTEGER PRIMARY KEY,
  edition_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefing_delivery (
  edition_id TEXT NOT NULL,
  chunk INTEGER NOT NULL,
  status TEXT NOT NULL,
  claimed_at TEXT NOT NULL,
  message_id TEXT,
  PRIMARY KEY(edition_id, chunk)
);

CREATE TRIGGER IF NOT EXISTS immutable_editions_update 
BEFORE UPDATE ON research_editions 
BEGIN 
  SELECT RAISE(ABORT, 'research editions are immutable'); 
END;

CREATE TRIGGER IF NOT EXISTS immutable_editions_delete 
BEFORE DELETE ON research_editions 
BEGIN 
  SELECT RAISE(ABORT, 'research editions are immutable'); 
END;

CREATE TRIGGER IF NOT EXISTS immutable_events_update 
BEFORE UPDATE ON research_events 
BEGIN 
  SELECT RAISE(ABORT, 'research events are immutable'); 
END;

CREATE TRIGGER IF NOT EXISTS immutable_events_delete 
BEFORE DELETE ON research_events 
BEGIN 
  SELECT RAISE(ABORT, 'research events are immutable'); 
END;
"""

class DatetimeEncoder(json.JSONEncoder):
    """datetime 객체를 ISO 형식 문자열로 안전하게 변환하는 커스텀 인코더"""
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)

class Journal:
    def __init__(self, path):
        self.path = str(path)
        # 데이터베이스 폴더가 없으면 자동 생성
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        
        with self.connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, kind, day, asof, body, payload, *, preview=False):
        edition_id = f"{kind}:{day}"
        if preview:
            edition_id = f"preview:{edition_id}:{hashlib.sha256(asof.isoformat().encode()).hexdigest()[:12]}"
        
        serialized_payload = json.dumps(payload, cls=DatetimeEncoder, ensure_ascii=False)

        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO research_editions VALUES (?,?,?,?,?,?,?)",
                (
                    edition_id,
                    kind,
                    day,
                    asof.isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    body,
                    serialized_payload,
                ),
            )
        return self.get(edition_id)

    def get(self, edition_id):
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM research_editions WHERE id=?", (edition_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def latest(self, kind):
        with self.connect() as c:
            row = c.execute(
                "SELECT id FROM research_editions WHERE kind=? AND id NOT LIKE 'preview:%' ORDER BY trade_date DESC LIMIT 1",
                (kind,),
            ).fetchone()
        return self.get(row[0]) if row else None

    def delivery_state(self, edition_id: str, chunk: int) -> str | None:
        """메시지 전송 상태를 확인합니다."""
        with self.connect() as c:
            row = c.execute(
                "SELECT status FROM briefing_delivery WHERE edition_id=? AND chunk=?",
                (edition_id, chunk),
            ).fetchone()
        return row["status"] if row else None

    def claim(self, edition_id: str, chunk: int) -> bool:
        """중복 전송 방지를 위해 전송 권한을 선점(Claim)합니다."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            try:
                c.execute(
                    "INSERT INTO briefing_delivery (edition_id, chunk, status, claimed_at) VALUES (?, ?, 'pending', ?)",
                    (edition_id, chunk, now_str),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def delivered(self, edition_id: str, chunk: int, message_id: int) -> None:
        """전송 완료 상태와 메시지 ID를 기록합니다."""
        with self.connect() as c:
            c.execute(
                "UPDATE briefing_delivery SET status='sent', message_id=? WHERE edition_id=? AND chunk=?",
                (str(message_id), edition_id, chunk),
            )
