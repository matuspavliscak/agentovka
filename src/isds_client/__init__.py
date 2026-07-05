"""isds_client — a standalone Python client for the Czech ISDS (datové schránky) SOAP API.

This package is usable without the MCP layer:

    from isds_client import IsdsClient, IsdsEnvironment

    client = IsdsClient(username="...", password="...", environment=IsdsEnvironment.TEST)
    info = client.get_owner_info()

Legal note: listing or downloading *received* messages counts as a login-based
delivery event in ISDS (event EV13) and legally delivers all messages that are
in the "delivered to box" (dodaná) state. See docs/delivery-semantics.md.
"""

from isds_client.client import IsdsClient, IsdsEnvironment
from isds_client.errors import (
    IsdsAuthError,
    IsdsError,
    IsdsResponseError,
)
from isds_client.models import (
    DataBox,
    DataBoxType,
    DeliveryEvent,
    DeliveryInfo,
    DmFile,
    MessageEnvelope,
    MessageStatus,
    OwnerInfo,
)

__all__ = [
    "DataBox",
    "DataBoxType",
    "DeliveryEvent",
    "DeliveryInfo",
    "DmFile",
    "IsdsAuthError",
    "IsdsClient",
    "IsdsEnvironment",
    "IsdsError",
    "IsdsResponseError",
    "MessageEnvelope",
    "MessageStatus",
    "OwnerInfo",
]
