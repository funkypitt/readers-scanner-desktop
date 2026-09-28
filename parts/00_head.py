#!/usr/bin/env python3
"""Reader's Scanner — documents from a real scanner (flatbed, feeder), read on this computer,
filed in plain folders as PDFs that can be searched, and shared with the phone through a WebDAV
folder (kDrive, Nextcloud…). NAPS2 (naps2.com, installed separately) talks to the scanner;
Tesseract reads the text. One file, PyQt5 + requests + Pillow + numpy. MIT licence."""

import base64
import json
import locale
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, date, timedelta
from urllib.parse import quote, unquote, urljoin, urlparse

import numpy as np
import requests
from PIL import Image
from PyQt5 import QtCore, QtGui, QtWidgets

APP = "readers-scanner"
VERSION = "1.0.2"
Image.MAX_IMAGE_PIXELS = 200_000_000      # an A3 page at 600 dpi is not an attack


def _app_dirs():
    """The settings folder and the documents' folder, one place per desktop."""
    elsewhere = os.environ.get("READERS_SCANNER_HOME")      # the tests' own place
    if elsewhere:
        return os.path.join(elsewhere, "config"), os.path.join(elsewhere, "data")
    if sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Readers Scanner")
        return base, base
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/" + APP)
        return base, base
    return (os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), APP),
            os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), APP))


def _steady(act, *args):
    """Windows refuses to move, replace or delete a file that anything still has open — a
    thumbnail being drawn, an antivirus looking at a new file. It is a matter of a moment: asked
    again for a few seconds before it is an error. Elsewhere an open file moves like any other."""
    for attempt in range(40 if sys.platform == "win32" else 1):
        try:
            return act(*args)
        except PermissionError:
            if attempt == (39 if sys.platform == "win32" else 0):
                raise
            time.sleep(0.1)


def move(src, dst):
    return _steady(shutil.move, src, dst)


def replace(src, dst):
    return _steady(os.replace, src, dst)


def remove(path):
    return _steady(os.remove, path)


def remove_tree(path):
    """A folder and what is in it; on Windows, asked again while a file of it is still open."""
    for _attempt in range(40 if sys.platform == "win32" else 1):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.1)


def quiet():
    """For every program started: on Windows, without this, a console window flashes each time."""
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def bundled(*path):
    """A file shipped inside the app (the Windows and macOS builds carry Tesseract); None elsewhere."""
    base = getattr(sys, "_MEIPASS", None)
    p = os.path.join(base, *path) if base else None
    return p if p and os.path.exists(p) else None


def system_locale():
    """"fr_CH": Qt knows it on every desktop; the environment often does not (Windows, an app
    opened from the Finder)."""
    return QtCore.QLocale.system().name() or "en_US"


def said(raw):
    """What a program wrote, whatever the code page it wrote it in (Windows consoles have their own)."""
    for enc in ("utf-8", "oem" if sys.platform == "win32" else "latin-1", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", "replace")


CONFIG_DIR, DATA_DIR = _app_dirs()
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
