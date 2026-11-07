"""
Local notices: failures in the unattended loop, visible without reading logs (#342).

The scheduler (cron runs ``main.py --run-due`` every 5 minutes) raises a notice when a
routine fails, doesn't run or gets stuck, when a position is through its stop or has no
protective stop, or when the check itself crashes. Notices are kept in state/notices.json,
one per condition (seeing a condition again updates its count rather than adding a notice),
shown by the scheduler CLI (``status``, ``notices``, ``ack``) and in the routine reports,
and stay open until someone acknowledges them. Nothing here touches the network.

Keys (one notice per condition per day, except ``unprotected``, which lasts until the
position has a stop again):

====================================  ========  =======================================
``routine_failed:<routine>:<date>``   CRITICAL  the routine failed after its retries
``routine_missed:<routine>:<date>``   CRITICAL  no run is recorded after its window
``routine_stuck:<routine>:<date>``    CRITICAL  a run started and never finished
``run_due_error:<date>:<Exception>``  CRITICAL  the cron check crashed
``stop_breach:<SYM>:<date>``          CRITICAL  through the stop, position still open
``target_breach:<SYM>:<date>``        WARNING   through the target, position still open
``unprotected:<SYM>``                 CRITICAL  an open position without a protective stop
``partial_exit_failed:<SYM>:<date>``  CRITICAL  a partial sale failed and the stop is not back
                                      WARNING   a partial sale failed, the stop is in place
``state_db_error:<date>``             CRITICAL  the scheduler history can't be read
``notices_file_reset:<date>``         WARNING   this file was unreadable and was reset
====================================  ========  =======================================

Every NoticeStore method is safe to call from a routine: an error is logged, never
raised (build the store with ``raise_errors=True`` to see it, as the tests do).
"""

import functools
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Container, Dict, Iterator, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from .scheduler_state import market_today
from src.trading.state.local_state_manager import write_json_atomic
from src.utils.date_utils import DEFAULT_TIMEZONE
from src.utils.state_paths import get_state_dir

try:
    import fcntl
except ImportError:  # Windows: no lock (as for the scheduler's run lock)
    fcntl = None

logger = logging.getLogger(__name__)

CRITICAL = "CRITICAL"
WARNING = "WARNING"
SEVERITIES = (CRITICAL, WARNING)

# Notice kinds: the first part of a key
ROUTINE_FAILED = "routine_failed"
ROUTINE_MISSED = "routine_missed"
ROUTINE_STUCK = "routine_stuck"
RUN_DUE_ERROR = "run_due_error"
STOP_BREACH = "stop_breach"
TARGET_BREACH = "target_breach"
UNPROTECTED = "unprotected"
PARTIAL_EXIT_FAILED = "partial_exit_failed"
STATE_DB_ERROR = "state_db_error"
NOTICES_FILE_RESET = "notices_file_reset"
CALENDAR_DISAGREES = "calendar_disagrees"
# What a completed run clears for its routine and day
ROUTINE_KINDS = (ROUTINE_FAILED, ROUTINE_MISSED, ROUTINE_STUCK)

FORMAT_VERSION = 1
ACK_RETENTION = timedelta(days=30)  # acknowledged or resolved notices are dropped after this
LOCK_WAIT_SECONDS = 10.0  # a busy lock is waited for this long, then given up
LOCK_POLL_SECONDS = 0.05
MAX_TEXT = 300  # characters of a title or detail

