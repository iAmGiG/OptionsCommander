"""
Email for CRITICAL notices and the dead-man heartbeat (#342). Fakes only: no SMTP
connection and no HTTP request is ever made (tests/conftest.py makes any real use raise).
"""

import asyncio
import json
import logging
import smtplib
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import main

from src.cli.scheduler.monitor import SchedulerMonitor
from src.cli.scheduler_cli import SchedulerCLI
from src.trading.scheduling import daily_scheduler, notifier
from src.trading.scheduling.notices import CRITICAL, WARNING, NoticeStore
from src.utils.http_timeouts import HEARTBEAT_TIMEOUT
from src.utils.state_paths import get_state_dir

HOST, USER, PASSWORD = "smtp.secret-host.test", "secret-user", "s3cr3t-pass"
PING = "https://ping.test/abc-secret-path"
CONFIG = {
    "notifications.email": {
        "smtp_host": HOST,
        "smtp_port": 465,
        "use_ssl": True,
        "username": USER,
        "password": PASSWORD,
        "from_addr": "from@example.com",
        "to_addrs": ["a@example.com", "b@example.com"],
    },
    "notifications.heartbeat_url": PING,
}
REAL_GET = notifier._get  # before the autouse fixture replaces it
KEY = "routine_failed:morning:2026-10-27"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 13, 25, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


class Mail:
    """What the fake SMTP classes saw."""

    def __init__(self):
        self.sent = []  # (class name, host, port, timeout, calls, message)
        self.fail = None  # an exception to raise on send

    def factory(self, name):
        mail = self

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                self.record = [name, host, port, timeout, []]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self):
                self.record[4].append("starttls")

            def login(self, user, password):
                self.record[4].append("login")

            def send_message(self, message):
                if mail.fail:
                    raise mail.fail
                self.record[4].append("send")
                mail.sent.append((*self.record, message))

        return FakeSMTP


@pytest.fixture
def config(monkeypatch):
    values = json.loads(json.dumps(CONFIG))
    monkeypatch.setattr(notifier, "_setting", lambda key: values.get(key))
    return values


@pytest.fixture
def mail(monkeypatch, config):
    fake = Mail()
    monkeypatch.setattr(smtplib, "SMTP", fake.factory("SMTP"))
    monkeypatch.setattr(smtplib, "SMTP_SSL", fake.factory("SMTP_SSL"))
    return fake


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return NoticeStore(tmp_path / "notices.json", raise_errors=True, now=clock)


@pytest.fixture
def today(monkeypatch):
    day = ["2026-10-27"]
    monkeypatch.setattr(notifier, "market_today", lambda: day[0])
    return day


def put(store, key=KEY, severity=CRITICAL, **fields):
    return store.raise_notice(
        key, key.split(":")[0], severity, fields.pop("title", "Morning routine failed"), **fields
    )


def one(store, key=KEY):
    return next(n for n in store.all_notices() if n.key == key)


# --- Settings ---------------------------------------------------------------------------


def test_incomplete_settings_send_nothing_and_take_no_lock(store, config, mail, monkeypatch):
    put(store)
    del config["notifications.email"]["password"]
    monkeypatch.setattr(store, "_locked", Mock(side_effect=AssertionError("locked")))

    assert notifier.send_notice_digest(store) == 0

    assert mail.sent == []
    assert notifier.email_settings()[1] == ["password"]


def test_no_settings_at_all_is_off_and_silent(store, caplog):
    put(store)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert notifier.send_notice_digest(store) == 0
        assert notifier.ping_heartbeat() is False
    assert caplog.records == []


def test_a_single_recipient_string_is_a_list(config):
    config["notifications.email"]["to_addrs"] = "one@example.com"
    settings, missing = notifier.email_settings()
    assert missing == [] and settings["to_addrs"] == ["one@example.com"]


def test_use_ssl_false_is_set_not_missing(config):
    config["notifications.email"]["use_ssl"] = False
    assert notifier.email_settings()[1] == []


# --- The digest -------------------------------------------------------------------------


