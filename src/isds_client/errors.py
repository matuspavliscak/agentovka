"""Error types raised by isds_client."""


class IsdsError(Exception):
    """Base class for all ISDS client errors."""


class IsdsAuthError(IsdsError):
    """Authentication against ISDS failed (wrong credentials, locked account, ...)."""


class IsdsResponseError(IsdsError):
    """ISDS returned a non-OK application status code (dbStatus/dmStatus)."""

    def __init__(self, status_code: str, status_message: str) -> None:
        self.status_code = status_code
        self.status_message = status_message
        super().__init__(f"ISDS error {status_code}: {status_message}")
