"""
Email for CRITICAL notices and a dead-man heartbeat (#342).

Settings come from config/config.json (see ConfigLoader):

    "notifications": {
        "email": {"smtp_host", "smtp_port", "use_ssl", "username", "password",
                  "from_addr", "to_addrs"},
        "heartbeat_url": "https://..."
    }

Email is on only when every email key is set; otherwise nothing is sent and nothing is
logged. ``use_ssl`` true connects with SMTP_SSL (usually port 465), false with SMTP and
STARTTLS (usually 587). The heartbeat is a GET to the URL after each healthy cron tick, for
a service (healthchecks.io and the like) that alerts when the pings stop.

Nothing here logs or prints a password, user name, host or URL: a ping URL carries a secret.

A digest sends the open CRITICAL notices not yet emailed in ONE message. Each notice keeps
its claim (``emailed_at``, ``email_attempts``, ``email_last_try``, see NoticeStore), so a
second call in the same tick sends nothing, a failed send is retried after RETRY_AFTER and
given up after MAX_ATTEMPTS, and at most DAILY_CAP emails go out per market day (counted
in state/email_log.json). WARNING notices are never emailed.
"""

import json
import logging
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

import requests

from .notices import Notice, NoticeStore, sanitize
from .scheduler_state import market_today
from src.trading.state.local_state_manager import write_json_atomic
from src.utils.config_loader import ConfigLoader
from src.utils.http_timeouts import HEARTBEAT_TIMEOUT
from src.utils.state_paths import get_state_dir

logger = logging.getLogger(__name__)

EMAIL_KEYS = (
    "smtp_host",
    "smtp_port",
    "use_ssl",
    "username",
    "password",
    "from_addr",
    "to_addrs",
)
SMTP_TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(minutes=15) - timedelta(seconds=30)  # slack: ticks are 5 min apart
DAILY_CAP = 10
EMAIL_LOG_FILE = "email_log.json"


def _setting(key: str) -> Any:
    """A value from config.json by dotted key; None if unset or the config can't be read."""
    try:
        return ConfigLoader().get(key)
    except Exception:
        return None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list)) and not value)


def email_settings() -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    The email settings and the names of the keys that are missing.

    Returns:
        (settings, []) when every key is set, else (None, the missing key names)
    """
    section = _setting("notifications.email")
    section = section if isinstance(section, dict) else {}
    missing = [key for key in EMAIL_KEYS if _blank(section.get(key))]
    if missing:
        return None, missing
    settings = dict(section)
    recipients = settings["to_addrs"]
    settings["to_addrs"] = [recipients] if isinstance(recipients, str) else list(recipients)
    return settings, []


def heartbeat_url() -> Optional[str]:
    """The heartbeat URL, or None if not set."""
    url = _setting("notifications.heartbeat_url")
    return url if isinstance(url, str) and url.strip() else None


# -----------------------------------------------------------------------------
# Sending
# -----------------------------------------------------------------------------


def _send_message(settings: Dict[str, Any], subject: str, body: str) -> None:
    """Send one message through the configured server. Raises on any failure."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["from_addr"]
    message["To"] = ", ".join(settings["to_addrs"])
    message.set_content(body)
    host, port = settings["smtp_host"], int(settings["smtp_port"])
    use_ssl = settings["use_ssl"] is True or str(settings["use_ssl"]).lower() == "true"
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS)
    else:
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
    with server:
        if not use_ssl:
            server.starttls()
        server.login(settings["username"], settings["password"])
        server.send_message(message)


def _digest_text(notices: List[Notice]) -> Tuple[str, str]:
    """The subject and body for these notices."""
    subject = f"OptionsCommander: {len(notices)} CRITICAL notice(s)"
    lines = []
    for notice in notices:
        lines.append(f"#{notice.seq} {sanitize(notice.title)}")
        if notice.detail:
            lines.append(f"  {sanitize(notice.detail)}")
        if notice.market_date:
            lines.append(f"  Market date: {sanitize(notice.market_date)}")
        if notice.report_path:
            lines.append(f"  Report: {sanitize(notice.report_path)}")
        lines.append("")
    lines.append("Acknowledge in the scheduler CLI: 'ack <number|all>'.")
    return subject, "\n".join(lines)


def send_test_email() -> Tuple[bool, str]:
    """
    Send one test email. Touches neither the daily cap nor the notices.

    Returns:
        (sent, a message that names no setting's value)
    """
    settings, missing = email_settings()
    if settings is None:
        return False, "Email is off (missing: " + ", ".join(missing) + ")"
    try:
        _send_message(
            settings,
            "OptionsCommander: test email",
            "This is a test from the scheduler CLI ('notify test').",
        )
    except Exception as error:
        return False, f"Test email failed ({type(error).__name__})"
    return True, f"Test email sent to {len(settings['to_addrs'])} recipient(s)"


# -----------------------------------------------------------------------------
# The daily cap
# -----------------------------------------------------------------------------


def _log_path() -> str:
    return str(get_state_dir() / EMAIL_LOG_FILE)


def _emails_today() -> int:
    """Emails sent today (market date); 0 if the log is missing or unreadable."""
    try:
        with open(_log_path(), encoding="utf-8") as handle:
            raw = json.load(handle)
        return int(raw["count"]) if raw["date"] == market_today() else 0
    except (OSError, ValueError, TypeError, KeyError):
        return 0


def _count_email() -> None:
    write_json_atomic(_log_path(), {"date": market_today(), "count": _emails_today() + 1})


# -----------------------------------------------------------------------------
# The digest and the heartbeat
# -----------------------------------------------------------------------------


def send_notice_digest(store: NoticeStore) -> int:
    """
    Email the open CRITICAL notices not yet sent, in one message. Safe to call twice in a
    tick. Never raises an Exception (the caller's routine is not worth failing for this).

    Returns:
        How many notices were emailed
    """
    try:
        settings, _ = email_settings()
        if settings is None:
            return 0
        if not store.email_pending(MAX_ATTEMPTS, RETRY_AFTER):
            return 0  # nothing to send: no lock
        room = DAILY_CAP - _emails_today()
        if room <= 0:
            logger.warning("Notice email: daily cap of %d reached; not sending", DAILY_CAP)
            return 0
        claimed = store.claim_for_email(MAX_ATTEMPTS, RETRY_AFTER)
        if not claimed:
            return 0
        subject, body = _digest_text(claimed)
        try:
            _send_message(settings, subject, body)
        except Exception as error:
            logger.warning(
                "Notice email failed (%s); %d notice(s) stay claimed for a retry",
                type(error).__name__,
                len(claimed),
            )
            return 0
        store.mark_emailed({n.key for n in claimed})
        _count_email()
        logger.info("Notice email sent for %d notice(s)", len(claimed))
        return len(claimed)
    except Exception as error:
        logger.warning("Notice email skipped (%s)", type(error).__name__)
        return 0


def _get(url: str) -> None:
    """The heartbeat request (a seam for tests)."""
    requests.get(url, timeout=HEARTBEAT_TIMEOUT).raise_for_status()


def ping_heartbeat() -> bool:
    """
    GET the heartbeat URL, if one is set. A failure is logged (by class name) and never
    raised.

    Returns:
        Whether a ping went out and was answered
    """
    url = heartbeat_url()
    if url is None:
        return False
    try:
        _get(url)
    except Exception as error:
        logger.warning("Heartbeat ping failed (%s)", type(error).__name__)
        return False
    return True