def test_one_digest_for_a_new_critical_notice_and_nothing_the_next_tick(store, mail, today):
    put(store, detail="boom", market_date="2026-10-27", report_path="reports/daily/x.md")

    assert notifier.send_notice_digest(store) == 1
    assert notifier.send_notice_digest(store) == 0

    [(kind, host, port, timeout, calls, message)] = mail.sent
    assert (kind, host, port, timeout) == ("SMTP_SSL", HOST, 465, 10)
    assert calls == ["login", "send"]
    assert message["Subject"] == "OptionsCommander: 1 CRITICAL notice(s)"
    assert message["To"] == "a@example.com, b@example.com"
    body = message.get_content()
    for text in ("Morning routine failed", "boom", "2026-10-27", "reports/daily/x.md"):
        assert text in body
    assert one(store).emailed_at and one(store).email_attempts == 1


def test_several_notices_go_in_one_email(store, mail, today):
    put(store)
    put(store, key="unprotected:AAPL", title="AAPL has no stop")

    assert notifier.send_notice_digest(store) == 2

    [sent] = mail.sent
    assert sent[-1]["Subject"] == "OptionsCommander: 2 CRITICAL notice(s)"


def test_warning_and_acknowledged_and_resolved_notices_never_email(store, mail, today):
    put(store, key="target_breach:AAPL:2026-10-27", severity=WARNING)
    put(store, key="a:1")
    put(store, key="b:1")
    store.ack(2)
    store.resolve("b:1")

    assert notifier.send_notice_digest(store) == 0
    assert mail.sent == []


def test_starttls_when_use_ssl_is_false(store, mail, config, today):
    config["notifications.email"].update(use_ssl=False, smtp_port=587)
    put(store)

    notifier.send_notice_digest(store)

    [(kind, _, port, timeout, calls, _)] = mail.sent
    assert (kind, port, timeout) == ("SMTP", 587, 10)
    assert calls == ["starttls", "login", "send"]


def test_a_failed_send_retries_after_fifteen_minutes_and_stops_after_three(
    store, mail, clock, today
):
    put(store)
    mail.fail = OSError("down")

    assert notifier.send_notice_digest(store) == 0  # attempt 1
    clock.advance(minutes=5)
    assert notifier.send_notice_digest(store) == 0  # too soon: no attempt
    assert one(store).email_attempts == 1
    clock.advance(minutes=10)  # 15 minutes since the first try
    notifier.send_notice_digest(store)  # attempt 2
    clock.advance(minutes=15)
    notifier.send_notice_digest(store)  # attempt 3
    clock.advance(hours=1)
    notifier.send_notice_digest(store)  # given up

    assert one(store).email_attempts == 3 and one(store).emailed_at is None
    mail.fail = None
    clock.advance(hours=1)
    assert notifier.send_notice_digest(store) == 0
    assert mail.sent == []


def test_a_failed_send_that_works_later_is_marked_sent(store, mail, clock, today):
    put(store)
    mail.fail = OSError("down")
    notifier.send_notice_digest(store)
    mail.fail = None
    clock.advance(minutes=15)

    assert notifier.send_notice_digest(store) == 1
    assert one(store).emailed_at and one(store).email_attempts == 2


def test_the_daily_cap_holds_and_resets_the_next_market_day(store, mail, today):
    for n in range(12):
        put(store, key=f"k{n}:x")
        assert notifier.send_notice_digest(store) == (1 if n < 10 else 0)

    assert len(mail.sent) == 10
    assert json.loads((get_state_dir() / "email_log.json").read_text()) == {
        "date": "2026-10-27",
        "count": 10,
    }
    today[0] = "2026-10-28"
    assert notifier.send_notice_digest(store) == 2  # the two held back go out together
    assert len(mail.sent) == 11


def test_a_reopened_notice_emails_again_and_a_repeat_does_not(store, mail, today):
    put(store)
    notifier.send_notice_digest(store)
    put(store)  # a repeat of an open notice
    assert notifier.send_notice_digest(store) == 0

    store.resolve(KEY)
    put(store)  # back
    assert one(store).emailed_at is None and one(store).email_attempts == 0
    assert notifier.send_notice_digest(store) == 1
    assert len(mail.sent) == 2


