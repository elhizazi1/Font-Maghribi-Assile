# Weights

| Weight | Style | Status |
|---:|---|---|
| 100 | Thin | Generated master (outline offset) |
| 200 | ExtraLight | Generated (interpolated from Thin/Regular/Black) |
| 300 | Light | Generated (interpolated from Thin/Regular/Black) |
| 400 | Regular | Original (hand-drawn master, untouched) |
| 500 | Medium | Generated (interpolated from Thin/Regular/Black) |
| 600 | SemiBold | Generated (interpolated from Thin/Regular/Black) |
| 700 | Bold | Generated (interpolated from Thin/Regular/Black) |
| 800 | ExtraBold | Generated (interpolated from Thin/Regular/Black) |
| 900 | Black | Generated master (outline offset) |

**Generated** means the outlines were produced by `scripts/build_weights.py`
from the Regular outlines (see README, "How the weights were made"). They are
real outline changes, not renamed copies of Regular, but they are **machine
generated and have not been reviewed or hand-corrected by a type designer**.
Thin (100) and Black (900) are the extremes and need the most review.
