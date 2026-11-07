"""Local notices (#342): the store behind them. No network, and no real state."""

import fcntl
import json
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest

from src.trading.scheduling import notices
from src.trading.scheduling.notices import (
    CRITICAL,
    WARNING,
    NoticeStore,
    record_run_due_error,
    routine_key,
    sanitize,
)
from src.utils.state_paths import get_state_dir

FAILED = "routine_failed:morning:2026-10-27"


class Clock:
    """A clock for the store that only moves when the test says so."""

    def __init__(self):
        self.now = datetime(2026, 10, 27, 13, 25, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return NoticeStore(tmp_path / "notices.json", raise_errors=True, now=clock)


def put(store, key=FAILED, title="Morning routine failed", severity=CRITICAL, **fields):
    return store.raise_notice(key, key.split(":")[0], severity, title, **fields)


# --- Raising, updating, acknowledging ---------------------------------------------------


def test_the_file_is_in_the_state_directory_with_its_lock_beside_it():
    store = NoticeStore()  # the default location; the unit tests point the state dir at a temp dir

    put(store)

    assert store.path == get_state_dir() / "notices.json"
    assert (get_state_dir() / "notices.lock").exists()
    saved = json.loads(store.path.read_text())
    assert (saved["version"], saved["next_seq"]) == (1, 2)
    assert set(saved["notices"][FAILED]) == {
        "seq",
        "key",
        "kind",
        "severity",
        "market_date",
        "symbol",
        "title",
        "detail",
        "report_path",
        "first_seen",
        "last_seen",
        "count",
        "acked_at",
        "resolved_at",
        "emailed_at",
        "email_attempts",
        "email_last_try",
    }


def test_a_raised_notice_is_saved_with_what_it_was_given(store):
    notice = store.raise_notice(
        "stop_breach:AAPL:2026-10-27",
        "stop_breach",
        CRITICAL,
        "AAPL is at or below its stop, still open",
        detail="price $170.00",
        market_date="2026-10-27",
        symbol="AAPL",
        report_path="reports/daily/2026-10-27_morning.md",
    )

    assert (notice.seq, notice.count, notice.severity) == (1, 1, "CRITICAL")
    assert notice.first_seen == notice.last_seen == "2026-10-27T13:25:00+00:00"
    assert (notice.acked_at, notice.resolved_at) == (None, None)
    [saved] = store.open_notices()
    assert saved == notice
    assert (saved.symbol, saved.market_date, saved.report_path) == (
        "AAPL",
        "2026-10-27",
        "reports/daily/2026-10-27_morning.md",
    )


def test_a_repeat_updates_the_notice_instead_of_adding_one(store, clock):
    put(store, detail="first")
    clock.advance(minutes=5)

    again = put(store, detail="second")

    assert (again.seq, again.count, again.detail) == (1, 2, "second")
    assert again.first_seen == "2026-10-27T13:25:00+00:00"
    assert again.last_seen == "2026-10-27T13:30:00+00:00"
    assert len(store.all_notices()) == 1


def test_ack_takes_one_notice_or_all_of_them(store):
    for key in ("a:1", "b:2", "c:3"):
        put(store, key)

    assert store.ack(2) == 1
    assert [n.seq for n in store.open_notices()] == [1, 3]
    assert (store.ack(2), store.ack(99)) == (0, 0)  # already done, and unknown
    assert store.ack("all") == 2
    assert store.open_notices() == []
    assert [n.seq for n in store.all_notices()] == [1, 2, 3]  # acknowledged, not deleted


def test_ack_takes_a_number_as_text_too(store):
    put(store)

    assert store.ack("1") == 1


def test_a_notice_acknowledged_while_its_condition_lasts_stays_acknowledged(store):
    put(store)
    store.ack(1)

    again = put(store)

    assert again.count == 2 and again.acked_at is not None
    assert store.open_notices() == []


def test_update_existing_false_leaves_an_open_notice_alone(store, clock):
    first = put(store, detail="first")
    clock.advance(minutes=5)

    same = put(store, detail="later", update_existing=False)

    assert (same.count, same.last_seen, same.detail) == (1, first.last_seen, "first")
    assert store.all_notices() == [first]


# --- Resolving --------------------------------------------------------------------------


def test_a_resolved_notice_is_not_acknowledged_and_returns_if_the_condition_does(store, clock):
    put(store)
    clock.advance(minutes=10)

    assert store.resolve(FAILED) is True
    assert store.resolve(FAILED) is False  # already resolved
    [resolved] = store.open_notices()  # still there for a person to see and acknowledge
    assert resolved.resolved_at == "2026-10-27T13:35:00+00:00" and resolved.acked_at is None

    clock.advance(minutes=5)
    back = put(store)

    assert back.resolved_at is None and back.count == 2
    assert back.last_seen == "2026-10-27T13:40:00+00:00"


def test_a_notice_acknowledged_and_resolved_opens_again_when_the_condition_returns(store):
    """Otherwise a position left unprotected twice would be shown the first time only."""
    put(store, "unprotected:SPY", symbol="SPY")
    store.ack(1)
    store.resolve("unprotected:SPY")

    put(store, "unprotected:SPY", symbol="SPY")

    [again] = store.open_notices()
    assert (again.acked_at, again.resolved_at, again.count) == (None, None, 2)


def test_update_existing_false_still_reopens_a_resolved_notice(store):
    put(store)
    store.resolve(FAILED)

    back = put(store, update_existing=False)

    assert back.resolved_at is None and back.count == 2


def test_resolve_kinds_clears_a_routines_notices_for_that_day_only(store):
    cleared = [routine_key(kind, "morning", "2026-10-27") for kind in notices.ROUTINE_KINDS]
    kept = [
        "routine_failed:evening:2026-10-27",
        "routine_failed:morning:2026-10-26",
        "stop_breach:AAPL:2026-10-27",
    ]
    for key in cleared + kept:
        put(store, key)

    assert store.resolve_kinds("morning", "2026-10-27") == 3
    assert {n.key for n in store.all_notices() if n.resolved_at} == set(cleared)
    assert store.resolve_kinds("morning", "2026-10-27") == 0


def test_resolve_cleared_keeps_the_symbols_still_in_trouble(store):
    for symbol in ("AAPL", "MSFT", "SPY"):
        put(store, f"unprotected:{symbol}", symbol=symbol)
    put(store, "stop_breach:AAPL:2026-10-27", symbol="AAPL")

    assert store.resolve_cleared("unprotected", {"MSFT"}) == 2
    assert {n.key for n in store.all_notices() if not n.resolved_at} == {
        "unprotected:MSFT",
        "stop_breach:AAPL:2026-10-27",  # another kind
    }


# --- Pruning ----------------------------------------------------------------------------


def test_acknowledged_notices_are_dropped_after_30_days_on_the_next_write(store, clock):
    for key in ("old:1", "never_acked:2", "recent:3"):
        put(store, key)
    store.ack(1)  # day 0
    clock.advance(days=29)
    store.ack(3)  # day 29
    clock.advance(days=2)  # day 31: "old" was acknowledged 31 days ago, "recent" 2

    assert {n.key for n in store.all_notices()} == {"old:1", "never_acked:2", "recent:3"}  # reads
    fresh = put(store, "fresh:4")  # a write prunes

    assert {n.key for n in store.all_notices()} == {"never_acked:2", "recent:3", "fresh:4"}
    assert fresh.seq == 4  # numbers are not reused


def test_resolved_notices_are_dropped_30_days_after_they_resolved_even_if_never_acked(store, clock):
    for key in ("routine_failed:morning:2026-10-27", "never_resolved:2"):
        put(store, key)
    store.resolve("routine_failed:morning:2026-10-27")  # day 0
    clock.advance(days=29)
    put(store, "fresh:3")
    assert len(store.all_notices()) == 3

    clock.advance(days=2)  # day 31
    put(store, "fresh:4")

    assert {n.key for n in store.all_notices()} == {"never_resolved:2", "fresh:3", "fresh:4"}


def test_an_acknowledged_condition_that_keeps_coming_back_is_kept_past_30_days(store, clock):
    put(store, "unprotected:SPY")
    store.ack(1)  # day 0
    for _ in range(4):
        clock.advance(days=10)
        put(store, "unprotected:SPY")  # still unprotected on days 10, 20, 30 and 40

    [notice] = store.all_notices()
    assert (notice.seq, notice.count) == (1, 5) and notice.acked_at is not None


# --- A broken file ----------------------------------------------------------------------

BROKEN_FILES = [
    "{not json",
    "",
    "[]",
    "null",
    '{"version": 2, "next_seq": 1, "notices": {}}',
    '{"version": 1, "next_seq": "one", "notices": {}}',
    '{"version": 1, "next_seq": 1, "notices": []}',
    '{"version": 1, "next_seq": 1, "notices": {"k": {"seq": "one"}}}',
]


@pytest.mark.parametrize("content", BROKEN_FILES)
def test_an_unreadable_file_is_moved_aside_and_replaced_with_a_warning(store, content):
    store.path.write_text(content)

    put(store, "k:1")

    [aside] = store.path.parent.glob("notices.json.corrupt-*")
    assert aside.name == "notices.json.corrupt-20261027T132500Z"
    assert aside.read_text() == content  # nothing is lost
    by_kind = {n.kind: n for n in store.all_notices()}
    reset = by_kind["notices_file_reset"]
    assert (reset.key, reset.severity) == ("notices_file_reset:2026-10-27", WARNING)
    assert aside.name in reset.detail
    assert "k" in by_kind


def test_reading_a_broken_file_shows_the_reset_not_an_empty_list(store):
    store.path.write_text("{not json")

    assert [n.kind for n in store.open_notices()] == ["notices_file_reset"]
    assert [n.kind for n in store.open_notices()] == ["notices_file_reset"]  # repaired once
    assert len(list(store.path.parent.glob("notices.json.corrupt-*"))) == 1


# --- Writing ----------------------------------------------------------------------------


def test_an_existing_files_mode_is_kept_and_no_temp_file_is_left(store):
    put(store)
    store.path.chmod(0o640)

    put(store)

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o640
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["notices.json", "notices.lock"]


def test_a_busy_lock_is_given_up_on_after_the_wait(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(notices, "LOCK_WAIT_SECONDS", 0.2)
    path = tmp_path / "notices.json"
    contained, strict = NoticeStore(path), NoticeStore(path, raise_errors=True)

    with open(tmp_path / "notices.lock", "a") as other_process:
        fcntl.flock(other_process, fcntl.LOCK_EX)
        assert put(contained) is None
        with pytest.raises(TimeoutError, match="busy"):
            put(strict)
        fcntl.flock(other_process, fcntl.LOCK_UN)

    assert not path.exists()
    assert "Notices: raise_notice failed" in caplog.text
    assert put(contained) is not None  # and works again once the lock is free


def test_concurrent_writers_lose_no_notice_and_share_no_number(tmp_path):
    path = tmp_path / "notices.json"
    errors = []

    def write(worker):
        try:
            writer = NoticeStore(path, raise_errors=True)
            for i in range(5):
                put(writer, f"worker{worker}:{i}")
        except Exception as error:  # reported below: a thread's error would otherwise vanish
            errors.append(error)

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    saved = NoticeStore(path).all_notices()
    assert len(saved) == 30
    assert [n.seq for n in saved] == list(range(1, 31))


def test_every_method_logs_its_errors_instead_of_raising(tmp_path, caplog):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the state directory should be")
    path = blocker / "notices.json"
    quiet = NoticeStore(path)

    assert quiet.raise_notice(FAILED, "routine_failed", CRITICAL, "t") is None
    assert quiet.resolve(FAILED) is False
    assert quiet.resolve_kinds("morning", "2026-10-27") == 0
    assert quiet.resolve_cleared("unprotected") == 0
    assert quiet.ack("all") == 0
    assert "Notices: raise_notice failed" in caplog.text
    with pytest.raises(OSError):  # a reader never answers "none" for a file it can't read
        quiet.open_notices()
    with pytest.raises(OSError):
        NoticeStore(path, raise_errors=True).raise_notice(FAILED, "routine_failed", CRITICAL, "t")


def test_a_file_that_cannot_be_read_is_left_where_it_is(store, monkeypatch, caplog):
    """No access or an I/O error is not a broken file: nothing is set aside or reset."""
    put(store)
    saved = store.path.read_text()
    real_read = type(store.path).read_text

    def no_access(path, *args, **kwargs):
        if path == store.path:
            raise PermissionError(13, "Permission denied")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(type(store.path), "read_text", no_access)
    quiet = NoticeStore(store.path)

    assert quiet.raise_notice("stop_breach:SPY:2026-10-27", "stop_breach", CRITICAL, "t") is None
    with pytest.raises(PermissionError):
        quiet.open_notices()
    monkeypatch.undo()
    assert store.path.read_text() == saved
    assert not list(store.path.parent.glob("notices.json.corrupt-*"))
    assert "Notices: raise_notice failed" in caplog.text


@pytest.mark.parametrize("text", ["²", "１", "1²"])
def test_ack_refuses_digits_that_are_not_ascii(store, text):
    put(store)

    with pytest.raises(ValueError, match="notice number or 'all'"):
        store.ack(text)


def test_ack_refuses_what_is_neither_a_number_nor_all(store):
    put(store)

    with pytest.raises(ValueError, match="notice number or 'all'"):
        store.ack("everything")
    assert NoticeStore(store.path).ack("everything") == 0  # contained


def test_an_unknown_severity_is_kept_as_a_warning(store):
    assert put(store, severity="URGENT").severity == WARNING


# --- What a notice may say --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, leaked",
    [
        ("rejected for key PKABCDEFGHIJKLMNOP12", "PKABCDEFGHIJKLMNOP12"),  # paper key id
        ("live key AKABCDEFGHIJKLMNOP12 used", "AKABCDEFGHIJKLMNOP12"),
        ("account PA3ABCDEFGHI is restricted", "PA3ABCDEFGHI"),
        (
            "order 3f2a9c1e-8b7d-4e6f-9a10-1234567890ab not found",
            "3f2a9c1e-8b7d-4e6f-9a10-1234567890ab",
        ),
        ("openai said sk-proj-abcdefgh12345678 is invalid", "sk-proj-abcdefgh12345678"),
        (
            "got abcdefghijklmnopqrstuvwxyz0123456789ABCDEF back",
            "abcdefghijklmnopqrstuvwxyz0123456789",
        ),
        ("APCA_API_SECRET_KEY=hunter2xyz failed", "hunter2xyz"),
        ("password: hunter2 rejected", "hunter2"),
        ("token = 'abc 123' expired", "abc 123"),
        ('{"api_key": "abc123", "status": 403}', "abc123"),
        ("equity: 123456.78 below the minimum", "123456.78"),
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
        ("account_number: 123456789 is restricted", "123456789"),
        ("account 123456789 is restricted", "123456789"),
        ("Account no. 98765432 not found", "98765432"),
    ],
)
def test_sanitize_removes_credentials_account_numbers_and_equity(raw, leaked):
    clean = sanitize(raw)

    assert leaked not in clean
    assert "[redacted]" in clean


def test_sanitize_keeps_ordinary_error_text():
    text = "Attempt 4 failed: Morning routine failed: protective stops not secured (AAPL, MSFT)"

    assert sanitize(text) == text
    assert sanitize("KeyError: 'AAPL'") == "KeyError: 'AAPL'"  # not a "key: value" secret
    assert sanitize("order PARTIALLY_FILLED at /state/notices.json") == (
        "order PARTIALLY_FILLED at /state/notices.json"
    )


def test_sanitize_puts_text_on_one_line_and_caps_it_at_300_characters():
    clean = sanitize("line one\nline two\n" + "word " * 200)

    assert "\n" not in clean and clean.startswith("line one line two word")
    assert len(clean) == 300 and clean.endswith("...")
    short = " ".join(["ab"] * 100)  # 299 characters
    assert sanitize(short) == short


def test_a_store_offers_the_same_sanitizer():
    assert NoticeStore.sanitize("api_key=abc123 failed") == sanitize("api_key=abc123 failed")


def test_a_notice_is_sanitized_when_it_is_raised(store):
    notice = put(
        store,
        title="Failed for PA3ABCDEFGHI",
        detail="api_key=abc123 order 3f2a9c1e-8b7d-4e6f-9a10-1234567890ab",
    )

    assert "PA3ABCDEFGHI" not in notice.title
    assert "abc123" not in notice.detail and "3f2a9c1e" not in notice.detail
    assert "abc123" not in store.path.read_text()


# --- Lines and the cron crash notice ----------------------------------------------------


def test_a_notice_line_names_its_number_severity_title_and_first_seen(store, clock):
    notice = put(store)
    assert notice.line() == "#1 CRITICAL Morning routine failed (first seen 2026-10-27 13:25 UTC)"

    clock.advance(minutes=5)
    notice = put(store)
    store.resolve(FAILED)
    [notice] = store.all_notices()
    assert notice.line() == (
        "#1 CRITICAL Morning routine failed "
        "(first seen 2026-10-27 13:25 UTC, seen 2 times, resolved 2026-10-27 13:30 UTC)"
    )


def test_a_crashed_cron_check_is_one_notice_per_error_type_per_day(store, monkeypatch):
    monkeypatch.setattr(notices, "market_today", lambda: "2026-10-27")

    record_run_due_error(RuntimeError("boom api_key=abc123"), store)
    record_run_due_error(RuntimeError("boom again"), store)
    record_run_due_error(ValueError("something else"), store)

    runtime, value = store.all_notices()
    assert (runtime.key, runtime.severity, runtime.count) == (
        "run_due_error:2026-10-27:RuntimeError",
        "CRITICAL",
        2,
    )
    assert runtime.title == "Scheduler check crashed (RuntimeError)"
    assert runtime.detail == "boom again"
    assert value.key == "run_due_error:2026-10-27:ValueError"
    assert "abc123" not in store.path.read_text()
