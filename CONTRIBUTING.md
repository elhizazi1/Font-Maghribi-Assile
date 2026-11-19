# Contributing

Thank you for helping improve Maghribi Assile.

For visual issues, please include: style/weight, text to reproduce, font size, operating system, browser/app, and a screenshot. Arabic reports should mention whether the issue affects joining, dots, marks, or spacing. Latin reports should mention the exact glyphs or letter pairs.

Do not modify `index.html` while working on font geometry; the type lab is a stable interface. Submit font-outline changes separately from documentation and test changes. Preserve the original Regular outlines unless a change is explicitly reviewed.

Run `python -m pip install -r requirements.txt` then `python scripts/verify.py` before submitting a pull request.
