"""Tests for the MCP safety model: acknowledge_delivery_trigger and send guards."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

import agentovka_mcp.server as server
from agentovka_mcp.archive import Archive
from agentovka_mcp.config import Settings
from isds_client.client import IsdsEnvironment


class _StubClient:
    def __init__(self) -> None:
        self.received_called = False
        self.download_called = False
        self.sent_message: dict[str, Any] | None = None

    def get_list_of_received_messages(self, limit: int = 100) -> list[Any]:
        self.received_called = True
        return []

    def signed_message_download(self, message_id: str) -> bytes:
        self.download_called = True
        return b""

    def create_message(self, recipient_id: str, subject: str, files: list[Any], **kw: Any) -> str:
        self.sent_message = {"recipient": recipient_id, "subject": subject, "files": files}
        return "42"


@pytest.fixture(autouse=True)
def reset_singletons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    stub = _StubClient()
    settings = Settings(
        username="u",
        password="p",
        environment=IsdsEnvironment.TEST,
        archive_dir=tmp_path / "arch",
        allow_send=False,
    )
    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_client", stub)
    monkeypatch.setattr(server, "_archive", Archive(tmp_path / "arch", "test"))
    return stub


def test_list_received_requires_acknowledgement(reset_singletons: _StubClient) -> None:
    result = server.list_received_messages(acknowledge_delivery_trigger=False)
    assert result["error"] == "delivery_trigger_not_acknowledged"
    assert reset_singletons.received_called is False
    assert "EV13" in result["explanation"]


def test_list_received_proceeds_when_acknowledged(reset_singletons: _StubClient) -> None:
    result = server.list_received_messages(acknowledge_delivery_trigger=True)
    assert result["delivery_triggered"] is True
    assert reset_singletons.received_called is True


def test_download_requires_acknowledgement(reset_singletons: _StubClient) -> None:
    result = server.download_message("123", acknowledge_delivery_trigger=False)
    assert result["error"] == "delivery_trigger_not_acknowledged"
    assert reset_singletons.download_called is False


def test_send_dry_run_is_default(reset_singletons: _StubClient) -> None:
    result = server.send_message(
        "aaaaaaa",
        "Test",
        [
            {
                "file_name": "a.pdf",
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(b"x").decode(),
            }
        ],
    )
    assert result["dry_run"] is True
    assert reset_singletons.sent_message is None


def test_send_blocked_when_allow_send_false(reset_singletons: _StubClient) -> None:
    result = server.send_message(
        "aaaaaaa",
        "Test",
        [
            {
                "file_name": "a.pdf",
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(b"x").decode(),
            }
        ],
        dry_run=False,
    )
    assert result["error"] == "sending_disabled"
    assert reset_singletons.sent_message is None


def test_send_succeeds_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClient()
    settings = Settings(
        username="u",
        password="p",
        environment=IsdsEnvironment.TEST,
        archive_dir=tmp_path / "arch",
        allow_send=True,
    )
    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_client", stub)
    monkeypatch.setattr(server, "_archive", Archive(tmp_path / "arch", "test"))

    result = server.send_message(
        "aaaaaaa",
        "Test",
        [
            {
                "file_name": "a.pdf",
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(b"x").decode(),
            }
        ],
        dry_run=False,
    )
    assert result["sent"] is True
    assert result["message_id"] == "42"
    assert stub.sent_message is not None
    assert stub.sent_message["recipient"] == "aaaaaaa"


def test_delivery_deadline_tool(reset_singletons: _StubClient) -> None:
    result = server.get_delivery_deadline("2026-06-01", today="2026-06-05")
    assert result["fiction_delivery_date"] == "2026-06-11"
    assert result["days_remaining"] == 6


def _att(name: str, meta: str | None = None) -> dict[str, str]:
    d = {
        "file_name": name,
        "mime_type": "application/pdf",
        "content_base64": base64.b64encode(b"x").decode(),
    }
    if meta is not None:
        d["meta_type"] = meta
    return d


def test_single_attachment_defaults_to_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubClient()
    settings = Settings("u", "p", IsdsEnvironment.TEST, tmp_path / "a", allow_send=True)
    monkeypatch.setattr(server, "_settings", settings)
    monkeypatch.setattr(server, "_client", stub)
    monkeypatch.setattr(server, "_archive", Archive(tmp_path / "a", "test"))
    result = server.send_message("aaaaaaa", "Test", [_att("a.pdf")], dry_run=False)
    assert result["sent"] is True
    assert stub.sent_message["files"][0]["meta_type"] == "main"


def test_multiple_attachments_without_main_rejected(reset_singletons: _StubClient) -> None:
    # Two attachments, neither marked main -> both default to enclosure -> 0 mains.
    result = server.send_message("aaaaaaa", "Test", [_att("a.pdf"), _att("b.pdf")], dry_run=True)
    assert result["error"] == "invalid_attachments"
    assert reset_singletons.sent_message is None


def test_multiple_attachments_with_two_mains_rejected(reset_singletons: _StubClient) -> None:
    result = server.send_message(
        "aaaaaaa", "Test", [_att("a.pdf", "main"), _att("b.pdf", "main")], dry_run=True
    )
    assert result["error"] == "invalid_attachments"


def test_multiple_attachments_one_main_ok(reset_singletons: _StubClient) -> None:
    result = server.send_message(
        "aaaaaaa", "Test", [_att("a.pdf", "main"), _att("b.pdf", "enclosure")], dry_run=True
    )
    assert result["dry_run"] is True
    metas = [a["meta_type"] for a in result["would_send"]["attachments"]]
    assert metas == ["main", "enclosure"]


def test_read_archived_message_uses_pythonic_keys(
    reset_singletons: _StubClient, sample_zfo: bytes
) -> None:
    from isds_client.zfo import parse_zfo

    parsed = parse_zfo(sample_zfo)
    server.get_archive().store(parsed.envelope, sample_zfo, parsed.files)
    data = server.read_archived_message("10123456")
    # Same key shape as download_message/list_* tools, not the dmID aliases.
    assert data["envelope"]["message_id"] == "10123456"
    assert "dmID" not in data["envelope"]


def test_search_archive_reports_bad_query_as_error(
    reset_singletons: _StubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    def boom(query: str, limit: int = 50) -> list[Any]:
        raise sqlite3.OperationalError("fts5: syntax error")

    monkeypatch.setattr(server.get_archive(), "search", boom)
    result = server.search_archive("((((")
    assert "error" in result


def test_delivery_deadline_rejects_bad_today(reset_singletons: _StubClient) -> None:
    result = server.get_delivery_deadline("2026-06-01", today="7/5/2026")
    assert result["error"] == "today must be ISO format YYYY-MM-DD"
