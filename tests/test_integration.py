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