# What may not reach a notice (it may be shown, or sent, somewhere less private than
# the state directory): credentials, account identifiers and equity. A name ending in
# password, secret, token or key (or one of the account fields) with its value; then
# the shapes of Alpaca key ids and account numbers, UUIDs (account and order ids), OpenAI
# keys, and any other run of 32 or more letters, digits, "_" or "-".
_REDACTED = "[redacted]"
_SECRET_NAME = (
    r"[\w-]*(?:password|passwd|secret|token|key|equity|buying_power|portfolio_value|cash"
    r"|account_number|account_id)"
)
_REDACTIONS = (
    (re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+"), f"Bearer {_REDACTED}"),
    (
        re.compile(rf"(?i)\b({_SECRET_NAME})\b[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)"),
        rf"\1={_REDACTED}",
    ),
    (re.compile(r"(?<![A-Za-z0-9])(?:PK|AK|CK)[A-Z0-9]{16,}(?![A-Za-z0-9])"), _REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])PA[A-Z0-9]{8,}(?![A-Za-z0-9])"), _REDACTED),
    (re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b"), _REDACTED),
    (re.compile(r"\bsk-[\w-]{8,}"), _REDACTED),
    # An account number in free text ("account 123456789", "account no. 12345678")
    (re.compile(r"(?i)\b(account(?:\s+(?:number|no\.?|id))?)\s*#?\s*\d{6,}"), rf"\1 {_REDACTED}"),
    (re.compile(r"(?<![\w-])[A-Za-z0-9_-]{32,}(?![\w-])"), _REDACTED),
)


# What a broken notices file raises when parsed: not JSON (or not text), or not a notices
# file. An OSError (no access, an I/O error) is not one: the file may be fine, so it is never
# set aside; the error reaches the caller (logged by _contained) and the next run tries again.
_UNREADABLE = (ValueError, TypeError, KeyError, AttributeError)


def sanitize(text: Any) -> str:
    """
    Text for a notice: on one line, without credentials, account numbers, ids or equity
    (see _REDACTIONS), and at most MAX_TEXT characters.
    """
    text = " ".join(str(text).split())
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 3] + "..."


def utc_now() -> datetime:
    """The current time, timezone-aware in UTC."""
    return datetime.now(timezone.utc)


def is_notice_number(text: str) -> bool:
    """Whether text is a notice number: ASCII digits only (not "²" or full-width digits)."""
    return bool(re.fullmatch(r"[0-9]+", text.strip()))


def routine_key(kind: str, routine: str, market_date: str) -> str:
    """The key of a routine notice (ROUTINE_KINDS), e.g. routine_failed:morning:2026-10-27."""
    return f"{kind}:{routine}:{market_date}"


def _stamp(moment: datetime) -> str:
    """A time as the UTC ISO string notices store."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_stamp(stamp: str) -> datetime:
    """A stored time; one without a timezone is read as UTC."""
    moment = datetime.fromisoformat(stamp)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _shown(stamp: str) -> str:
    """A stored time for people: 2026-10-27 13:25 UTC."""
    return _parse_stamp(stamp).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@dataclass(kw_only=True)
class Notice:
    """One condition worth a human's attention."""

    seq: int  # the number `ack` takes
    key: str  # one notice per key
    kind: str
    severity: str  # CRITICAL or WARNING
    market_date: Optional[str] = None  # the trading day (ET) it is about
    symbol: Optional[str] = None
    title: str
    detail: str = ""
    report_path: Optional[str] = None
    first_seen: str = ""  # UTC ISO times
    last_seen: str = ""
    count: int = 1  # times the condition was raised
    acked_at: Optional[str] = None
    resolved_at: Optional[str] = None  # the condition cleared; the notice stays until acked

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Notice":
        """A notice from its saved form; raises if a field is missing or mistyped."""
        known = {f.name for f in fields(cls)}
        notice = cls(**{name: value for name, value in raw.items() if name in known})
        for name, kind in (("seq", int), ("count", int), ("key", str), ("title", str)):
            if not isinstance(getattr(notice, name), kind):
                raise ValueError(f"notice field {name!r} has the wrong type")
        for name in ("first_seen", "last_seen", "acked_at", "resolved_at"):
            if getattr(notice, name):
                _parse_stamp(getattr(notice, name))  # raises if it isn't a time
        return notice

    def line(self) -> str:
        """One line for lists: number, severity, title, when first seen, and if it cleared."""
        notes = [f"first seen {_shown(self.first_seen)}"]
        if self.count > 1:
            notes.append(f"seen {self.count} times")
        if self.resolved_at:
            notes.append(f"resolved {_shown(self.resolved_at)}")
        return f"#{self.seq} {self.severity} {self.title} ({', '.join(notes)})"


