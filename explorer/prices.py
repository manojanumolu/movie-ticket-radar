"""Ticket prices for the Explorer — BookMyShow's own, from the read that is on screen.

Where prices come from
----------------------
The showtimes response the Explorer already reads
(``/api/movies-data/v4/showtimes-by-event/primary-dynamic``, see
``platforms.bookmyshow``) lists every show's seat categories, each with its
price::

    additionalData.categories[] =
        {"priceDesc": "GOLD", "priceCode": "0002", "curPrice": "295.00",
         "availStatus": "3", "areaCatCode": "0000000002", ...}

``platforms.bookmyshow.parse_seat_categories`` already reads them onto every
``Showtime`` (``SeatCategory.price``). So prices cost **no extra request**:
they arrive with the showtimes, and are exactly as fresh as the read the
page's freshness line describes.

What a price here means
-----------------------
``curPrice`` is the price BookMyShow lists for one ticket in that seat
category of that show. Whether it includes GST is not stated in the payload,
and the booking fee is added only at checkout — so the page calls these the
*listed* ticket prices and says the final amount is shown at seat selection.
Nothing is converted, rounded up or estimated.

When a price is shown
---------------------
* **Every category priced** — the lowest and highest ("₹150 – ₹450"), or one
  price when they are all the same ("₹295").
* **Some categories priced, some not** — "₹150 onwards": the lowest of the
  prices BookMyShow did list. The top of the range is not known, so it is
  never shown.
* **No category priced** (none listed yet, or every price missing, zero or
  unreadable) — "Price unavailable".
* **The catalogue sync's snapshot** (the fallback when a live read fails)
  stores no prices, and a snapshot is never a source of one: a show from it
  always says "Price unavailable". Prices are never carried from one read to
  another, so a stale price cannot reappear beside a fresh showtime.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

UNAVAILABLE = "Price unavailable"
#: What the page says about every price it shows.
NOTE = ("Ticket prices are BookMyShow's listed price per seat category at the time of the read. "
        "The final amount, with any booking fee, is shown on BookMyShow at seat selection.")


@dataclass(frozen=True)
class SeatPrice:
    """One seat category of one show, as BookMyShow listed it."""

    name: str                 # "GOLD"
    amount: float | None      # 295.0; None when BookMyShow listed no usable price
    sold_out: bool = False


def parse_amount(value: object) -> float | None:
    """A listed price ("295.00", 295) as a number, or None when it is not a
    real price: missing, unreadable, not finite, zero or negative."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


def rupees(amount: float) -> str:
    """₹295 for a whole amount, ₹295.50 otherwise — never rounded."""
    whole = round(amount)
    text = f"{whole:,}" if abs(amount - whole) < 0.005 else f"{amount:,.2f}"
    return f"₹{text}"


@dataclass(frozen=True)
class PriceRange:
    """What is known about the prices of one show, or of many."""

    low: float | None = None
    high: float | None = None
    #: True when every seat category involved had a usable price — only then
    #: is ``high`` the real top of the range.
    complete: bool = False
    categories: int = 0       # seat categories BookMyShow listed
    priced: int = 0           # of them, with a usable price

    @property
    def known(self) -> bool:
        return self.low is not None

    @property
    def label(self) -> str:
        if self.low is None:
            return UNAVAILABLE
        if not self.complete or self.high is None:
            return f"{rupees(self.low)} onwards"
        if abs(self.high - self.low) < 0.005:
            return rupees(self.low)
        return f"{rupees(self.low)} – {rupees(self.high)}"

    @property
    def kind(self) -> str:
        """"range", "single", "from" or "none" — for styling and tests."""
        if self.low is None:
            return "none"
        if not self.complete:
            return "from"
        return "single" if abs((self.high or self.low) - self.low) < 0.005 else "range"


def of_show(seats: Iterable[SeatPrice]) -> PriceRange:
    seats = tuple(seats)
    amounts = [s.amount for s in seats if s.amount is not None]
    if not amounts:
        return PriceRange(categories=len(seats))
    return PriceRange(low=min(amounts), high=max(amounts), complete=len(amounts) == len(seats),
                      categories=len(seats), priced=len(amounts))


def summarise(ranges: Iterable[PriceRange]) -> tuple[PriceRange, int, int]:
    """Across many shows: ``(range, shows priced, shows)``. The range is
    complete only when every show's own range is."""
    ranges = list(ranges)
    known = [r for r in ranges if r.known]
    if not known:
        return PriceRange(), 0, len(ranges)
    complete = len(known) == len(ranges) and all(r.complete for r in known)
    return (PriceRange(low=min(r.low for r in known), high=max(r.high for r in known if r.high is not None),
                       complete=complete, categories=sum(r.categories for r in ranges),
                       priced=sum(r.priced for r in ranges)),
            len(known), len(ranges))


def detail(seats: Iterable[SeatPrice]) -> str:
    """Each category in BookMyShow's own words, for a tooltip:
    "EXECUTIVE ₹150 · GOLD ₹295 · RECLINER ₹450 (sold out)"."""
    parts = []
    for s in seats:
        price = rupees(s.amount) if s.amount is not None else "no price listed"
        parts.append(f"{s.name or 'Seat'} {price}" + (" (sold out)" if s.sold_out else ""))
    return " · ".join(parts)


__all__ = ["NOTE", "PriceRange", "SeatPrice", "UNAVAILABLE", "detail", "of_show", "parse_amount", "rupees",
           "summarise"]
