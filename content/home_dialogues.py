"""Home's hero rotation: famous cinema lines from around the world.

Separate from Home's 24 dialogue icons (``ui.home.LINES``), which are not
read or changed here, and from the Explorer's recent-Indian collection. No
line here repeats one of the 24 icons. Lines are the original words in Roman
letters — no regional scripts, no translations. Each was checked against the
publications named in ``source``; lines that could not be checked were left
out rather than guessed (no Kannada-language line could be verified yet).

The first six are the six Telugu stars, one line each, none of them a line
the 24 icons already carry.
"""

from __future__ import annotations

from content import Dialogue

#: The six Telugu stars — six different stars, one line each.
TELUGU_STARS: tuple[Dialogue, ...] = (
    Dialogue("Veera Shankar Reddy… mokke kada ani peekeste peeka kostha!", "Indra", 2002,
             "Indrasena Reddy (Chiranjeevi)", "Telugu",
             "Filmibeat, All Time Top 10 Dialogues in Telugu Movies (2014); TeluguOne; Telugu Movie Desk"),
    Dialogue("Flute jinka mundu oodu, simham mundu kaadu.", "Simha", 2010,
             "Balakrishna", "Telugu",
             "Deccan Chronicle, Saying it with a roar (2017); Filmibeat (2014); IndiaHerald — "
             "film confirmed as Simha; Balakrishna plays two roles and the character is not confirmed"),
    Dialogue("Pushpa ante flower anukuntiva… fire-uu!", "Pushpa: The Rise", 2021,
             "Pushpa Raj (Allu Arjun)", "Telugu",
             "Gulte; Deccan Herald; Republic World"),
    Dialogue("Nuvvu Nanda aithe nenu Badri, Badrinath.", "Badri", 2000,
             "Badri (Pawan Kalyan)", "Telugu",
             "Ragalahari, Badri completes 20 years (2020); Wikipedia; IndiaGlitz"),
    Dialogue("Evadu kodithe dimma tirigi mind block ayipotadho, vaade Pandugadu.", "Pokiri", 2006,
             "Pandu (Mahesh Babu)", "Telugu",
             "Sakshi Post, Mahesh Babu's Unforgettable Dialogues in Pokiri; Telugu Movie Desk; Wikipedia"),
    Dialogue("Iddaru kottukunte yuddham… ade okkade meeda padipothe danda yatra… idi Daya gadi danda yatra.",
             "Temper", 2015, "Daya (Jr NTR)", "Telugu",
             "Spotboye, Celebrating 10 Years Of Temper (2025); Telugu Stop teaser report; Filmibeat review"),
)

#: The rest of the world, in the order the rotation shows them after the six.
WORLD: tuple[Dialogue, ...] = (
    Dialogue("Kitne aadmi the?", "Sholay", 1975, "Gabbar Singh (Amjad Khan)", "Hindi",
             "The Quint; Pinkvilla, 9 memorable Sholay dialogues"),
    Dialogue("Avengers… assemble.", "Avengers: Endgame", 2019, "Steve Rogers", "English",
             "Sideshow; ScreenRant"),
    Dialogue("En vazhi thani vazhi.", "Padayappa", 1999, "Padayappa (Rajinikanth)", "Tamil",
             "Deccan Chronicle; The News Minute"),
    Dialogue("Here's looking at you, kid.", "Casablanca", 1942, "Rick Blaine", "English",
             "AFI's 100 Years…100 Movie Quotes"),
    Dialogue("Bab-eun meokgo daninya?", "Memories of Murder", 2003, "Park Doo-man", "Korean",
             "Korean film writing (tistory, Substack) on the line"),
    Dialogue("Mere paas maa hai.", "Deewaar", 1975, "Ravi (Shashi Kapoor)", "Hindi",
             "The Quint; Pinkvilla"),
    Dialogue("I can do this all day.", "Captain America: The First Avenger", 2011, "Steve Rogers", "English",
             "Digital Trends; ComicBook.com"),
    Dialogue("Nee po mone Dinesha.", "Narasimham", 2000, "Induchoodan (Mohanlal)", "Malayalam",
             "The Statesman; Onmanorama"),
    Dialogue("Frankly, my dear, I don't give a damn.", "Gone with the Wind", 1939, "Rhett Butler", "English",
             "AFI's 100 Years…100 Movie Quotes"),
    Dialogue("Jusqu'ici tout va bien.", "La Haine", 1995, "Hubert", "French",
             "Wikipedia; TV Tropes"),
    Dialogue("Naan oru thadava sonna, nooru thadava sonna maadhiri.", "Baashha", 1995,
             "Manikkam (Rajinikanth)", "Tamil", "Silverscreen India; Pinkvilla"),
    Dialogue("Dormammu, I've come to bargain.", "Doctor Strange", 2016, "Stephen Strange", "English",
             "WatchMojo; Looper"),
    Dialogue("Dada, ami banchte chai.", "Meghe Dhaka Tara", 1960, "Neeta", "Bengali",
             "Wikipedia; Deccan Herald"),
    Dialogue("You talking to me?", "Taxi Driver", 1976, "Travis Bickle", "English",
             "AFI's 100 Years…100 Movie Quotes"),
    Dialogue("Mogambo khush hua.", "Mr. India", 1987, "Mogambo (Amrish Puri)", "Hindi",
             "The Quint; Pinkvilla"),
    Dialogue("We have a Hulk.", "The Avengers", 2012, "Tony Stark", "English",
             "Bam Smack Pow; WatchMojo"),
    Dialogue("Buongiorno, principessa!", "La vita è bella", 1997, "Guido", "Italian",
             "Shmoop; DiLei"),
    Dialogue("I'm the king of the world!", "Titanic", 1997, "Jack Dawson", "English",
             "AFI's 100 Years…100 Movie Quotes"),
    Dialogue("…no puede cambiar de pasión.", "El secreto de sus ojos", 2009, "Pablo Sandoval", "Spanish",
             "Semanario Universidad; Cambio Colombia"),
    Dialogue("I am inevitable.", "Avengers: Endgame", 2019, "Thanos", "English",
             "Know Your Meme; CinemaBlend"),
    Dialogue("I'm gonna make him an offer he can't refuse.", "The Godfather", 1972, "Vito Corleone", "English",
             "AFI's 100 Years…100 Movie Quotes"),
    Dialogue("Qu'est-ce que c'est, « dégueulasse » ?", "À bout de souffle", 1960, "Patricia", "French",
             "Cinésérie; IMDb FAQ"),
    Dialogue("I am Steve Rogers.", "Avengers: Infinity War", 2018, "Steve Rogers", "English",
             "Ranker; ScreenRant"),
)

#: The whole rotation: the six Telugu stars first, then the world.
DIALOGUES: tuple[Dialogue, ...] = TELUGU_STARS + WORLD

__all__ = ["DIALOGUES", "TELUGU_STARS", "WORLD"]
