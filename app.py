"""Vercel entrypoint for the public, read-only scanner.

Vercel's Python runtime looks for a top-level `app` in one of a handful of
filenames; this is that file and nothing else. All of the behaviour lives in
`pitstop/web_app.py`, which is also what runs under plain uvicorn locally:

    uvicorn app:app --reload

Note which application this is. `pitstop.server` is the local UI that plans and
applies repairs and holds OAuth tokens; it is deliberately not deployed.
`pitstop.web_app` audits and nothing else, and has no writer in its import
graph — asserted in tests/test_public_scan.py rather than merely intended.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The bundle root holds both this file and the `pitstop/` package. Adding it
# explicitly means the import works whether the platform runs us from the
# project root or from somewhere else.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pitstop.web_app import app  # noqa: E402,F401
