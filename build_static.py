"""Render the form to one self-contained HTML file (CSS + JS inlined).

    python build_static.py [output.html]

The result needs no server; it still exposes the window.ACR API.
"""
import sys
from pathlib import Path

from app import app, render_form

out = Path(sys.argv[1] if len(sys.argv) > 1 else "acr_form.html")
with app.test_request_context("/"):
    out.write_text(render_form(inline=True), encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size:,} bytes)")
