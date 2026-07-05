import base64

import pytest

from conftest import build_cms, message_xml
from isds_client.models import MessageStatus
from isds_client.zfo import ZfoParseError, extract_xml_from_cms, parse_zfo


def test_parse_message_envelope(sample_zfo: bytes) -> None:
    parsed = parse_zfo(sample_zfo)
    env = parsed.envelope
    assert env.message_id == "10123456"
    assert env.sender_name == "Městský úřad Testov"
    assert env.subject == "Rozhodnutí o přestupku"
    assert env.status == MessageStatus.DELIVERED_BY_LOGIN
    assert env.delivery_time is not None
    assert env.delivery_time.year == 2026


def test_parse_attachments(sample_zfo: bytes) -> None:
    parsed = parse_zfo(sample_zfo)
    assert len(parsed.files) == 1
    f = parsed.files[0]
    assert f.file_name == "rozhodnuti.pdf"
    assert f.mime_type == "application/pdf"
    assert f.content == b"%PDF-1.4 fake"
    assert not parsed.is_delivery_receipt


def test_parse_delivery_receipt(receipt_zfo: bytes) -> None:
    parsed = parse_zfo(receipt_zfo)
    assert parsed.envelope.message_id == "10123456"
    assert parsed.is_delivery_receipt
    assert len(parsed.events) == 2
    assert "EV13" in (parsed.events[1].description or "")


def test_base64_wrapped_input(sample_zfo: bytes) -> None:
    wrapped = base64.encodebytes(sample_zfo)
    parsed = parse_zfo(wrapped)
    assert parsed.envelope.message_id == "10123456"


def test_extract_rejects_garbage() -> None:
    with pytest.raises(ZfoParseError):
        extract_xml_from_cms(b"not a zfo at all")


def test_extract_rejects_non_xml_content() -> None:
    blob = build_cms(b"binary \x00 garbage")
    with pytest.raises(ZfoParseError):
        parse_zfo(blob)


def test_unknown_status_is_tolerated() -> None:
    blob = build_cms(message_xml(status=99))
    parsed = parse_zfo(blob)
    assert parsed.envelope.status is None


def test_billion_laughs_is_rejected() -> None:
    # An internal-entity expansion bomb wrapped in a valid CMS envelope must be
    # refused by the hardened parser rather than exhausting memory.
    bomb = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
        b"]><q:dmDm xmlns:q='http://isds.czechpoint.cz/v20'>&lol3;</q:dmDm>"
    )
    with pytest.raises(ZfoParseError):
        parse_zfo(build_cms(bomb))
