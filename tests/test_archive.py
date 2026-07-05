import json
from pathlib import Path

from agentovka_mcp.archive import Archive
from isds_client.zfo import parse_zfo


def make_archive(tmp_path: Path) -> Archive:
    return Archive(tmp_path / "archive", environment="test")


def test_store_and_get(tmp_path: Path, sample_zfo: bytes) -> None:
    archive = make_archive(tmp_path)
    parsed = parse_zfo(sample_zfo)
    msg_dir = archive.store(parsed.envelope, sample_zfo, parsed.files)

    assert (msg_dir / "message.zfo").read_bytes() == sample_zfo
    assert (msg_dir / "attachments" / "rozhodnuti.pdf").exists()

    meta = json.loads((msg_dir / "metadata.json").read_text())
    assert meta["envelope"]["dmID"] == "10123456"
    assert meta["zfo_sha256"]
    assert meta["attachments"][0]["file_name"] == "rozhodnuti.pdf"

    loaded = archive.get("10123456")
    assert loaded is not None
    assert loaded["envelope"]["dmAnnotation"] == "Rozhodnutí o přestupku"


def test_list_and_fulltext_search(tmp_path: Path, sample_zfo: bytes) -> None:
    archive = make_archive(tmp_path)
    parsed = parse_zfo(sample_zfo)
    archive.store(parsed.envelope, sample_zfo, parsed.files)

    listed = archive.list_messages()
    assert len(listed) == 1
    assert listed[0].subject == "Rozhodnutí o přestupku"

    hits = archive.search("přestupku")
    assert [m.message_id for m in hits] == ["10123456"]
    assert archive.search("neexistujici") == []


def test_store_is_idempotent(tmp_path: Path, sample_zfo: bytes) -> None:
    archive = make_archive(tmp_path)
    parsed = parse_zfo(sample_zfo)
    archive.store(parsed.envelope, sample_zfo, parsed.files)
    archive.store(parsed.envelope, sample_zfo, parsed.files)
    assert len(archive.list_messages()) == 1
    # FTS index must not contain duplicates either.
    assert len(archive.search("přestupku")) == 1


def test_read_attachment_blocks_traversal(tmp_path: Path, sample_zfo: bytes) -> None:
    archive = make_archive(tmp_path)
    parsed = parse_zfo(sample_zfo)
    archive.store(parsed.envelope, sample_zfo, parsed.files)
    assert archive.read_attachment("10123456", "rozhodnuti.pdf") == b"%PDF-1.4 fake"
    assert archive.read_attachment("10123456", "../../index.db") is None
