"""Showtime Explorer — browse what BookMyShow lists, by movie or by theatre.

A browsing feature, kept apart from monitoring on purpose:

* ``explorer.classify``  — operator, theatre type and locality, read only
  from the text BookMyShow itself supplies for a theatre;
* ``explorer.query``     — pure filtering, sorting, grouping and the
  Asia/Kolkata time window;
* ``explorer.source``    — where the showtimes come from: a bounded, cached
  live read of BookMyShow's showtimes-by-event endpoint, and the snapshot the
  catalogue sync already reads (``explorer.snapshot``).

Nothing in this package writes a monitor, a monitor's state, history or
settings, and nothing in ``monitor`` imports it.
"""
