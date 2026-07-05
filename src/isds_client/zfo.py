"""Parser for ZFO files (signed ISDS data messages).

A ZFO file is a DER-encoded CMS/PKCS#7 SignedData structure (RFC 5652) whose
encapsulated content is the XML of the ISDS SOAP response - either a signed
data message (``MessageDownloadResponse``/``SignedMessageDownloadResponse``
body containing ``dmReturnedMessage``) or a signed delivery receipt
(``GetDeliveryInfoResponse`` containing ``dmDelivery``).

The same container format is produced by ``SignedMessageDownload``,
``SignedSentMessageDownload`` and ``GetSignedDeliveryInfo`` and used by the
official Datovka desktop application (CZ.NIC) and libisds/libdatovka, whose
handling of the format this implementation follows.

This module extracts the inner XML, the message envelope metadata, attachments
(base64-decoded) and delivery events. It does NOT validate the CMS signature -
the ZFO is preserved verbatim in the archive so the signature can always be
verified later by external tools.
"""

from __future__ import annotations

import base64
import contextlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from xml.etree.ElementTree import Element

from asn1crypto import cms as asn1_cms
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from isds_client.models import DeliveryEvent, DmFile, MessageEnvelope, MessageStatus


class ZfoParseError(ValueError):
    """The file is not a parseable ZFO / signed ISDS message."""


@dataclass
class ParsedZfo:
    """Result of parsing a ZFO file."""

    envelope: MessageEnvelope
    files: list[DmFile] = field(default_factory=list)
    events: list[DeliveryEvent] = field(default_factory=list)
    inner_xml: bytes = b""

    @property
    def is_delivery_receipt(self) -> bool:
        return not self.files and bool(self.events)


def extract_xml_from_cms(data: bytes) -> bytes:
    """Extract the encapsulated XML content from a CMS/PKCS#7 SignedData blob.

    Accepts raw DER as well as base64-wrapped DER (ISDS sometimes hands out
    base64 text). Raises ZfoParseError if the structure is not SignedData or
    carries no content.
    """
    der = data
    if not der.lstrip().startswith(b"\x30"):
        # Not DER - try base64 (possibly with whitespace/newlines).
        try:
            der = base64.b64decode(re.sub(rb"\s+", b"", data), validate=True)
        except Exception as exc:
            raise ZfoParseError("input is neither DER nor base64-encoded DER") from exc

    try:
        content_info = asn1_cms.ContentInfo.load(der)
    except Exception as exc:
        raise ZfoParseError("cannot parse CMS ContentInfo") from exc

    if content_info["content_type"].native != "signed_data":
        raise ZfoParseError(f"unexpected CMS content type: {content_info['content_type'].native!r}")

    signed_data = content_info["content"]
    encap = signed_data["encap_content_info"]
    inner = encap["content"]
    if inner is None:
        raise ZfoParseError("CMS SignedData carries no encapsulated content (detached signature)")
    xml_bytes: bytes = inner.native
    return xml_bytes


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_first(root: Element, local_name: str) -> Element | None:
    for el in root.iter():
        if _strip_ns(el.tag) == local_name:
            return el
    return None


def _global_text(root: Element, local_name: str) -> str | None:
    el = _find_first(root, local_name)
    return el.text if el is not None else None


def _child_map(el: Element) -> dict[str, Any]:
    return {_strip_ns(child.tag): (child.text or None) for child in el}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_envelope(dm: Element) -> MessageEnvelope:
    values = _child_map(dm)
    # dmID may live on the dmDm element itself (attribute-less child) or on
    # the enclosing dmReturnedMessage; fall back to empty string upstream.
    status_raw = values.get("dmMessageStatus")
    status: MessageStatus | None = None
    if status_raw is not None:
        try:
            status = MessageStatus(int(status_raw))
        except ValueError:
            status = None
    return MessageEnvelope(
        dmID=values.get("dmID") or "",
        dbIDSender=values.get("dbIDSender"),
        dmSender=values.get("dmSender"),
        dmSenderAddress=values.get("dmSenderAddress"),
        dbIDRecipient=values.get("dbIDRecipient"),
        dmRecipient=values.get("dmRecipient"),
        dmRecipientAddress=values.get("dmRecipientAddress"),
        dmAnnotation=values.get("dmAnnotation"),
        dmMessageStatus=status,
        dmDeliveryTime=_parse_time(values.get("dmDeliveryTime")),
        dmAcceptanceTime=_parse_time(values.get("dmAcceptanceTime")),
        dmAttachmentSize=int(values["dmAttachmentSize"])
        if values.get("dmAttachmentSize")
        else None,
        dmPersonalDelivery=values.get("dmPersonalDelivery") in ("true", "1"),
        dmSenderRefNumber=values.get("dmSenderRefNumber"),
        dmRecipientRefNumber=values.get("dmRecipientRefNumber"),
        dmToHands=values.get("dmToHands"),
        dmLegalTitleLaw=values.get("dmLegalTitleLaw"),
        dmLegalTitleSect=values.get("dmLegalTitleSect"),
    )


