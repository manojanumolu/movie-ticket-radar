"""One collection of film dialogue, audited, for Home and the Explorer.

Every entry is a short excerpt — never more than ten words — with the film,
its year, the language it is spoken in and who says it. Each was checked
against a named reference (Wikipedia's article on the film, the film's IMDb
quotes page, or contemporary press coverage); ``source`` records which. AFI's
lists are not used as a ranking of anything: they are an American list of
American films.

``kind`` says exactly what is shown, so nothing is passed off as more than
it is:

* ``"quote"`` — the line as spoken. Lines in a script other than Latin are
  romanised (Telugu, Hindi, Tamil, Malayalam, Bengali, Japanese, Korean,
  Mandarin), with an English translation beside them.
* ``"subtitle"`` — the line's published English subtitle, shown because the
  original-language wording could not be confirmed letter for letter from a
  reliable source. The language named is still the language spoken.
* ``"paraphrase"`` — our own words, inspired by the film, and labelled as
  such wherever it is shown. Never presented as a quotation.

What the audit changed from the earlier lines: the RRR, Salaar and
Magadheera lines could not be confirmed as spoken (the Magadheera one was
also misspelt) and are gone; "I love you 3000" is from *Avengers: Endgame*,
not *Iron Man*; Thanos's line was cut mid-sentence and is now whole; "Hulk
smash!" is said *to* the Hulk in *The Avengers*, not by him, and is gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

#: The longest excerpt shown, in words.
MAX_WORDS = 10
KINDS = ("quote", "subtitle", "paraphrase")


@dataclass(frozen=True)
class Dialogue:
    key: str
    line: str          # as shown: the original (romanised) or, for "subtitle", the English subtitle
    english: str       # an English translation; "" when the line is already English
    film: str
    year: int
    language: str      # the language the line is spoken in
    speaker: str       # the character, then the actor
    region: str        # "India" or "World"
    kind: str = "quote"
    source: str = ""   # where the wording and attribution were checked

    @property
    def film_year(self) -> str:
        return f"{self.film} ({self.year})"

    @property
    def credit(self) -> str:
        """"Film (Year) · Language" — the attribution every surface shows."""
        return f"{self.film_year} · {self.language}"

    @property
    def note(self) -> str:
        """The label a non-verbatim entry carries wherever it is shown."""
        return {"subtitle": "English subtitle", "paraphrase": "Paraphrase, inspired by the film"}.get(self.kind, "")

    @property
    def spoken(self) -> str:
        """The whole entry as one sentence, for an accessible label."""
        parts = [f"“{self.line}”"]
        if self.english:
            parts.append(f"({self.english})")
        parts.append(f"— {self.speaker}, {self.credit}")
        if self.note:
            parts.append(f"[{self.note}]")
        return " ".join(parts)


WIKI = "Wikipedia"
IMDB = "IMDb quotes"

DIALOGUES: tuple[Dialogue, ...] = (
    # ── India ───────────────────────────────────────────────────────────
    Dialogue("pushpa", "Pushpa… Pushpa Raj… Thaggedhe le!", "Pushpa… Pushpa Raj… I won't back down!",
             "Pushpa: The Rise", 2021, "Telugu", "Pushpa Raj (Allu Arjun)", "India", source=WIKI),
    Dialogue("pokiri", "Okkasari commit ayithe, naa maata nene vinanu.",
             "Once I commit, I don't listen even to myself.",
             "Pokiri", 2006, "Telugu", "Pandu (Mahesh Babu)", "India", source="Telugu press"),
    Dialogue("gabbar_singh", "Naaku konchem tikkundi… kaani daaniko lekkundi.",
             "I'm a little mad… but there's a method to it.",
             "Gabbar Singh", 2012, "Telugu", "Gabbar Singh (Pawan Kalyan)", "India", source="Telugu press"),
    Dialogue("baahubali", "This is my word! My word is my law!", "",
             "Baahubali 2: The Conclusion", 2017, "Telugu", "Sivagami (Ramya Krishnan)", "India",
             kind="subtitle", source=IMDB),
    Dialogue("sholay", "Kitne aadmi the?", "How many men were there?",
             "Sholay", 1975, "Hindi", "Gabbar Singh (Amjad Khan)", "India", source=WIKI),
    Dialogue("deewaar", "Mere paas maa hai.", "I have Mother.",
             "Deewaar", 1975, "Hindi", "Ravi (Shashi Kapoor)", "India", source=WIKI),
    Dialogue("mr_india", "Mogambo khush hua.", "Mogambo is pleased.",
             "Mr. India", 1987, "Hindi", "Mogambo (Amrish Puri)", "India", source=WIKI),
    Dialogue("ddlj", "Bade bade deshon mein aisi chhoti chhoti baatein…",
             "In big countries, such little things…",
             "Dilwale Dulhania Le Jayenge", 1995, "Hindi", "Raj (Shah Rukh Khan)", "India", source=WIKI),
    Dialogue("padayappa", "En vazhi, thani vazhi.", "My way is a way of its own.",
             "Padayappa", 1999, "Tamil", "Padayappa (Rajinikanth)", "India", source=WIKI),
    Dialogue("baashha", "Naan oru thadava sonna, nooru thadava sonna maadhiri.",
             "If I say it once, it's as good as a hundred times.",
             "Baashha", 1995, "Tamil", "Manikkam (Rajinikanth)", "India", source=WIKI),
    Dialogue("narasimham", "Nee po mone Dinesha.", "Off you go, Dinesha, my boy.",
             "Narasimham", 2000, "Malayalam", "Induchoodan (Mohanlal)", "India", source=WIKI),
    Dialogue("kgf2", "Violence, violence, violence. I don't like it. I avoid.", "",
             "K.G.F: Chapter 2", 2022, "Kannada · said in English", "Rocky (Yash)", "India", source=IMDB),
    Dialogue("meghe_dhaka_tara", "Dada, ami banchte chai.", "Brother, I want to live.",
             "Meghe Dhaka Tara", 1960, "Bengali", "Neeta (Supriya Choudhury)", "India", source=WIKI),
    Dialogue("hirak_rajar_deshe", "A king's rule falls when everyone pulls together.", "",
             "Hirak Rajar Deshe", 1980, "Bengali", "Udayan Pandit (Soumitra Chatterjee)", "India",
             kind="paraphrase", source=WIKI),
    # ── The world ───────────────────────────────────────────────────────
    Dialogue("casablanca", "Here's looking at you, kid.", "",
             "Casablanca", 1942, "English", "Rick Blaine (Humphrey Bogart)", "World", source=WIKI),
    Dialogue("wizard_of_oz", "There's no place like home.", "",
             "The Wizard of Oz", 1939, "English", "Dorothy (Judy Garland)", "World", source=WIKI),
    Dialogue("star_wars", "May the Force be with you.", "",
             "Star Wars", 1977, "English", "Han Solo (Harrison Ford)", "World", source=IMDB),
    Dialogue("terminator", "I'll be back.", "",
             "The Terminator", 1984, "English", "The Terminator (Arnold Schwarzenegger)", "World", source=WIKI),
    Dialogue("jaws", "You're gonna need a bigger boat.", "",
             "Jaws", 1975, "English", "Martin Brody (Roy Scheider)", "World", source=WIKI),
    Dialogue("apollo_13", "Houston, we have a problem.", "",
             "Apollo 13", 1995, "English", "Jim Lovell (Tom Hanks)", "World", source=WIKI),
    Dialogue("dark_knight", "Why so serious?", "",
             "The Dark Knight", 2008, "English", "The Joker (Heath Ledger)", "World", source=IMDB),
    Dialogue("batman", "I'm Batman.", "",
             "Batman", 1989, "English", "Batman (Michael Keaton)", "World", source=IMDB),
    Dialogue("toy_story", "To infinity and beyond!", "",
             "Toy Story", 1995, "English", "Buzz Lightyear (Tim Allen)", "World", source=WIKI),
    Dialogue("iron_man", "I am Iron Man.", "",
             "Iron Man", 2008, "English", "Tony Stark (Robert Downey Jr.)", "World", source=IMDB),
    Dialogue("endgame", "I love you 3000.", "",
             "Avengers: Endgame", 2019, "English", "Tony Stark (Robert Downey Jr.)", "World", source=IMDB),
    Dialogue("infinity_war", "Dread it. Run from it. Destiny arrives all the same.", "",
             "Avengers: Infinity War", 2018, "English", "Thanos (Josh Brolin)", "World", source=IMDB),
    Dialogue("black_panther", "Wakanda forever!", "",
             "Black Panther", 2018, "English", "T'Challa (Chadwick Boseman)", "World", source=IMDB),
    Dialogue("spider_man", "With great power comes great responsibility.", "",
             "Spider-Man", 2002, "English", "Ben Parker (Cliff Robertson)", "World", source=IMDB),
    Dialogue("dr_no", "Bond. James Bond.", "",
             "Dr. No", 1962, "English", "James Bond (Sean Connery)", "World", source=WIKI),
    Dialogue("la_haine", "Jusqu'ici tout va bien.", "So far, so good.",
             "La Haine", 1995, "French", "Hubert (Hubert Koundé)", "World", source=WIKI),
    Dialogue("breathless", "Qu'est-ce que c'est, « dégueulasse » ?", "What does “disgusting” mean?",
             "À bout de souffle", 1960, "French", "Patricia (Jean Seberg)", "World", source=WIKI),
    Dialogue("life_is_beautiful", "Buongiorno, principessa!", "Good morning, princess!",
             "La vita è bella", 1997, "Italian", "Guido (Roberto Benigni)", "World", source=WIKI),
    Dialogue("secret_in_their_eyes", "…no puede cambiar de pasión.", "…he can't change his passion.",
             "El secreto de sus ojos", 2009, "Spanish", "Pablo Sandoval (Guillermo Francella)", "World",
             source="Argentine press"),
    Dialogue("run_lola_run", "The ball is round. The game lasts ninety minutes.", "",
             "Lola rennt", 1998, "German", "Schuster (Armin Rohde)", "World", kind="subtitle", source=IMDB),
    Dialogue("seven_samurai", "Katta no wa ano hyakushō-tachi da.", "The winners are those farmers.",
             "Seven Samurai", 1954, "Japanese", "Kambei Shimada (Takashi Shimura)", "World", source=WIKI),
    Dialogue("castle_in_the_sky", "Miro! Hito ga gomi no yō da!", "Look! The people are like rubbish!",
             "Castle in the Sky", 1986, "Japanese", "Muska (Minori Terada)", "World", source=WIKI),
    Dialogue("memories_of_murder", "Bab-eun meokgo daninya?", "Are you eating properly?",
             "Memories of Murder", 2003, "Korean", "Park Doo-man (Song Kang-ho)", "World", source="Korean press"),
    Dialogue("parasite", "You know what kind of plan never fails? No plan.", "",
             "Parasite", 2019, "Korean", "Kim Ki-taek (Song Kang-ho)", "World", kind="subtitle", source=IMDB),
    Dialogue("farewell_my_concubine", "…yí ge shíchen, dōu bú suàn yíbèizi!",
             "…even an hour short is not a lifetime!",
             "Farewell My Concubine", 1993, "Mandarin", "Cheng Dieyi (Leslie Cheung)", "World",
             source="Chinese press"),
    Dialogue("infernal_affairs", "Sorry, I'm a cop.", "",
             "Infernal Affairs", 2002, "Cantonese", "Chan Wing-yan (Tony Leung)", "World",
             kind="subtitle", source=IMDB),
)

BY_KEY: dict[str, Dialogue] = {d.key: d for d in DIALOGUES}
assert len(BY_KEY) == len(DIALOGUES), "every entry has its own key"

#: The rotation under Home's hero: India and the world, alternating, so the
#: languages change at every turn.
HOME_ROTATION: tuple[str, ...] = (
    "pushpa", "casablanca", "sholay", "la_haine", "padayappa", "seven_samurai",
    "narasimham", "life_is_beautiful", "meghe_dhaka_tara", "memories_of_murder",
    "kgf2", "secret_in_their_eyes", "pokiri", "farewell_my_concubine",
)

#: The rotation under the Explorer's hero: Indian cinema only, a language
#: change at every turn.
EXPLORER_ROTATION: tuple[str, ...] = (
    "pushpa", "sholay", "padayappa", "narasimham", "kgf2", "meghe_dhaka_tara",
    "pokiri", "deewaar", "baashha", "mr_india", "gabbar_singh", "ddlj",
)

#: How long each line holds the slot, in seconds.
LINE_SECONDS = 6


def words(text: str) -> int:
    """Words in an excerpt; an ellipsis is not one."""
    return len(text.replace("…", " ").split())


def line_markup(d: Dialogue, *, delay: int | None = None) -> str:
    """One entry: the line, its translation, then film, year and language —
    and, for a subtitle or a paraphrase, the label that says so. Every
    string is escaped here, once, whatever its source."""
    en = f'<span class="en">{escape(d.english)}</span>' if d.english else ""
    note = f'<span class="note">{escape(d.note)}</span>' if d.note else ""
    style = f' style="animation-delay:{delay}s"' if delay is not None else ""
    return (f'<span class="ln"{style}><span class="q">“{escape(d.line)}”</span>{en}'
            f'<span class="by">{escape(d.credit)}</span>{note}</span>')


def rotation_markup(keys: tuple[str, ...], cls: str) -> str:
    """The lines stacked in one fixed slot, each fading in at its own delay
    — CSS alone, so nothing reruns and the slot never changes size. Hidden
    from assistive technology: a sentence that changes every six seconds is
    noise to a screen reader, and Home's marks speak the same lines in their
    own labels."""
    body = "".join(line_markup(BY_KEY[k], delay=i * LINE_SECONDS) for i, k in enumerate(keys))
    return f'<div class="tr-dlg {cls}" aria-hidden="true">{body}</div>'


def rotation_css(cls: str, count: int) -> str:
    """The fade for a rotation of ``count`` lines. Each line holds the slot
    for its slice of the loop; opacity and a 4px rise only (compositor work,
    never layout); still under ``prefers-reduced-motion``, where the first
    line simply stays."""
    period = count * LINE_SECONDS
    shown = 100 / count
    fade = min(1.2 / period * 100, shown / 4)
    return f"""
