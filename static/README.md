# static/

Hand-prepared artwork for the UI. Read through `ui.assets_registry`, never
by path. Design context: `docs/design-system.md` §4.

The folder is named `static` because that is the one directory Streamlit
serves (`server.enableStaticServing` in `.streamlit/config.toml`): a file
here is `app/static/<path>` to the browser, fetched once and revalidated by
ETag, which is how the Login page's posters avoid riding inside the page
as base64 on every rerun (`assets_registry.static_url`).

```
static/
  login/posters/     the Login wall — six fixed films, never BookMyShow's catalogue
  avatars/
    animation/       jack, jerry, kung_fu_panda, oggy, tom
    cartoon/         doraemon, dorami, gian, nobita, shizuka, suneo
    marvel/          black_widow, captain_america, hulk, iron_man, logan, spiderman, thor
    dc/              batman
    tollywood/       (empty — Jr NTR, Ram Charan, Prabhas, Mahesh Babu, Pawan Kalyan, Allu Arjun still to come)
  branding/          (empty — reserved for logo lockups and favicon)
```

There is no `radar/` folder on purpose: the radar is SVG + CSS, not an
image (`docs/design-system.md` §5).

## Adding a file

1. Name it `lowercase_snake_case.<ext>` with the extension that matches the
   bytes (`tests/test_assets.py` fails on a mismatch). The stem is the key.
2. Drop it in its category folder. A new avatar folder is a new category;
   add it to `AVATAR_CATEGORIES` in `ui/assets_registry.py` to place it in
   picker order.
3. Keep it under 300 KB if a page will embed it inline
   (`assets_registry.EMBED_LIMIT`); otherwise expect the phase that uses it
   to resize or serve it first.
4. Run `python -m pytest tests/test_assets.py`.

## Inventory (Phase 0 audit, 18 Sep 2026)

Posters — all JPEG, RGB, no alpha:

| key | px | ratio | KB |
|---|---|---|---|
| avengers_endgame | 1000×1482 | 0.675 | 428 ⚠ |
| interstellar | 440×697 | 0.631 | 64 |
| avatar | 1086×1609 | 0.675 | 486 ⚠ (embedded ICC profile) |
| rrr | 720×1280 | 0.562 | 136 |
| baahubali | 335×597 | 0.561 | 37 |
| spiderman_brand_new_day | 375×533 | 0.704 | 30 |

Avatars:

| category | key | format | px | KB |
|---|---|---|---|---|
| animation | jack | PNG RGB | 1254×1254 | 1998 ⚠ |
| animation | jerry | PNG RGB | 1254×1254 | 1856 ⚠ |
| animation | kung_fu_panda | JPEG | 447×447 | 21 |
| animation | oggy | PNG RGB | 1254×1254 | 1848 ⚠ |
| animation | tom | PNG RGB | 1254×1254 | 1952 ⚠ |
| cartoon | doraemon | PNG RGB | 1254×1254 | 1761 ⚠ |
| cartoon | dorami | PNG RGB | 1254×1254 | 1856 ⚠ |
| cartoon | gian | PNG RGB | 1254×1254 | 1811 ⚠ |
| cartoon | nobita | PNG RGB | 1254×1254 | 1669 ⚠ |
| cartoon | shizuka | PNG RGB | 1254×1254 | 1827 ⚠ |
| cartoon | suneo | PNG RGB | 1254×1254 | 1741 ⚠ |
| marvel | black_widow | JPEG | 447×447 | 23 |
| marvel | captain_america | JPEG | 548×559 | 62 (not square) |
| marvel | hulk | JPEG | 300×300 | 16 (smallest) |
| marvel | iron_man | JPEG | 736×736 | 106 |
| marvel | logan | JPEG | 736×736 | 87 |
| marvel | spiderman | PNG RGB | 324×576 | 222 (9:16 portrait — needs a crop to be an avatar) |
| marvel | thor | JPEG | 554×554 | 42 |
| dc | batman | JPEG | 554×554 | 29 |

⚠ = above `EMBED_LIMIT`; do not embed inline as-is.

Known issues, left for a deliberate decision (nothing was altered):

- **`jerry.png` and `dorami.png` are byte-identical** (md5 `2aeafb9e…`).
  One of them is the wrong picture.
- The ten 1254×1254 PNGs have no transparency and would be ~60–90 KB each
  as 512×512 WebP/JPEG. Total today: 18.3 MB of avatar PNG.
- The posters arrived as JPEG bytes under `.webp` names; they were renamed
  to `.jpg` (content untouched). If real WebP was intended, re-export.
- The tree was `assets/` in Phase 0 and became `static/` in Phase 1 so
  Streamlit could serve it; nothing else changed.
- `spiderman` (marvel) is a 9:16 portrait, `captain_america` is 548×559.
- Not present: hawkeye, doctor_strange, loki; every Tollywood avatar;
  anything in `branding/`.
