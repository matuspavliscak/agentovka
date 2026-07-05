"""Typed models for ISDS entities.

Field names follow the ISDS SOAP schema (dmID, dbIDSender, ...) where it helps
cross-referencing with the official documentation (Provozní řád ISDS), with
pythonic aliases for ergonomics.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageStatus(enum.IntEnum):
    """dmMessageStatus - lifecycle state of a data message.

    The delivery-relevant distinction is DELIVERED_TO_BOX (dodaná, not yet
    legally delivered) vs DELIVERED_BY_FICTION / DELIVERED_BY_LOGIN (doručená -
    legal deadlines are running).
    """

    SUBMITTED = 1  # podána (accepted by ISDS)
    TIMESTAMPED = 2  # opatřena časovým razítkem
    INFECTED = 3  # neprošla AV kontrolou, zničena
    DELIVERED_TO_BOX = 4  # dodána do schránky (NOT yet legally delivered)
    DELIVERED_BY_FICTION = 5  # doručena fikcí (§ 17 odst. 4 zák. 300/2008 Sb.)
    DELIVERED_BY_LOGIN = 6  # doručena přihlášením (event EV13 for API access)
    READ = 7  # přečtena
    UNDELIVERABLE = 8  # nedoručitelná (schránka znepřístupněna)
    DELETED = 9  # obsah smazán
    IN_VAULT = 10  # v Datovém trezoru


class DataBoxType(enum.IntEnum):
    """dbType - type of a datová schránka (subset of the ISDS enumeration)."""

    SYSTEM = 0
    OVM = 10  # orgán veřejné moci
    OVM_NOTAR = 11
    OVM_EXEKUT = 12
    OVM_REQ = 13
    OVM_FO = 14
    OVM_PFO = 15
    OVM_PO = 16
    PO = 20  # právnická osoba
    PO_ZAK = 21
    PO_REQ = 22
    PFO = 30  # podnikající fyzická osoba
    PFO_ADVOK = 31
    PFO_DANPOR = 32
    PFO_INSSPR = 33
    PFO_AUDITOR = 34
    PFO_ZNALEC = 35
    PFO_TLUMOCNIK = 36
    PFO_ARCH = 37
    PFO_AIAT = 38
    PFO_AZI = 39
    FO = 40  # fyzická osoba


class DataBox(BaseModel):
    """A datová schránka as returned by FindDataBox / ISDSSearch."""

    model_config = ConfigDict(populate_by_name=True)

    box_id: str = Field(alias="dbID")
    box_type: str | None = Field(default=None, alias="dbType")
    name: str | None = Field(default=None, alias="dbName")
    address: str | None = Field(default=None, alias="dbAddress")
    ic: str | None = Field(default=None, alias="dbIC")
    effective_ovm: bool | None = Field(default=None, alias="dbEffectiveOVM")
    send_options: str | None = Field(default=None, alias="dbSendOptions")
    accessible: bool | None = None


class OwnerInfo(BaseModel):
    """Information about the authenticated user's own datová schránka."""

    model_config = ConfigDict(populate_by_name=True)

    box_id: str = Field(alias="dbID")
    box_type: str | None = Field(default=None, alias="dbType")
    name: str | None = None
    firm_name: str | None = Field(default=None, alias="firmName")
    ic: str | None = None
    state: int | None = Field(default=None, alias="dbState")
    open_addressing: bool | None = Field(default=None, alias="dbOpenAddressing")


class DmFile(BaseModel):
    """One attachment (písemnost) of a data message."""

    file_name: str
    mime_type: str | None = None
    meta_type: str | None = None  # main|enclosure|signature|meta
    size: int | None = None
    content: bytes | None = None  # raw bytes when downloaded


class MessageEnvelope(BaseModel):
    """Envelope (obálka) of a data message - metadata without attachments."""

    model_config = ConfigDict(populate_by_name=True)

    message_id: str = Field(alias="dmID")
    sender_box_id: str | None = Field(default=None, alias="dbIDSender")
    sender_name: str | None = Field(default=None, alias="dmSender")
    sender_address: str | None = Field(default=None, alias="dmSenderAddress")
    recipient_box_id: str | None = Field(default=None, alias="dbIDRecipient")
    recipient_name: str | None = Field(default=None, alias="dmRecipient")
    recipient_address: str | None = Field(default=None, alias="dmRecipientAddress")
    subject: str | None = Field(default=None, alias="dmAnnotation")
    status: MessageStatus | None = Field(default=None, alias="dmMessageStatus")
    delivery_time: datetime | None = Field(default=None, alias="dmDeliveryTime")
    """Time the message was DODÁNA (delivered to the box, not yet legally delivered)."""
    acceptance_time: datetime | None = Field(default=None, alias="dmAcceptanceTime")
    """Time the message was DORUČENA (legally delivered - by login or by fiction)."""
    attachment_size_kb: int | None = Field(default=None, alias="dmAttachmentSize")
    personal_delivery: bool | None = Field(default=None, alias="dmPersonalDelivery")
    sender_ref_number: str | None = Field(default=None, alias="dmSenderRefNumber")
    recipient_ref_number: str | None = Field(default=None, alias="dmRecipientRefNumber")
    to_hands: str | None = Field(default=None, alias="dmToHands")
    legal_title_law: str | None = Field(default=None, alias="dmLegalTitleLaw")
    legal_title_sect: str | None = Field(default=None, alias="dmLegalTitleSect")


class DeliveryEvent(BaseModel):
    """One event from a delivery receipt (doručenka).

    Event descriptions carry EV* codes, e.g.:
      EV0  - delivery by fiction time reached (doručení fikcí)
      EV5  - delivery/acceptance (doručenka)
      EV11 - login of an authorized person (přihlášení)
      EV12 - delivery by login of a person authorized to read this message
      EV13 - delivery caused by API/spisová služba access (přístup aplikací)
    """

    time: datetime | None = Field(default=None, alias="dmEventTime")
    description: str | None = Field(default=None, alias="dmEventDescr")

    model_config = ConfigDict(populate_by_name=True)


class DeliveryInfo(BaseModel):
    """Delivery receipt (doručenka) for a sent message."""

    envelope: MessageEnvelope
    events: list[DeliveryEvent] = []
    signed_raw: bytes | None = None
    """Raw signed doručenka (CMS/PKCS#7), suitable for archiving as evidence."""
