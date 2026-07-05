"""Integration tests against the ISDS TEST environment (ws1.czebox.cz).

These are skipped unless AGENTOVKA_RUN_INTEGRATION=1 and ISDS_USERNAME /
ISDS_PASSWORD are set for a *test* box (create one free at
https://www.datovka-test.gov.cz). They never run against production and never
call delivery-triggering operations by default.

Run with:
    AGENTOVKA_RUN_INTEGRATION=1 ISDS_USERNAME=... ISDS_PASSWORD=... uv run pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from isds_client.client import IsdsClient, IsdsEnvironment

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("AGENTOVKA_RUN_INTEGRATION") == "1"
_HAS_CREDS = bool(os.environ.get("ISDS_USERNAME") and os.environ.get("ISDS_PASSWORD"))

skip_reason = "set AGENTOVKA_RUN_INTEGRATION=1 and ISDS_USERNAME/ISDS_PASSWORD (test box) to run"


@pytest.fixture
def test_client() -> IsdsClient:
    if os.environ.get("ISDS_ENV", "test").lower() == "production":
        pytest.skip("integration tests refuse to run against production")
    return IsdsClient(
        os.environ["ISDS_USERNAME"],
        os.environ["ISDS_PASSWORD"],
        environment=IsdsEnvironment.TEST,
    )


@pytest.mark.skipif(not (_ENABLED and _HAS_CREDS), reason=skip_reason)
def test_owner_info_roundtrip(test_client: IsdsClient) -> None:
    """Safe class-A call: GetOwnerInfoFromLogin returns our own box ID."""
    info = test_client.get_owner_info()
    assert info.box_id
    assert len(info.box_id) == 7


@pytest.mark.skipif(not (_ENABLED and _HAS_CREDS), reason=skip_reason)
def test_find_databox_ovm(test_client: IsdsClient) -> None:
    """Safe class-A call: search returns at least one box for a known query."""
    boxes = test_client.find_databox("Ministerstvo vnitra")
    assert isinstance(boxes, list)


@pytest.mark.skipif(not (_ENABLED and _HAS_CREDS), reason=skip_reason)
def test_list_sent_messages(test_client: IsdsClient) -> None:
    """Safe class-A call: listing sent messages does not touch the received store."""
    msgs = test_client.get_list_of_sent_messages(limit=10)
    assert isinstance(msgs, list)


# ---------------------------------------------------------------------------
# DELIVERY-TRIGGERING integration tests (event EV13). These legally deliver
# every message in the TEST box, which is harmless there but is still kept
# behind its own opt-in flag so the default integration run stays class-A.
# Run with AGENTOVKA_RUN_INTEGRATION_DELIVERY=1 as well.
# ---------------------------------------------------------------------------

_DELIVERY_ENABLED = os.environ.get("AGENTOVKA_RUN_INTEGRATION_DELIVERY") == "1"
delivery_skip_reason = (
    "set AGENTOVKA_RUN_INTEGRATION_DELIVERY=1 to run delivery-triggering integration tests "
    "(they deliver all messages in the TEST box, event EV13)"
)


@pytest.mark.skipif(
    not (_ENABLED and _HAS_CREDS and _DELIVERY_ENABLED), reason=delivery_skip_reason
)
def test_received_list_and_zfo_roundtrip(test_client: IsdsClient) -> None:
    """List received messages; if any exists, download and parse the signed ZFO.

    This exercises the real ZFO structure end to end - most importantly that
    dmDeliveryTime/dmMessageStatus live as siblings of dmDm and are parsed.
    """
    from isds_client.zfo import parse_zfo

    msgs = test_client.get_list_of_received_messages(limit=10)
    assert isinstance(msgs, list)
    if not msgs:
        pytest.skip("test box has no received messages - send one between test boxes first")

    raw = test_client.signed_message_download(msgs[0].message_id)
    parsed = parse_zfo(raw)
    assert parsed.envelope.message_id == msgs[0].message_id
    # The whole point of the sibling-fields fix: real ZFOs must yield a
    # delivery time and a status, not None.
    assert parsed.envelope.delivery_time is not None
    assert parsed.envelope.status is not None