@dataclass
class _State:
    """The contents of the notices file."""

    notices: Dict[str, Notice] = field(default_factory=dict)
    next_seq: int = 1

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "_State":
        """The state from parsed JSON; raises if it isn't a version-1 notices file."""
        if raw["version"] != FORMAT_VERSION or not isinstance(raw["next_seq"], int):
            raise ValueError(f"not a version {FORMAT_VERSION} notices file")
        notices = {key: Notice.from_dict(notice) for key, notice in raw["notices"].items()}
        return cls(notices=notices, next_seq=raw["next_seq"])

    def as_json(self) -> Dict[str, Any]:
        return {
            "version": FORMAT_VERSION,
            "next_seq": self.next_seq,
            "notices": {key: asdict(notice) for key, notice in self.notices.items()},
        }


def _upsert(
    state: _State, now: datetime, new: Notice, update_existing: bool
) -> Tuple[Notice, bool]:
    """
    Add a notice, or fold a repeat into the one with its key: count, last seen, detail and
    report path. One that had cleared is back, so it opens again, unacknowledged.

    Returns:
        (the notice in the state, whether the state changed)
    """
    stamp = _stamp(now)
    old = state.notices.get(new.key)
    if old is None:
        new.seq, new.first_seen, new.last_seen = state.next_seq, stamp, stamp
        state.next_seq += 1
        state.notices[new.key] = new
        return new, True
    if old.resolved_at is None and not update_existing:
        return old, False
    if old.resolved_at is not None:
        old.resolved_at = old.acked_at = None
    old.count += 1
    old.last_seen = stamp
    old.detail = new.detail
    old.report_path = new.report_path or old.report_path
    return old, True


def _prune(state: _State, now: datetime) -> None:
    """
    Drop the notices acknowledged, or resolved, more than ACK_RETENTION ago and not seen
    since: a loop left unattended doesn't keep every cleared problem forever, and an
    acknowledged condition that keeps coming back stays acknowledged.
    """
    cutoff = now - ACK_RETENTION

    def old(stamp: Optional[str]) -> bool:
        return bool(stamp) and _parse_stamp(stamp) < cutoff

    stale = [
        key
        for key, notice in state.notices.items()
        if (old(notice.acked_at) or old(notice.resolved_at))
        and (not notice.last_seen or old(notice.last_seen))
    ]
    for key in stale:
        del state.notices[key]


def _contained(fallback: Callable[[], Any]) -> Callable:
    """
    Make a NoticeStore method safe for a routine to call: an error is logged and the
    fallback's value returned (unless the store was built with raise_errors).
    """

    def decorate(method: Callable) -> Callable:
        @functools.wraps(method)
        def wrapper(self: "NoticeStore", *args: Any, **kwargs: Any) -> Any:
            try:
                return method(self, *args, **kwargs)
            except Exception:
                if self.raise_errors:
                    raise
                logger.exception("Notices: %s failed", method.__name__)
                return fallback()

        return wrapper

    return decorate