def test_no_secret_reaches_the_logs(store, mail, config, today, caplog, monkeypatch):
    put(store)
    mail.fail = smtplib.SMTPAuthenticationError(535, f"bad login {USER} at {HOST}".encode())
    caplog.clear()
    monkeypatch.setattr(notifier, "_get", Mock(side_effect=OSError(f"{PING} {PASSWORD}")))

    with caplog.at_level(logging.DEBUG):
        notifier.send_notice_digest(store)
        notifier.ping_heartbeat()

    for secret in (PASSWORD, USER, HOST, PING):
        assert secret not in caplog.text
    assert "SMTPAuthenticationError" in caplog.text and "OSError" in caplog.text


def test_the_body_carries_no_credentials_from_a_sent_email(store, mail, today):
    put(store, detail=f"password={PASSWORD}")
    notifier.send_notice_digest(store)
    [sent] = mail.sent
    body = sent[-1].get_content()
    assert PASSWORD not in body and USER not in body and HOST not in body


def test_a_base_exception_from_the_sender_propagates(store, mail, today, monkeypatch):
    class Stop(BaseException):
        pass

    put(store)
    monkeypatch.setattr(notifier, "_send_message", Mock(side_effect=Stop()))
    with pytest.raises(Stop):
        notifier.send_notice_digest(store)


def test_an_unexpected_error_is_contained(store, mail, today, monkeypatch):
    put(store)
    monkeypatch.setattr(notifier, "_emails_today", Mock(side_effect=RuntimeError("x")))
    assert notifier.send_notice_digest(store) == 0


# --- The scheduler hook -----------------------------------------------------------------


def test_run_scheduled_tasks_sends_the_digest_after_the_outcome_check(monkeypatch):
    order = []
    scheduler = daily_scheduler.DailyScheduler.__new__(daily_scheduler.DailyScheduler)
    scheduler.tasks = []
    scheduler._hours_memo = {}
    scheduler._notice_store = "store"
    scheduler.run_lock = lambda: nullcontext(True)
    scheduler.check_routine_outcomes = lambda: order.append("outcomes")
    monkeypatch.setattr(daily_scheduler, "send_notice_digest", lambda s: order.append(s))

    scheduler.run_scheduled_tasks()

    assert order == ["outcomes", "store"]


# --- Heartbeat --------------------------------------------------------------------------


def test_the_ping_is_one_get_with_the_timeout(config, monkeypatch):
    get = Mock()
    monkeypatch.setattr(notifier, "_get", REAL_GET)
    monkeypatch.setattr(notifier.requests, "get", get)

    assert notifier.ping_heartbeat() is True

    get.assert_called_once_with(PING, timeout=HEARTBEAT_TIMEOUT)


def test_no_url_means_no_ping():
    assert notifier.ping_heartbeat() is False  # the default fixture would raise if it tried


def test_a_failed_ping_is_logged_not_raised(config, monkeypatch, caplog):
    monkeypatch.setattr(notifier, "_get", Mock(side_effect=OSError(PING)))
    with caplog.at_level(logging.WARNING):
        assert notifier.ping_heartbeat() is False
    assert "OSError" in caplog.text and PING not in caplog.text


def test_a_base_exception_from_the_ping_propagates(config, monkeypatch):
    class Stop(BaseException):
        pass

    monkeypatch.setattr(notifier, "_get", Mock(side_effect=Stop()))
    with pytest.raises(Stop):
        notifier.ping_heartbeat()


# --- main.py --run-due ------------------------------------------------------------------


@pytest.fixture
def tick(monkeypatch):
    """_run_due_mode with the checks replaced; returns the pings and digests it made."""
    seen = Mock()
    monkeypatch.setattr(main, "configure_scheduler_logging", lambda: None)
    monkeypatch.setattr(main, "load_scheduler_config", lambda: {})
    monkeypatch.setattr(main, "ping_heartbeat", seen.ping)
    monkeypatch.setattr(main, "send_notice_digest", seen.digest)
    monkeypatch.setattr(main, "_note_run_due_error", seen.note)
    return seen


def test_a_healthy_tick_pings_once(tick, monkeypatch):
    monkeypatch.setattr(main, "_run_due_checks", lambda limit: 0)
    assert main._run_due_mode() == 0
    tick.ping.assert_called_once_with()


def test_an_error_tick_never_pings_and_emails_after_the_notice(tick, monkeypatch):
    def crash(limit):
        raise ValueError("bad")

    monkeypatch.setattr(main, "_run_due_checks", crash)
    with pytest.raises(ValueError):
        main._run_due_mode()
    tick.ping.assert_not_called()
    assert [c[0] for c in tick.mock_calls if c[0] in ("note", "digest")] == ["note", "digest"]


