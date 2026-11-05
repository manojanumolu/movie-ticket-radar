"""Editorial content shown by the UI, kept apart from the UI code.

* ``home_dialogues`` — famous cinema lines from around the world, for the
  Home hero's rotating line.
* ``explorer_dialogues`` — recent Indian cinema lines, for the Showtime
  Explorer hero's rotating line.
* ``format_directory`` — the 24 cinema formats the Explorer's craft icons
  explain.

Every dialogue is the original-language line written in Roman letters, with
no translation, and carries its film, year, speaker, language, source and
verification status. Nothing here is read by monitoring, the wizard or the
BookMyShow provider.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dialogue:
    line: str          # the words as spoken, in Roman letters — never a translation
    film: str
    year: int
    speaker: str       # the character, with the star where it is known
    language: str      # the language the line is spoken in
    source: str        # the publication(s) the wording and credit were checked against
    status: str = "verified"
    material: str = "film"   # film · trailer · teaser · glimpse · promo

    @property
    def credit(self) -> str:
        """``Film · Year · Speaker · Language`` — the credit every rotation shows."""
        return f"{self.film} · {self.year} · {self.speaker} · {self.language}"


__all__ = ["Dialogue"]
