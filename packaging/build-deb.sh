#!/bin/bash
# Builds readers-scanner_<version>_all.deb next to this script. Needs dpkg-deb and fakeroot.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$HERE/.."
"$SRC/build.sh" >/dev/null
VERSION=$(grep -oE '^VERSION = "[^"]+"' "$SRC/readers_scanner.py" | cut -d'"' -f2)
ROOT="$HERE/deb-root"; rm -rf "$ROOT"
install -Dm755 "$SRC/readers_scanner.py" "$ROOT/usr/lib/readers-scanner/readers_scanner.py"
install -Dm644 "$HERE/readers-scanner.png" "$ROOT/usr/lib/readers-scanner/readers-scanner.png"
install -Dm755 /dev/stdin "$ROOT/usr/bin/readers-scanner" <<'SH'
#!/bin/sh
exec python3 /usr/lib/readers-scanner/readers_scanner.py "$@"
SH
install -Dm644 "$HERE/readers-scanner.desktop" "$ROOT/usr/share/applications/readers-scanner.desktop"
install -Dm644 "$HERE/readers-scanner.svg" "$ROOT/usr/share/icons/hicolor/scalable/apps/readers-scanner.svg"
install -Dm644 "$SRC/LICENSE" "$ROOT/usr/share/doc/readers-scanner/copyright"
mkdir -p "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<CTRL
Package: readers-scanner
Version: $VERSION
Section: graphics
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-pyqt5, python3-requests, python3-pil, python3-numpy, tesseract-ocr, poppler-utils
Recommends: naps2, sane-utils, tesseract-ocr-osd
Maintainer: funkypitt <pierregallaz@gmail.com>
Homepage: https://github.com/funkypitt/readers-scanner-desktop
Description: One-click scanning to searchable PDFs, filed with the phone's (needs NAPS2)
 REQUIRES NAPS2 (free, https://www.naps2.com/download), which drives the
 scanner and is not in the distribution's archive: install its .deb or its
 Flatpak. Without it the app still files pictures and PDFs you hand it.
 .
 One window and one button: the pages come from the feeder or the glass,
 blank backs are left out, upside-down sheets set upright, the text is read
 on this computer (Tesseract) and the PDF is filed in the folders shared with
 the Reader's Scanner Android app over your own WebDAV folder (kDrive,
 Nextcloud...). Black and white, no account with the app.
CTRL
fakeroot dpkg-deb --build "$ROOT" "$HERE/readers-scanner_${VERSION}_all.deb"
rm -rf "$ROOT"
echo "built $HERE/readers-scanner_${VERSION}_all.deb"
