"""The Showtime Explorer's hero rotation: recent Indian cinema only.

Separate from Home's collection. Every line is the original words in Roman
letters (no regional scripts, no translations, never a Hindi-dub line given
as a regional film's original), and ``material`` says where it comes from:
the released film, an official trailer, teaser, glimpse or promo.

Left out because the exact spoken line could not be verified: Jailer 2
(2026) — official trailers exist, but no publication quotes Tiger Muthuvel
Pandian verbatim; Pathaan (2023) — outlets disagree on the wording; Pushpa
2 (2024) — the widely quoted line is the Hindi dub's; Kantara: Chapter 1
(2025) — published lines are Hindi-dub versions; and no recent
Kannada-language line could be verified.
"""

from __future__ import annotations

from content import Dialogue

DIALOGUES: tuple[Dialogue, ...] = (
    Dialogue("Ee samudram chepala kante katthulni, netthurine ekkuva choosuntaadi. "
             "Andhuke deenni Erra Samudram antaaru.",
             "Devara: Part 1", 2024, "Devara (Jr NTR)", "Telugu",
             "m9.news (Telugu text of the glimpse, romanised); DT Next; ThePrint", material="glimpse"),
    Dialogue("Bete ko haath lagane se pehle, baap se baat kar.", "Jawan", 2023,
             "Shah Rukh Khan", "Hindi",
             "The Week; ThePrint; Free Press Journal (dialogue writer Sumit Arora)", material="trailer"),
    Dialogue("Aarambikalaama?", "Vikram", 2022, "Vikram (Kamal Haasan)", "Tamil",
             "The News Minute; Film Companion", material="teaser"),
    Dialogue("Eda mone!", "Aavesham", 2024, "Ranga (Fahadh Faasil)", "Malayalam",
             "Onmanorama; ETV Bharat; Filmibeat 2024 recap"),
    Dialogue("Bloody sweet.", "Leo", 2023, "Leo (Vijay)", "Tamil film · said in English",
             "DT Next; Film Companion", material="promo"),
)

__all__ = ["DIALOGUES"]
