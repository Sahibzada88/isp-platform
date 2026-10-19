"""Vercel entrypoint: Vercel's Python runtime serves the WSGI `app` object."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.wsgi import application as app  # noqa: E402,F401
