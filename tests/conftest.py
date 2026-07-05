"""Shared test fixtures - most importantly a synthetic ZFO builder.

Real ZFO files contain personal data and qualified signatures, so tests build
a structurally equivalent CMS SignedData envelope around hand-written ISDS
response XML instead.
"""

from __future__ import annotations

import base64

import pytest
from asn1crypto import cms, core

ISDS_NS = "http://isds.czechpoint.cz/v20"


def build_cms(xml: bytes) -> bytes:
    """Wrap XML bytes in a minimal CMS SignedData structure (unsigned)."""
    signed = cms.SignedData(
        {
            "version": "v1",
            "digest_algorithms": [],
            "encap_content_info": {
                "content_type": "data",
                "content": core.OctetString(xml),
            },
            "signer_infos": [],
        }
    )
    info = cms.ContentInfo({"content_type": "signed_data", "content": signed})
    return info.dump()


def message_xml(
    message_id: str = "10123456",
    subject: str = "Rozhodnutí o přestupku",
    attachment_name: str = "rozhodnuti.pdf",
    attachment_bytes: bytes = b"%PDF-1.4 fake",
    status: int = 6,
) -> bytes:
    encoded = base64.b64encode(attachment_bytes).decode()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<q:MessageDownloadResponse xmlns:q="{ISDS_NS}">
  <q:dmReturnedMessage>
    <q:dmDm>
      <q:dmID>{message_id}</q:dmID>
      <q:dbIDSender>aaaaaaa</q:dbIDSender>
      <q:dmSender>Městský úřad Testov</q:dmSender>
      <q:dmSenderAddress>Náměstí 1, Testov</q:dmSenderAddress>
      <q:dbIDRecipient>bbbbbbb</q:dbIDRecipient>
      <q:dmRecipient>Jan Novák</q:dmRecipient>
      <q:dmAnnotation>{subject}</q:dmAnnotation>
      <q:dmFiles>
        <q:dmFile dmMimeType="application/pdf" dmFileMetaType="main"
                  dmFileDescr="{attachment_name}">
          <q:dmEncodedContent>{encoded}</q:dmEncodedContent>
        </q:dmFile>
      </q:dmFiles>
    </q:dmDm>
    <!-- Per tReturnedMessage in dmBaseTypes.xsd these are SIBLINGS of dmDm. -->
    <q:dmDeliveryTime>2026-06-01T10:00:00.000+02:00</q:dmDeliveryTime>
    <q:dmAcceptanceTime>2026-06-03T08:30:00.000+02:00</q:dmAcceptanceTime>
    <q:dmMessageStatus>{status}</q:dmMessageStatus>
    <q:dmAttachmentSize>1</q:dmAttachmentSize>
  </q:dmReturnedMessage>
</q:MessageDownloadResponse>""".encode()


def delivery_receipt_xml(message_id: str = "10123456") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<q:GetDeliveryInfoResponse xmlns:q="{ISDS_NS}">
  <q:dmDelivery>
    <q:dmDm>
      <q:dmID>{message_id}</q:dmID>
      <q:dmSender>Městský úřad Testov</q:dmSender>
      <q:dmRecipient>Jan Novák</q:dmRecipient>
      <q:dmAnnotation>Rozhodnutí o přestupku</q:dmAnnotation>
      <q:dmMessageStatus>6</q:dmMessageStatus>
    </q:dmDm>
    <q:dmEvents>
      <q:dmEvent>
        <q:dmEventTime>2026-06-01T10:00:00.000+02:00</q:dmEventTime>
        <q:dmEventDescr>EV5: Datová zpráva byla dodána do datové schránky příjemce.</q:dmEventDescr>
      </q:dmEvent>
      <q:dmEvent>
        <q:dmEventTime>2026-06-03T08:30:00.000+02:00</q:dmEventTime>
        <q:dmEventDescr>EV13: Přihlásila se elektronická aplikace za pomoci systémového certifikátu.</q:dmEventDescr>
      </q:dmEvent>
    </q:dmEvents>
  </q:dmDelivery>
</q:GetDeliveryInfoResponse>""".encode()


@pytest.fixture
def sample_zfo() -> bytes:
    return build_cms(message_xml())


@pytest.fixture
def receipt_zfo() -> bytes:
    return build_cms(delivery_receipt_xml())
