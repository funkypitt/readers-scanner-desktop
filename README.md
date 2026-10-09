# Reader's Scanner (desktop)

> **Most scanners need nothing else.** Those made since about 2015 (AirScan, Mopria, eSCL) are
> asked directly, on the network or by USB. **The others need [NAPS2](https://www.naps2.com/download)**
> (free, open source), which is not installed with the app: on Linux it reaches them through
> SANE, on Windows through WIA or TWAIN, on macOS through Apple's drivers.
>
> **What it was tried on**: one real scanner, an HP ScanJet Pro 4500 fn1 on Linux, by USB and on
> the network (feeder, both sides, glass). The Windows and macOS builds pass the same tests on
> GitHub's machines, which have no scanner: no scan has been made with them yet. If yours
> misbehaves, [say so](https://github.com/funkypitt/readers-scanner-desktop/issues).

One window, one button. Put the sheets in the feeder or a page on the glass, press **scan**, press
**Enter**: a searchable PDF, dated and named by you, filed in the folders you share
with [Reader's Scanner](https://github.com/funkypitt/readers-scanner) on the phone. Black and white,
no account with the app, nothing leaves your computer except to your own WebDAV folder.

## Key points

- **Two actions for a document**: *scan* (or Ctrl+N), then Enter — or a click on the folder it
  belongs to. The name field has the keyboard as the pages arrive: type the document's name,
  or leave it to its date.
- **Nothing to set up for a scan, and nothing to choose.** The scanner is found by itself and
  remembered; of the ways to reach it, the app takes the best one that answers — the scanner
  itself before any driver, the cable before the network — and the next one when it stops
  answering. « automatic » takes the feeder when sheets are in it, the glass otherwise. 300 dpi colour, straightened.
  From the feeder, blank sheets and blank backs are left out (and can be put back); sheets fed upside down
  are set upright.
- **The text is read on this computer** (Tesseract), in the language chosen under the button;
  the most accurate model for that language is fetched once, by itself. The PDF keeps the page as
  scanned, with its text underneath to search and copy.
- **Review and filing on one screen**: turn, reorder, remove, « + page » for the next page on the
  glass, another look (as scanned, clean, grey, black and white). An unfiled scan survives a restart.
- **The phone's folders**: enter the same WebDAV server as on the phone (kDrive, Nextcloud…) and
  both see the same documents and folders. What was scanned elsewhere is listed and searchable at
  once; its PDF comes down when you open it. Renaming, moving and deleting travel both ways.
- **Plain files on this computer**: on Linux every document is also a PDF in `Scans`, in your
  documents folder, named and filed as on the server (`Scans/Factures/2026-09-27 19h51 Facture.pdf`),
  for the file manager and every other program; the phone's scans come down into it by
  themselves. Another folder, or none, in the settings (Windows and macOS: none until you give
  one). The app writes that folder and does not read it: rename, move and delete in the app.
- **Pictures and PDFs too**: drop them on the window or Ctrl+O; they are read and filed like a scan.
- Ctrl+F finds in names and text; F2 renames; Delete deletes; F5 syncs; Ctrl+T flips white on
  black / black on white; Ctrl+= and Ctrl+- change the size; Ctrl+, opens the settings.
- English, French, German, Spanish, Portuguese and Russian, following the system language.

More detail: [docs/NOTES.md](docs/NOTES.md).

## Install

NAPS2, **only if your scanner is not found by itself**: <https://www.naps2.com/download>
(Debian/Ubuntu: its `.deb`; others: Flatpak `com.naps2.Naps2`; Arch: `naps2-bin` from the AUR).
A scanner plugged in by USB is asked directly on Linux when `ipp-usb` is installed (it is, on
most distributions).

- Debian, Ubuntu, Pop!_OS: add the [apt repository](https://funkypitt.github.io/apt-repo/), then `sudo apt install readers-scanner`. Or take the `.deb` from the [latest release](https://github.com/funkypitt/readers-scanner-desktop/releases/latest): `sudo apt install ./readers-scanner_*_all.deb`. Tesseract and poppler come with it.
- Arch, Manjaro: `git clone https://github.com/funkypitt/readers-scanner-desktop && cd readers-scanner-desktop/packaging && makepkg -si`.
- Windows: `readers-scanner_…_windows_x64_setup.exe` from the latest release. It installs for you alone, without administrator rights.
- macOS: the `.dmg` from the latest release, `apple-silicon` or `intel`; drag the app to Applications.
- Anywhere else: `python3 readers_scanner.py` with PyQt5, requests, Pillow, numpy and zeroconf installed, and `tesseract` and `pdftoppm` (poppler) on the path — or `pip install pypdfium2` in place of poppler.

The Windows and macOS builds carry their own Tesseract. They
are not signed. Windows: *More info* › *Run anyway*. macOS: open the app once, then *System
Settings* › *Privacy & Security* › *Open Anyway* (before macOS 15: right click on the app › *Open*).

## Build and test

The program is one file, `readers_scanner.py`, glued from `parts/` by `./build.sh`.
`packaging/build-deb.sh` builds the .deb.

    python3 tests/test_engine.py                           # names, looks, blank pages, reading, the PDF
    QT_QPA_PLATFORM=offscreen python3 tests/test_ui.py     # the window, driven like a user
    python3 tests/test_sync.py                             # two computers and a WebDAV server (needs wsgidav)
    python3 tests/real_naps2.py                            # with the real NAPS2 installed, no scanner needed
    python3 readers_scanner.py --self-test -               # what any build must be able to do

The Windows installer, the two macOS disk images and the .deb are built by GitHub Actions at every
`v*` tag, each on its own system, after the same tests and the built app's own self-test.
`tools/bundle_tesseract.py` gathers the Tesseract they carry; `tools/naps2_messages.py` rewrites
the table of what NAPS2 says in its 46 languages (on Windows it answers in the system's).

The tests use stand-ins — a scanner that answers by itself (`tests/fake_escl.py`), NAPS2
(`tests/fake_naps2.py`) — and pages drawn for the purpose; they need no scanner.

## Crédits / Credits

© 2026 Pierre Gallaz. Développé avec [Claude Code](https://claude.com/claude-code) (Anthropic).
Licence MIT, voir `LICENSE`. Les scanners qui ne répondent pas d'eux-mêmes sont pilotés par [NAPS2](https://www.naps2.com) (GPL 2),
lancé comme programme séparé ; la lecture par [Tesseract](https://github.com/tesseract-ocr/tesseract) (Apache 2.0).

© 2026 Pierre Gallaz. Developed with [Claude Code](https://claude.com/claude-code) (Anthropic).
MIT licence, see `LICENSE`. Scanners that do not answer by themselves are driven by [NAPS2](https://www.naps2.com) (GPL 2), run as a
separate program; reading by [Tesseract](https://github.com/tesseract-ocr/tesseract) (Apache 2.0).
