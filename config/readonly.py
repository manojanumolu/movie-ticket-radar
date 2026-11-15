"""A process-wide "nothing may be written" switch, for the worker's dry run.

A dry run evaluates every running monitor exactly as a real check would and
reports what it would have emailed — and must leave nothing behind: no alert
marked as sent, no observed state, no history line, no monitor flipped to
EXPIRED, no discovery clock. ``monitor.checker.run_once(dry_run=True)`` simply
does not call any of those writes. This is the net under that: the two places
anything is ever persisted — a Firestore commit (``config.firestore``) and a
data-file write (``config.store.write_json``) — refuse while it is on, so a
write path added later, or one that was missed, fails loudly instead of
quietly changing production data.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_READ_ONLY: ContextVar[bool] = ContextVar("tr_read_only", default=False)


class ReadOnlyViolation(RuntimeError):
    """Something tried to persist data while :func:`read_only` was on."""


@contextmanager
def read_only():
    token = _READ_ONLY.set(True)
    try:
        yield
    finally:
        _READ_ONLY.reset(token)


def active() -> bool:
    return _READ_ONLY.get()


def refuse(what: str) -> None:
    """Raise when a write is attempted under :func:`read_only`."""
    if _READ_ONLY.get():
        print(f"[dry-run] refused a write: {what}", flush=True)
        raise ReadOnlyViolation(f"dry run: refused to write {what}")


__all__ = ["ReadOnlyViolation", "active", "read_only", "refuse"]
