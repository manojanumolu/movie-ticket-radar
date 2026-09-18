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
    animation/       jack, kung_fu_panda, oggy, tom
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

Avatars (Phase 2: every one at most 512 px and under 150 KB, rewritten by
`tools/optimize_avatars.py` — a resize and re-encode only; the originals are
in git history at `8df12a1`):

| category | key | format | px | KB |
|---|---|---|---|---|
| animation | jack | WEBP | 512×512 | 52 |
| animation | kung_fu_panda | JPEG | 447×447 | 21 |
| animation | oggy | WEBP | 512×512 | 46 |
| animation | tom | WEBP | 512×512 | 48 |
| cartoon | doraemon | WEBP | 512×512 | 38 |
| cartoon | dorami | WEBP | 512×512 | 42 |
| cartoon | gian | WEBP | 512×512 | 41 |
| cartoon | nobita | WEBP | 512×512 | 41 |
| cartoon | shizuka | WEBP | 512×512 | 44 |
| cartoon | suneo | WEBP | 512×512 | 40 |
| marvel | black_widow | JPEG | 447×447 | 23 |
| marvel | captain_america | WEBP | 502×512 | 66 |
| marvel | hulk | JPEG | 300×300 | 16 |
| marvel | iron_man | WEBP | 512×512 | 76 |
| marvel | logan | WEBP | 512×512 | 62 |
| marvel | spiderman | WEBP | 288×512 | 13 |
| marvel | thor | WEBP | 512×512 | 61 |
| dc | batman | WEBP | 512×512 | 33 |

⚠ = above `EMBED_LIMIT`; do not embed inline as-is.

Known issues, left for a deliberate decision (nothing was altered):

- `jerry.png` was a second copy of Dorami's picture (Phase 0 found them
  byte-identical; Phase 2 looked: it is Dorami). It was removed rather than
  offered as "Jerry". There is no Jerry artwork; add `jerry.<ext>` to
  `avatars/animation/` when there is.
- The ten 1254×1254 PNGs (18.3 MB) became 512×512 WebP (~40–50 KB each)
  in Phase 2; the whole avatar set is now 763 KB.
- `kung_fu_panda`, `hulk` and `batman` carry a white or checkerboard
  background inside the circle — that is in the artwork, not the UI.
- The posters arrived as JPEG bytes under `.webp` names; they were renamed
  to `.jpg` (content untouched). If real WebP was intended, re-export.
- The tree was `assets/` in Phase 0 and became `static/` in Phase 1 so
  Streamlit could serve it; nothing else changed.
- `spiderman` (marvel) is a 9:16 portrait, `captain_america` is 548×559.
- Not present: hawkeye, doctor_strange, loki; every Tollywood avatar;
  anything in `branding/`.
