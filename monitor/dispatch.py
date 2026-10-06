"""Who may start a GitHub workflow from the app — decided on the server.

The app holds a token that can dispatch workflows. Hiding a button is not
authorisation, so every dispatch the app makes goes through here, and
``config.store.dispatch_workflow`` refuses any that did not (outside
GitHub Actions):

* **A check of every monitor** (Settings → "Run a ticket check now") and a
  **catalogue refresh** — the administrator only: the ``admin`` custom
  claim from Firebase's account record, held in the session's
  server-side scope. Nothing on a page can supply it.
* **A check of one monitor** — its owner, for one of their own *running*
  monitors (starting one, Retry, Extend), at most once a minute per
  monitor for an ordinary account. The worker then checks only that
  monitor early; everything else keeps its own interval.

Whose monitor it is comes from the store, which only ever reads the
signed-in person's own documents — never from the id alone.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from config import store
from monitor import state

#: Seconds between two on-demand checks of the same monitor by an ordinary
#: account. Enough to stop a Retry button being hammered; short enough that
#: nobody waiting on a real problem notices.
MEMBER_COOLDOWN = 60.0

CATALOGUE_WORKFLOW = "catalogue-sync.yml"


class DispatchNotAllowed(PermissionError):
    """The signed-in person may not start this."""


_lock = threading.Lock()
_last: dict[tuple[str, str], float] = {}
_clock: Callable[[], float] = time.monotonic


def _scope() -> state.Scope:
    scope = state._signed_in_scope()
    if scope is None:
        raise DispatchNotAllowed("Sign in first.")
    return scope


def _cooldown(uid: str, monitor_id: str) -> None:
    with _lock:
        now = _clock()
        last = _last.get((uid, monitor_id))
        if last is not None and now - last < MEMBER_COOLDOWN:
            wait = int(MEMBER_COOLDOWN - (now - last)) + 1
            raise DispatchNotAllowed(f"A check was just requested. Try again in {wait} seconds.")
        _last[(uid, monitor_id)] = now


def check_now(monitor_id: str = "", *, force: bool = True) -> tuple[bool, str]:
    """Ask the worker to check now. Raises :class:`DispatchNotAllowed`."""
    scope = _scope()
    if not monitor_id:
        if not scope.admin:
            raise DispatchNotAllowed("Only the admin account can run a check of every monitor.")
    else:
        owned = state.get_monitor(monitor_id)   # the store reads this person's own only
        if owned is None or not owned.is_running():
            raise DispatchNotAllowed("That isn't one of your running monitors.")
        if not scope.admin:
            _cooldown(scope.uid, monitor_id)
    with store.dispatch_grant():
        return store.request_check_now(monitor_id, force=force)


def refresh_catalogue(city: str) -> tuple[bool, str]:
    """Re-sync the movie catalogue. The administrator only."""
    scope = _scope()
    if not scope.admin:
        raise DispatchNotAllowed("Only the admin account can refresh the catalogue.")
    with store.dispatch_grant():
        return store.dispatch_workflow(CATALOGUE_WORKFLOW, {"city": city})


__all__ = ["CATALOGUE_WORKFLOW", "DispatchNotAllowed", "MEMBER_COOLDOWN", "check_now", "refresh_catalogue"]
