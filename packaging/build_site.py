"""Wraps site/landing.html into a full _site/index.html for GitHub Pages.

landing.html is written without <html>/<head>/<body> so it can also be published as a preview page;
everything before the first <header> goes into <head>.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "site" / "landing.html").read_text(encoding="utf-8")
cut = src.index("<header")
head, body = src[:cut], src[cut:]
out = ROOT / "_site"
out.mkdir(exist_ok=True)
(out / "index.html").write_text(
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
    "<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
    "img{max-width:100%}[hidden]{display:none!important}</style>\n"
    f"{head}</head>\n<body>\n{body}</body>\n</html>\n",
    encoding="utf-8",
)
print(f"wrote {out / 'index.html'}")

# The same interactive simulation can be previewed in conversation or on the website.
demo = (ROOT / "site" / "demo.html").read_text(encoding="utf-8")
(out / "demo.html").write_text(
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<title>Try SystemAutoBot — Interactive demo</title>'
    '<style>body{margin:0;padding:24px;background:#eef1eb;max-width:1000px;margin-inline:auto;'
    'font-family:Segoe UI,system-ui,sans-serif}nav{margin-bottom:16px}nav a{color:#18362d}'
    '@media(max-width:600px){body{padding:12px}}</style></head><body>'
    '<nav><a href="index.html">← Back to SystemAutoBot</a></nav>' + demo + '</body></html>\n',
    encoding="utf-8",
)
print(f"wrote {out / 'demo.html'}")
