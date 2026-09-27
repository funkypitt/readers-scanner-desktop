# Reader's Scanner (desktop) — notes

The design is in the phone's repository: `readers-scanner/docs/DESKTOP.md`; the shared folder's
format in `readers-scanner/docs/SYNC.md`. This file is what was learnt building it.

## State (1.0.0, 2026-09-27)

Validated by the three test suites (engine, window driven offscreen, two computers and a WebDAV
server) and by a live exchange with the Android app 1.1.0 on an emulator: a desktop scan received
and displayed by the phone, a rename on the phone followed by the desktop.

**Not yet run against a real scanner.** The HP ScanJet Pro 4500 fn1 was switched off on the days
of the build. Acquisition is tested against `tests/fake_naps2.py`, written from NAPS2's source
(v8.2.1: its messages, its exit code, when it writes the files); the private-profile mechanism
below was run against the real NAPS2 binary, up to "The selected scanner is offline." First
thing to do with the scanner on: feeder, glass, both sides, empty feeder, cancel.

## NAPS2, as it is driven

- NAPS2 **is required and is not shipped**. It is said in the README, the package description,
  the desktop entry, the first screen when it is missing (with « get NAPS2 »), and the settings.
- `naps2 console` always exits 0; what went wrong is a sentence on stdout, in the system's
  language → it is run with `LC_ALL=en_US.UTF-8 LANGUAGE=en` and the sentences are matched
  (`_ERRORS` in `parts/20_engine.py`).
- `--device NAME` enumerates every device first (about 10 s with SANE). Instead the app writes
  its own `profiles.xml` holding the device's id, in its own data folder, and points NAPS2 to it
  with `NAPS2_TEST_DATA=<folder>`: a scan starts in under 3 s, and the user's own NAPS2 profiles
  are never read nor changed.
- The scanner is looked for with `scanimage -f` when it is there (real SANE ids), otherwise
  with `naps2 console --listdevices`. One scanner is reachable by several routes (airscan, escl,
  hpaio…): they are tried in that order, the one that worked is remembered, and one new search
  is made by itself when the remembered one no longer answers.
- Pages are written by NAPS2 only when the whole scan is over; the count shown while scanning
  comes from its "Scanned page N." lines.
- « automatic » = feeder, and when NAPS2 answers "No pages are in the feeder.", the glass.

## Reading and the PDF

- Measured on a 300-dpi page: Tesseract's fast model reads « er juillet », « KWh »; the best
  model on the raw scan loses commas (« TVA 81% :797 CHF »); the best model on a grey copy with
  the paper evened out to white reads « TVA 8,1 % : 7,97 CHF », « 412 kWh », « 1er juillet ».
  Hence: the best model is fetched at the first reading in a language (setting, on by default),
  and the text is read from that copy (`reading_copy`), never shown nor kept.
- Tesseract writes a PDF from the image it read. To keep the page *as scanned* with the text read
  on the *cleaned* copy, the app writes the PDF itself (`write_pdf`): each JPEG goes in byte for
  byte, and the words are laid invisibly over it with Tesseract's own glyphless font (572 bytes,
  embedded), any alphabet. Checked with poppler (text, order, position of a word) and
  Ghostscript; displayed by Android's PdfRenderer.
- The `tsv`/`txt` config files are not found when `--tessdata-dir` points to the app's models:
  `-c tessedit_create_tsv=1 -c tessedit_create_txt=1` instead.
- Blank back: thumbnail 1200 px, 4 % of the edges left out, dark pixels counted. Measured:
  empty page 0, a lone page number 50, a letter about 8000; blank under 16.
- Upright: Tesseract's `--psm 0 -l osd`, followed only above a confidence of 2.5.

## Sync

A port of the phone's `Sync.kt`, folders by name. Found by `tests/test_sync.py` and fixed on
both sides (phone: in the source, for the release after 1.1.0):

- Renamed on both devices, the later one syncing second: the file stayed on the server under
  the loser's name, and the PDF was sent again beside it. The winner now starts from where the
  description says the file is.
- « named by hand » alone (confirming the automatic name) did not count as a change.
- Search: every word asked must be there, in any order (was: the exact phrase).

## Qt

- `deleteLater()` alone leaves the old widgets drawn until the event loop turns: `clear(layout)`
  hides and unparents them first.
- A test process that ends with `os._exit` must flush: `sys.stdout.reconfigure(line_buffering=True)`.