def _parse_files(root: Element) -> list[DmFile]:
    files: list[DmFile] = []
    for el in root.iter():
        if _strip_ns(el.tag) != "dmFile":
            continue
        content: bytes | None = None
        for child in el:
            if _strip_ns(child.tag) == "dmEncodedContent":
                # An empty element is a legal zero-byte attachment, not a
                # missing one - decode to b"" so it still gets archived.
                text = child.text or ""
                content = base64.b64decode(re.sub(r"\s+", "", text))
        files.append(
            DmFile(
                file_name=el.get("dmFileDescr") or "attachment.bin",
                mime_type=el.get("dmMimeType"),
                meta_type=el.get("dmFileMetaType"),
                size=len(content) if content is not None else None,
                content=content,
            )
        )
    return files


def _parse_events(root: Element) -> list[DeliveryEvent]:
    events: list[DeliveryEvent] = []
    for el in root.iter():
        if _strip_ns(el.tag) != "dmEvent":
            continue
        values = _child_map(el)
        events.append(
            DeliveryEvent(
                dmEventTime=_parse_time(values.get("dmEventTime")),
                dmEventDescr=values.get("dmEventDescr"),
            )
        )
    return events


def parse_zfo(data: bytes) -> ParsedZfo:
    """Parse a ZFO file (or raw signed-message CMS blob) into structured data."""
    xml_bytes = extract_xml_from_cms(data)
    try:
        root = fromstring(xml_bytes)
    except ParseError as exc:
        raise ZfoParseError("CMS content is not valid XML") from exc
    except DefusedXmlException as exc:
        raise ZfoParseError(f"CMS content contains forbidden XML constructs: {exc}") from exc

    dm = _find_first(root, "dmDm")
    envelope: MessageEnvelope
    if dm is not None:
        envelope = _parse_envelope(dm)
    else:
        # Delivery receipts (dmDelivery) put envelope fields directly under dmHash siblings.
        delivery = _find_first(root, "dmDelivery")
        if delivery is None:
            raise ZfoParseError("XML contains neither dmDm nor dmDelivery")
        envelope = _parse_envelope(delivery)

    if not envelope.message_id:
        # dmID is an element inside dmDm in download responses; in some receipt
        # variants it sits on the wrapping element - try a global lookup.
        dm_id_el = _find_first(root, "dmID")
        if dm_id_el is not None and dm_id_el.text:
            envelope = envelope.model_copy(update={"message_id": dm_id_el.text})

    # Per the ISDS schema (tReturnedMessage / tDelivery in dmBaseTypes.xsd) the
    # delivery timestamps, status and attachment size are SIBLINGS of dmDm, not
    # its children - fill them in from a document-wide lookup when the dmDm
    # parse left them empty. (The message contains each element at most once.)
    updates: dict[str, Any] = {}
    if envelope.delivery_time is None:
        updates["delivery_time"] = _parse_time(_global_text(root, "dmDeliveryTime"))
    if envelope.acceptance_time is None:
        updates["acceptance_time"] = _parse_time(_global_text(root, "dmAcceptanceTime"))
    if envelope.status is None:
        status_raw = _global_text(root, "dmMessageStatus")
        if status_raw is not None:
            with contextlib.suppress(ValueError):
                updates["status"] = MessageStatus(int(status_raw))
    if envelope.attachment_size_kb is None:
        size_raw = _global_text(root, "dmAttachmentSize")
        if size_raw is not None and size_raw.isdigit():
            updates["attachment_size_kb"] = int(size_raw)
    if updates:
        envelope = envelope.model_copy(update=updates)

    return ParsedZfo(
        envelope=envelope,
        files=_parse_files(root),
        events=_parse_events(root),
        inner_xml=xml_bytes,
    )
