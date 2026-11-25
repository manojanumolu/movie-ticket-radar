"""Conditional reads of the existing public catalogue for the dedicated worker."""
from __future__ import annotations

import json
import time

import requests

BASE = "https://raw.githubusercontent.com/manojanumolu/movie-ticket-radar/main/data/"
FILES = {"bookmyshow": "catalogue.json", "pvr_inox": "catalogue_pvr_inox.json"}
MAX_BYTES = 8 * 1024 * 1024


class CatalogueRefresh:
    def __init__(self, get=requests.get, *, clock=time.monotonic):
        self._get = get
        self._clock = clock
        self._entries = {}
        self._valid_until = {}

    def read(self, platform: str) -> dict:
        previous, etag = self._entries.get(platform, (None, None))
        if self._clock() < self._valid_until.get(platform, 0):
            return previous
        start = self._clock()
        headers = {"If-None-Match": etag} if etag else {}
        with self._get(BASE + FILES[platform], headers=headers, timeout=(2, 3),
                       stream=True, allow_redirects=False) as response:
            if response.status_code == 304 and previous is not None:
                value = previous
            elif response.status_code == 404 and platform == "pvr_inox":
                value, etag = {"movies": []}, None
            elif response.status_code == 200:
                raw = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES or self._clock() > start + 5:
                        raise ValueError("catalogue response exceeded its bounds")
                value = json.loads(raw)
                if not isinstance(value, dict) or not isinstance(value.get("movies"), list):
                    raise ValueError("invalid catalogue data")
                etag = response.headers.get("ETag")
            else:
                raise ValueError("public catalogue unavailable")
        self._entries[platform] = (value, etag)
        self._valid_until[platform] = start + 30
        return value