def test_a_tick_with_a_failed_routine_does_not_ping(tick, monkeypatch):
    monkeypatch.setattr(main, "_run_due_checks", lambda limit: 1)
    assert main._run_due_mode() == 1
    tick.ping.assert_not_called()


def test_a_timeout_tick_emails_and_does_not_ping(tick, monkeypatch):
    from src.trading.scheduling.watchdog import RunDueTimeoutError

    def hang(limit):
        raise RunDueTimeoutError(1)

    monkeypatch.setattr(main, "_run_due_checks", hang)
    assert main._run_due_mode() == 1
    tick.ping.assert_not_called()
    tick.digest.assert_called_once()


# --- The daemon -------------------------------------------------------------------------


def test_the_daemon_pings_at_most_every_five_minutes(monkeypatch):
    scheduler = daily_scheduler.DailyScheduler.__new__(daily_scheduler.DailyScheduler)
    scheduler.config = {"market_timezone": "America/New_York"}
    scheduler.tasks = []
    scheduler.effective_settings = lambda: {"enabled": True, "source": "test"}
    now = iter([0, 60, 301, 302])
    runs = []
    scheduler.run_scheduled_tasks = lambda: runs.append(next(now))
    pings = []
    monkeypatch.setattr(daily_scheduler, "ping_heartbeat", lambda: pings.append(runs[-1]))
    monkeypatch.setattr(daily_scheduler, "time", Mock(monotonic=lambda: runs[-1]))

    async def sleep(_):
        if len(runs) == 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(daily_scheduler.asyncio, "sleep", sleep)
    asyncio.run(scheduler.run_daemon(check_interval_seconds=60))

    assert runs == [0, 60, 301, 302] and pings == [0, 301]


# --- The scheduler CLI ------------------------------------------------------------------


def _status(capsys):
    scheduler = Mock(config={})
    scheduler.effective_settings.return_value = {"enabled": True}
    scheduler.get_execution_history.return_value = []
    scheduler.notice_store.open_notices.return_value = []
    monitor = SchedulerMonitor(scheduler)
    monitor.daemon_manager = Mock(is_running=Mock(return_value=False))
    monitor.daemon_manager.cron_entry.return_value = "*/5 * * * * main.py --run-due"
    monitor.show_status()
    return capsys.readouterr().out


def test_status_lines_name_missing_keys_only(config, capsys):
    del config["notifications.email"]["smtp_host"]
    config["notifications.heartbeat_url"] = None

    out = _status(capsys)

    assert "Email: off (missing: smtp_host)" in out
    assert "Heartbeat: not set" in out
    assert HOST not in out


def test_status_lines_when_configured(config, capsys):
    out = _status(capsys)

    assert "Email: configured (2 recipients)" in out and "Heartbeat: set" in out
    assert PING not in out and PASSWORD not in out


def _cli(command):
    cli = SchedulerCLI()
    asyncio.run(cli._handle_command(command))


def test_notify_test_sends_one_email_without_touching_cap_or_notices(mail, capsys):
    _cli("notify test")

    assert "Test email sent to 2 recipient(s)" in capsys.readouterr().out
    [sent] = mail.sent
    assert sent[-1]["Subject"] == "OptionsCommander: test email"
    assert not (get_state_dir() / "email_log.json").exists()
    assert not (get_state_dir() / "notices.json").exists()


def test_notify_test_failure_names_the_exception_class_only(mail, capsys):
    mail.fail = smtplib.SMTPAuthenticationError(535, f"{USER}@{HOST}".encode())
    _cli("notify test")
    out = capsys.readouterr().out
    assert "failed (SMTPAuthenticationError)" in out
    assert USER not in out and HOST not in out


def test_notify_test_says_what_is_missing(config, capsys):
    del config["notifications.email"]["username"]
    del config["notifications.email"]["to_addrs"]
    _cli("notify test")
    assert "Email is off (missing: username, to_addrs)" in capsys.readouterr().out


def test_notify_without_test_prints_the_usage(capsys):
    _cli("notify")
    assert "Usage: notify test" in capsys.readouterr().out