.{cls} .ln {{ animation: {cls}-turn {period}s linear infinite; }}
@keyframes {cls}-turn {{
  0% {{ opacity:0; transform:translateY(4px); }}
  {fade:.2f}% {{ opacity:1; transform:none; }}
  {shown - fade:.2f}% {{ opacity:1; transform:none; }}
  {shown:.2f}% {{ opacity:0; transform:translateY(-4px); }}
  100% {{ opacity:0; transform:translateY(-4px); }}
}}
@media (prefers-reduced-motion: reduce) {{
  .{cls} .ln {{ animation:none; }}
  .{cls} .ln:first-child {{ opacity:1; }}
}}
"""


#: The slot's look, shared by every rotation: a fixed two-line box (three on
#: a phone, where the line may wrap), the line in italic, the translation
#: quieter beside it, the attribution in the mono eyebrow style beneath.
CSS = """
.tr-dlg { position:relative; height:46px; margin-top:16px; overflow:hidden; pointer-events:none; }
.tr-dlg .ln { position:absolute; left:0; right:0; top:0; opacity:0; display:block; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; line-height:1.45; }
.tr-dlg .q { font-size:13.5px; font-style:italic; font-weight:600; color:var(--tr-text-2); }
.tr-dlg .en { margin-left:10px; font-size:12px; color:var(--tr-text-3); }
.tr-dlg .by { display:block; margin-top:4px; line-height:1.3; font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.18em;
  text-transform:uppercase; color:var(--tr-accent-soft); opacity:.85; }
.tr-dlg .note { position:absolute; right:0; bottom:0; font-size:9.5px; letter-spacing:.06em; color:var(--tr-text-3);
  border:1px solid var(--tr-border); border-radius:999px; padding:1px 7px; }
@media (max-width: 768px) {
  .tr-dlg { height:82px; margin-top:12px; }
  .tr-dlg .ln { white-space:normal; }
  .tr-dlg .en { display:block; margin:2px 0 0; }
  .tr-dlg .note { position:static; display:inline-block; margin-top:4px; }
}
"""


__all__ = [
    "BY_KEY",
    "CSS",
    "DIALOGUES",
    "EXPLORER_ROTATION",
    "HOME_ROTATION",
    "KINDS",
    "LINE_SECONDS",
    "MAX_WORDS",
    "Dialogue",
    "line_markup",
    "rotation_css",
    "rotation_markup",
    "words",
]
