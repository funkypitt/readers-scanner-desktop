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
VERSION = "1.0.0"
Image.MAX_IMAGE_PIXELS = 200_000_000      # an A3 page at 600 dpi is not an attack


def _app_dirs():
    """The settings folder and the documents' folder, one place per desktop."""
    if sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Readers Scanner")
        return base, base
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/" + APP)
        return base, base
    return (os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), APP),
            os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), APP))


CONFIG_DIR, DATA_DIR = _app_dirs()
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