class NoticeStore:
    """
    The notices in state/notices.json.

    Writers (the scheduler's cron runs and the CLI's ``ack``) take an exclusive lock on
    notices.lock beside the file, waiting up to LOCK_WAIT_SECONDS, and replace the file
    whole, so a reader never sees half a write. A file that can't be read is set aside
    as notices.json.corrupt-<time> and replaced, with a WARNING notice saying so; readers
    do this too, so the status never shows an empty list over a broken file.
    """

    sanitize = staticmethod(sanitize)  # what raise_notice applies to titles and details

    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        *,
        raise_errors: bool = False,
        now: Optional[Callable[[], datetime]] = None,
    ):
        """
        Args:
            path: The notices file (default: notices.json in the state directory)
            raise_errors: Raise errors instead of logging them (for tests)
            now: Clock returning an aware datetime (default: the current UTC time)
        """
        self.path = Path(path) if path is not None else get_state_dir() / "notices.json"
        self.raise_errors = raise_errors
        self._lock_path = self.path.with_suffix(".lock")
        self._now = now or utc_now

    # -------------------------------------------------------------------------
    # Raising and clearing
    # -------------------------------------------------------------------------

    @_contained(lambda: None)
    def raise_notice(
        self,
        key: str,
        kind: str,
        severity: str,
        title: str,
        detail: str = "",
        market_date: Optional[str] = None,
        symbol: Optional[str] = None,
        report_path: Optional[str] = None,
        *,
        update_existing: bool = True,
    ) -> Optional[Notice]:
        """
        Raise a notice, or count a repeat of the one with this key (its last seen, detail
        and report are updated; an acknowledgement is kept). A notice that had cleared
        opens again, unacknowledged: the condition is back.

        Args:
            update_existing: False leaves an open notice with this key alone, for
                conditions checked on every tick (a repeat isn't a new occurrence)

        Returns:
            The notice, or None if it couldn't be saved
        """
        if severity not in SEVERITIES:
            logger.warning("Notice %s: unknown severity %r; using %s", key, severity, WARNING)
            severity = WARNING
        new = Notice(
            seq=0,
            key=key,
            kind=kind,
            severity=severity,
            market_date=market_date,
            symbol=symbol,
            title=sanitize(title),
            detail=sanitize(detail),
            report_path=report_path,
        )

        def change(state: _State, now: datetime) -> Tuple[Tuple[Notice, bool], bool]:
            outcome = _upsert(state, now, new, update_existing)
            return outcome, outcome[1]

        notice, changed = self._update(change)
        if changed:
            seen = "raised" if notice.count == 1 else f"seen {notice.count} times"
            logger.warning("Notice #%d %s %s: %s", notice.seq, notice.severity, seen, notice.title)
        return notice

    @_contained(bool)
    def resolve(self, key: str) -> bool:
        """Mark the notice with this key resolved (not acknowledged). True if it was open."""
        return bool(self._resolve_matching(lambda notice: notice.key == key))

    @_contained(int)
    def resolve_kinds(self, routine: str, market_date: str) -> int:
        """Resolve a routine's failed, missed and stuck notices for a day. Returns how many."""
        keys = {routine_key(kind, routine, market_date) for kind in ROUTINE_KINDS}
        return self._resolve_matching(lambda notice: notice.key in keys)

    @_contained(int)
    def resolve_cleared(self, kind: str, still_present: Container[str] = ()) -> int:
        """
        Resolve the notices of a kind for symbols that are no longer a problem: those not
        in still_present. Returns how many.
        """
        return self._resolve_matching(
            lambda notice: notice.kind == kind and notice.symbol not in still_present
        )

    def _resolve_matching(self, matches: Callable[[Notice], bool]) -> int:
        """Resolve the unresolved notices that match; returns how many."""

        def change(state: _State, now: datetime) -> Tuple[int, bool]:
            hit = [n for n in state.notices.values() if n.resolved_at is None and matches(n)]
            for notice in hit:
                notice.resolved_at = _stamp(now)
            return len(hit), bool(hit)

        return self._update(change) or 0

    @_contained(int)
    def ack(self, target: Union[int, str]) -> int:
        """
        Acknowledge a notice by number, or every open one with "all".

        Returns:
            How many were acknowledged (0 for a number that is unknown or already done)
        """
        if isinstance(target, str) and is_notice_number(target):
            target = int(target)
        elif target != "all" and not isinstance(target, int):
            raise ValueError(f"cannot acknowledge {target!r}: a notice number or 'all'")

        def change(state: _State, now: datetime) -> Tuple[int, bool]:
            chosen = [
                n
                for n in state.notices.values()
                if n.acked_at is None and (target == "all" or n.seq == target)
            ]
            for notice in chosen:
                notice.acked_at = _stamp(now)
            return len(chosen), bool(chosen)

        return self._update(change) or 0

    # -------------------------------------------------------------------------
    # Reading
    # -------------------------------------------------------------------------

    # The readers are not contained: an empty list must mean "no notices", never "couldn't
    # read them". Their callers (status, notices, the report, the agent's count) catch.

    def open_notices(self) -> List[Notice]:
        """
        The notices nobody has acknowledged (cleared ones included), oldest first. Raises
        OSError if the file can't be read (a broken file is set aside and reset instead).
        """
        return [n for n in self._read().notices.values() if n.acked_at is None]

    def all_notices(self) -> List[Notice]:
        """Every saved notice, acknowledged ones too, oldest first (raises as open_notices)."""
        return list(self._read().notices.values())

    def _read(self) -> _State:
        """The saved notices, oldest first, without waiting for a writer."""
        try:
            state = self._parse()
        except _UNREADABLE:
            state = self._update(lambda current, now: (current, False))  # repairs the file
        state.notices = dict(sorted(state.notices.items(), key=lambda item: item[1].seq))
        return state

    # -------------------------------------------------------------------------
    # The file
    # -------------------------------------------------------------------------

    def _parse(self) -> _State:
        """The saved notices (none without a file). Raises one of _UNREADABLE if it is broken."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _State()
        return _State.from_json(raw)

    def _update(self, change: Callable[[_State, datetime], Tuple[Any, bool]]) -> Any:
        """
        Apply change(state, now) to the saved notices under the lock, and save them if it
        reports a change (or an unreadable file was replaced). Returns change's result.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            reset = False
            try:
                state = self._parse()
            except _UNREADABLE as error:
                state, reset = self._set_aside(error), True
            now = self._now()
            result, changed = change(state, now)
            if changed or reset:
                _prune(state, now)
                write_json_atomic(str(self.path), state.as_json())
            return result

    def _set_aside(self, error: Exception) -> _State:
        """Move the unreadable file aside, so nothing is lost, and start afresh with a notice."""
        now = self._now()
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        aside = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        os.replace(self.path, aside)
        logger.error(
            "Notices file unreadable (%s: %s); moved to %s", type(error).__name__, error, aside
        )
        today = now.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date().isoformat()
        state = _State()
        _upsert(
            state,
            now,
            Notice(
                seq=0,
                key=f"{NOTICES_FILE_RESET}:{today}",
                kind=NOTICES_FILE_RESET,
                severity=WARNING,
                market_date=today,
                title="Notices file was unreadable and was reset",
                detail=sanitize(f"Moved aside as {aside.name} ({type(error).__name__})."),
            ),
            True,
        )
        return state

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """
        Exclusive lock for a read-modify-write of the file.

        Raises:
            TimeoutError: If another process still holds it after LOCK_WAIT_SECONDS
        """
        if fcntl is None:
            yield
            return
        with open(self._lock_path, "a") as handle:
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"{self._lock_path} busy for {LOCK_WAIT_SECONDS:.0f} s"
                        ) from None
                    time.sleep(LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def record_run_due_error(error: BaseException, store: Optional[NoticeStore] = None) -> None:
    """
    Raise the run_due_error notice for a cron check that crashed (main.py --run-due),
    counting a repeat of the same error on the same day.
    """
    store = store or NoticeStore()
    today = market_today()
    name = type(error).__name__
    store.raise_notice(
        key=f"{RUN_DUE_ERROR}:{today}:{name}",
        kind=RUN_DUE_ERROR,
        severity=CRITICAL,
        title=f"Scheduler check crashed ({name})",
        detail=str(error),
        market_date=today,
    )
