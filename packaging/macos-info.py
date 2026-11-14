"""The macOS build's Info.plist completed after PyInstaller, before the signature: the version,
the oldest macOS the app starts on, and why it asks for the local network.
usage: python packaging/macos-info.py "dist/Readers Scanner.app" 1.2.2

The oldest macOS is not a guess: it is the highest « minos » of every program and library in the
app (numpy's wheels for Apple silicon ask for macOS 14, pdfium for 13). Without it an older Mac
opens the app and it vanishes at its first import, without a word; with it the Finder says why."""
import os
import plistlib
import re
import subprocess
import sys

app, version = sys.argv[1], sys.argv[2]
MACHO = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def minos(path):
    """The oldest macOS each architecture of this file asks for, as (major, minor)."""
    out = subprocess.run(["otool", "-arch", "all", "-l", path], capture_output=True, text=True).stdout
    found = []
    for block in re.split(r"Load command \d+", out):
        m = re.search(r"cmd LC_BUILD_VERSION.*?\n\s*minos (\d+)\.(\d+)", block, re.S) or \
            re.search(r"cmd LC_VERSION_MIN_MACOSX.*?\n\s*version (\d+)\.(\d+)", block, re.S)
        if m:
            found.append((int(m.group(1)), int(m.group(2))))
    return found


highest, by = (10, 13), "nothing"
for root, _dirs, files in os.walk(app):
    for name in files:
        path = os.path.join(root, name)
        if os.path.islink(path):
            continue
        try:
            with open(path, "rb") as f:
                if f.read(4) not in MACHO:
                    continue
        except OSError:
            continue
        for v in minos(path):
            if v > highest:
                highest, by = v, os.path.relpath(path, app)

plist = os.path.join(app, "Contents", "Info.plist")
with open(plist, "rb") as f:
    info = plistlib.load(f)
info["CFBundleShortVersionString"] = version
info["CFBundleVersion"] = version
info["LSMinimumSystemVersion"] = f"{highest[0]}.{highest[1]}"
# macOS 15 and later ask the user before the app reaches the scanners of the network
info["NSLocalNetworkUsageDescription"] = "Reader's Scanner looks for scanners on your network and asks them for pages."
info["NSBonjourServices"] = ["_uscan._tcp", "_uscans._tcp"]       # what parts/19_escl.py looks for
with open(plist, "wb") as f:
    plistlib.dump(info, f)
print(f"Info.plist: {version}, macOS {info['LSMinimumSystemVersion']} or later (because of {by})")
