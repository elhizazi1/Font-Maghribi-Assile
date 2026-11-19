"""Sanity checks for every shipped weight (run in CI)."""
from pathlib import Path
from fontTools.ttLib import TTFont

root = Path(__file__).resolve().parents[1]
WEIGHTS = [(100, "Thin"), (200, "ExtraLight"), (300, "Light"), (400, "Regular"),
           (500, "Medium"), (600, "SemiBold"), (700, "Bold"), (800, "ExtraBold"),
           (900, "Black")]


def gsub_maps(font):
    out = []
    for lk in font["GSUB"].table.LookupList.Lookup:
        for st in lk.SubTable:
            if hasattr(st, "mapping"):
                out.append(("single", tuple(sorted(st.mapping.items()))))
            elif hasattr(st, "ligatures"):
                out.append(("lig", tuple(sorted((k, tuple((l.Component, l.LigGlyph) for l in v))
                                                for k, v in st.ligatures.items()))))
            else:
                out.append((type(st).__name__, st.Format))
    return out


ref = TTFont(root / "fonts/ttf/MaghribiAssile-Regular.ttf")
ref_order, ref_gsub = ref.getGlyphOrder(), gsub_maps(ref)
seen = set()
for w, style in WEIGHTS:
    ttf = root / f"fonts/ttf/MaghribiAssile-{style}.ttf"
    f = TTFont(ttf)
    assert f["OS/2"].usWeightClass == w, (style, f["OS/2"].usWeightClass)
    assert f.getGlyphOrder() == ref_order and len(ref_order) == 414, style
    assert gsub_maps(f) == ref_gsub, f"GSUB differs in {style}"
    assert f["name"].getDebugName(6) == f"MaghribiAssile-{style}"
    assert f["name"].getDebugName(1) in ("Maghribi Assile", f"Maghribi Assile {style}")
    assert f["hhea"].ascent == ref["hhea"].ascent and f["hhea"].descent == ref["hhea"].descent
    assert f["head"].unitsPerEm == 2048
    seen.add(w)
    print(f"OK: {ttf.name}, {len(ref_order)} glyphs, wght {w}")
assert seen == {w for w, _ in WEIGHTS}

vf = TTFont(root / "fonts/variable/MaghribiAssile[wght].ttf")
ax = vf["fvar"].axes[0]
assert (ax.axisTag, ax.minValue, ax.defaultValue, ax.maxValue) == ("wght", 100, 400, 900)
assert gsub_maps(vf) == ref_gsub
print("OK: variable font wght 100-900")

for p in sorted((root / "fonts/woff2").glob("*.woff2")):
    assert TTFont(p)["OS/2"].usWeightClass in seen
    print(f"OK: {p.name}")
