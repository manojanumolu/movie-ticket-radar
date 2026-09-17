# TicketRadar

TicketRadar is a personal movie-ticket monitoring application. You pick a
movie, the theatres you'd go to, the formats you care about (Dolby, IMAX,
4DX, …) and the dates you can make, and it watches the booking platform for
you — then emails you the moment matching tickets go on sale. It's a
Streamlit web app with sign-in, per-user cloud-backed data, and a
background worker that does the actual checking, so you don't have to keep
a booking page open and refreshing.

## Features

- **Movie, theatre and format monitoring** — one monitor covers several theatres and formats at once, each tracked independently.
- **Date preferences** — watch only the show dates you can attend.
- **Availability alerts by email** — a notification when tickets go live, and when new showtimes appear; no repeat mail for the same news.
- **Live status** — the open dashboard updates on its own as checks land.
- **Secure sign-in** — email/password or Google, with each person's monitors and history kept private to their account.
- **Monitor and history management** — start, stop, extend and delete monitors; see what the radar has found.

Currently supports BookMyShow listings, starting with Hyderabad.

## Live App

https://movie-ticket-radar.streamlit.app/
