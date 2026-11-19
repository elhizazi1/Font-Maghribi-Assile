from pathlib import Path
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
STYLES = [('Thin', 100), ('ExtraLight', 200), ('Light', 300), ('Regular', 400), ('Medium', 500), ('SemiBold', 600), ('Bold', 700), ('ExtraBold', 800), ('Black', 900)]
for style, weight in STYLES:
    paths = [ROOT / f'fonts/{ext}/MaghribiAssile-{style}.{ext}' for ext in ('ttf', 'woff2')]
    fonts = [TTFont(p) for p in paths]
    for font in fonts:
        assert len(font.getGlyphOrder()) == 414, style
        assert font['OS/2'].usWeightClass == weight, style
        assert font['OS/2'].fsType == 0, style
        assert font['head'].fontRevision == 3.0, style
        assert {'GSUB', 'GPOS', 'cmap', 'glyf', 'name'}.issubset(font.keys()), style
    assert fonts[0].getBestCmap() == fonts[1].getBestCmap(), style
    assert fonts[0]['glyf'].compile(fonts[0]) == fonts[1]['glyf'].compile(fonts[1]), style
    print(f'PASS {style} {weight}: TTF + WOFF2, 414 glyphs, GSUB/GPOS')
print('All 9 styles verified (structural validation only).')
