"""
Default request timeouts. The alpaca-py clients send every request without a timeout, so
a broker that accepts a connection and never answers would hang the process forever (and,
under cron, hold state/scheduler.lock with it).
"""

import logging
from typing import Any, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 30
DEFAULT_TIMEOUT: Tuple[float, float] = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
# The dead-man heartbeat ping runs on every cron tick, so it gives up quickly
HEARTBEAT_TIMEOUT: Tuple[float, float] = (5, 5)


def set_default_timeout(
    session: requests.Session, timeout: Optional[Tuple[float, float]] = None
) -> requests.Session:
    """
    Make a session send its requests with a timeout (connect, read) unless the call passes
    its own, `timeout=None` included. Returns the session.
    """
    timeout = timeout or DEFAULT_TIMEOUT
    original = session.request

    def request_with_timeout(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(method, url, **kwargs)

    session.request = request_with_timeout  # type: ignore[method-assign]
    return session


def with_default_timeout(client: Any, timeout: Optional[Tuple[float, float]] = None) -> Any:
    """
    Give an alpaca-py client (its `_session`) the default timeout. Returns the client.
    A client without a requests session is left alone, with a warning, so an SDK change
    can't stop a routine from starting.
    """
    session = getattr(client, "_session", None)
    if isinstance(session, requests.Session):
        set_default_timeout(session, timeout)
    else:
        logger.warning("%s has no requests session; no default timeout set", type(client).__name__)
    return client
