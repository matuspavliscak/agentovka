"""Local message archive.

ISDS permanently deletes messages 90 days after delivery, and the Portál
občana auto-archiving is known to miss messages delivered by fiction. This
archive keeps everything that passes through ``download_message``:

    <archive_dir>/<environment>/<message_id>/
        message.zfo          — original signed message, verbatim
        metadata.json        — envelope, timestamps, sha256 of the ZFO
        attachments/<name>   — extracted files

plus an SQLite database (index.db) with an FTS5 full-text index over subject,
sender, recipient and extracted attachment text.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isds_client.models import DeliveryEvent, DmFile, MessageEnvelope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    recipient TEXT,
    delivery_time TEXT,
    acceptance_time TEXT,
    status INTEGER,
    zfo_sha256 TEXT,
    archived_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (message_id, environment)
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id, subject, sender, recipient, body
);
"""

_TEXT_MIME = re.compile(r"^text/|[+/]xml$|^application/json$")

# A message_id becomes a filesystem directory name. It arrives from the ZFO /
# network (whose CMS signature we deliberately do not verify) or from an MCP
# tool parameter, so it is untrusted. ISDS message IDs are short alphanumeric
# tokens; anything else is rejected to prevent path traversal / arbitrary write.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class UnsafeIdentifierError(ValueError):
    """A message_id is not a safe filesystem path component."""


def _safe_component(value: str) -> str:
    if not value or value in (".", "..") or not _SAFE_COMPONENT.match(value):
        raise UnsafeIdentifierError(f"unsafe message id / path component: {value!r}")
    return value


def _attachment_text(files: list[DmFile]) -> str:
    """Best-effort plain text from attachments for the FTS index."""
    chunks: list[str] = []
    for f in files:
        if f.content and f.mime_type and _TEXT_MIME.search(f.mime_type):
            try:
                chunks.append(f.content.decode("utf-8", errors="replace")[:100_000])
            except Exception:
                continue
    return "\n".join(chunks)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ]", "_", name).strip()
    if cleaned in ("", ".", ".."):
        cleaned = "attachment.bin"
    return cleaned[:200]


@dataclass
class ArchivedMessage:
    message_id: str
    environment: str
    subject: str | None
    sender: str | None
    recipient: str | None
    delivery_time: str | None
    acceptance_time: str | None
    archived_at: str | None
    directory: Path


class Archive:
    def __init__(self, root: Path, environment: str) -> None:
        self.root = root
        self.environment = environment
        self.root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.root / "index.db")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # -- writing ---------------------------------------------------------

    def store(
        self,
        envelope: MessageEnvelope,
        zfo_bytes: bytes,
        files: list[DmFile],
        events: list[DeliveryEvent] | None = None,
    ) -> Path:
        """Persist a downloaded message; idempotent per (message_id, environment)."""
        message_id = _safe_component(envelope.message_id)
        msg_dir = self.root / self.environment / message_id
        att_dir = msg_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)

        (msg_dir / "message.zfo").write_bytes(zfo_bytes)

        saved_files: list[dict[str, Any]] = []
        for f in files:
            if f.content is None:
                continue
            target = att_dir / _safe_filename(f.file_name)
            target.write_bytes(f.content)
            saved_files.append(
                {
                    "file_name": target.name,
                    "mime_type": f.mime_type,
                    "meta_type": f.meta_type,
                    "size": len(f.content),
                }
            )

        metadata = {
            "envelope": envelope.model_dump(mode="json", by_alias=True),
            "environment": self.environment,
            "zfo_sha256": hashlib.sha256(zfo_bytes).hexdigest(),
            "attachments": saved_files,
            "delivery_events": [e.model_dump(mode="json", by_alias=True) for e in (events or [])],
        }
        (msg_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        with self._db:
            self._db.execute(
                "DELETE FROM messages_fts WHERE message_id = ?", (envelope.message_id,)
            )
            self._db.execute(
                """INSERT OR REPLACE INTO messages
                   (message_id, environment, subject, sender, recipient,
                    delivery_time, acceptance_time, status, zfo_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    envelope.message_id,
                    self.environment,
                    envelope.subject,
                    envelope.sender_name,
                    envelope.recipient_name,
                    envelope.delivery_time.isoformat() if envelope.delivery_time else None,
                    envelope.acceptance_time.isoformat() if envelope.acceptance_time else None,
                    int(envelope.status) if envelope.status is not None else None,
                    metadata["zfo_sha256"],
                ),
            )
            self._db.execute(
                "INSERT INTO messages_fts (message_id, subject, sender, recipient, body)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    envelope.message_id,
                    envelope.subject or "",
                    envelope.sender_name or "",
                    envelope.recipient_name or "",
                    _attachment_text(files),
                ),
            )
        return msg_dir

    # -- reading ---------------------------------------------------------

    def _row_to_message(self, row: sqlite3.Row) -> ArchivedMessage:
        return ArchivedMessage(
            message_id=row["message_id"],
            environment=row["environment"],
            subject=row["subject"],
            sender=row["sender"],
            recipient=row["recipient"],
            delivery_time=row["delivery_time"],
            acceptance_time=row["acceptance_time"],
            archived_at=row["archived_at"],
            directory=self.root / row["environment"] / row["message_id"],
        )

    def list_messages(self, limit: int = 100) -> list[ArchivedMessage]:
        self._db.row_factory = sqlite3.Row
        rows = self._db.execute(
            "SELECT * FROM messages WHERE environment = ? ORDER BY delivery_time DESC LIMIT ?",
            (self.environment, limit),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[ArchivedMessage]:
        """Full-text search over subject/sender/recipient/attachment text."""
        self._db.row_factory = sqlite3.Row
        rows = self._db.execute(
            """SELECT m.* FROM messages_fts f
               JOIN messages m ON m.message_id = f.message_id AND m.environment = ?
               WHERE messages_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (self.environment, query, limit),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def get(self, message_id: str) -> dict[str, Any] | None:
        """Load metadata.json for one archived message, or None."""
        try:
            message_id = _safe_component(message_id)
        except UnsafeIdentifierError:
            return None
        meta_path = self.root / self.environment / message_id / "metadata.json"
        if not meta_path.exists():
            return None
        data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        data["directory"] = str(meta_path.parent)
        return data

    def read_attachment(self, message_id: str, file_name: str) -> bytes | None:
        try:
            message_id = _safe_component(message_id)
        except UnsafeIdentifierError:
            return None
        att = self.root / self.environment / message_id / "attachments" / file_name
        # Guard against path traversal via crafted file names.
        if not att.resolve().is_relative_to((self.root / self.environment).resolve()):
            return None
        if not att.exists():
            return None
        return att.read_bytes()
