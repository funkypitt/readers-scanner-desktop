# Reader's Scanner (desktop) — notes

The design is in the phone's repository: `readers-scanner/docs/DESKTOP.md`; the shared folder's
format in `readers-scanner/docs/SYNC.md`. This file is what was learnt building it.

## State (1.0.0, 2026-09-27)

Validated by the three test suites (engine, window driven offscreen, two computers and a WebDAV
server) and by a live exchange with the Android app 1.1.0 on an emulator: a desktop scan received
and displayed by the phone, a rename on the phone followed by the desktop.

**Run on the real scanner on 2026-09-27** (HP ScanJet Pro 4500 fn1, USB, through the window:
« scan », Enter). What it taught, which the stand-in has learnt since:

- NAPS2 writes a profile's resolution as `<Resolution>Dpi300</Resolution>`. A number in a tag
  is not understood, and the driver then takes its lowest resolution: the first real pages came
  at 75 dpi (620 × 877). The stand-in now does the same, and a test checks the pages' size.
- Right after a scan the scanner may answer « busy » for a moment: waited for (5 tries, 2.5 s
  apart, « the scanner is getting ready… »), not reported.
- Blank sheets in a one-sided stack happen too (7 of the 8 sheets of that first stack): left
  out and kept aside in every feeder scan, not only with both sides.
- Measured: scanner found in 12 s at the first start, then never looked for again; 8 sheets
  from the feeder in 26 s (at 75 dpi); the glass at 300 dpi in 10.6 s; empty feeder noticed in
  2.5 s, « automatic » then on the glass: 12 s in all; « feeder » asked and empty: said in 2.5 s.
- Of the five routes to that scanner, airscan works; escl and hpaio answer « busy » or
  « interrupted » while airscan has just been used. The order tried is the right one.

- One printed sheet at 300 dpi: 19 s, 266 words read, 265 in the PDF's text layer.
- Cancel (on the glass): NAPS2 scans through a helper process; stopping NAPS2 alone left the
  helper and our pipe open, and « cancel » came back after 5.7 s. NAPS2 now runs in a process
  group of its own and the group is signalled (Windows: `taskkill /T`): back in 0.1 s. The
  scanner then needs a moment: the next scan waited twice on « busy » and took 32 s.
- The user's own NAPS2 window may be open at the same time (it was, for days): ours are told
  apart by their process group, never by name.

Still to run on it: **both sides** (asked once with a single sheet in the feeder: one page came,
where two sides were expected — not understood yet), a sheet fed upside down, a stack of blank
sheets after the change, cancel in the middle of a stack.

## The scanner asked directly (since 2026-09-27, the first choice)

Decided with the user after the first day on the real scanner: NAPS2 had been his suggestion,
and the day showed what four layers cost (the app → NAPS2 → SANE → a driver → the scanner): a
page lost somewhere in them, sentences to be recognised in 46 languages, ten seconds to find a
scanner. Most scanners made since about 2015 speak eSCL (AirScan, Mopria): HTTP and a little
XML. `parts/19_escl.py` asks them directly; NAPS2 stays for the others.

Measured on the HP ScanJet Pro 4500 fn1, the same day, directly against through NAPS2: found in
0.03 s against 12 s; the glass at 300 dpi in 6.3 s against 10.6 s; the empty feeder known at
once against 2.5 s; a scan right after another in 14.7 s against 26–32 s.

- The scanner says whether its feeder is loaded (`AdfState`) before anything moves: « automatic »
  no longer tries the feeder to learn that it is empty. It names a jam, an open cover, two
  sheets taken together, as codes.
- Its pages come one by one (`NextDocument` until 404); cancelling is a DELETE of the job.
- The address it gives for a job may be one only it knows (`http://ScanjetPro4500.local./…`):
  only its path is kept.
- It does not always write the resolution in the JPEG: `_stamp` writes it in the file's header,
  the picture untouched (the PDF's page size comes from it).
- Found: over USB through ipp-usb, which listens on this computer (ports 60000 and up, asked
  one by one: an instant); on the network by mDNS (`zeroconf`), three seconds. A scanner
  plugged in and on the network is found twice: two ways to one scanner.
- **Nobody is asked to choose a driver** (the user: « I hate that most scanner apps on Linux list
  ALL drivers »). `way_order`: a way that failed twice running last; then the better driver
  (the scanner itself, airscan, the makers', and sane's own escl last); then the cable before
  the network. A way asked directly that does not answer is known in a moment and the next
  one is taken: a cable pulled out costs nothing.
- Looking again, when no remembered way answers, is done in two stages: those that answer by
  themselves (3 s), then every way NAPS2 knows (15 s here).
- `READERS_SCANNER_DIRECT` (addresses, or empty for none) replaces the search in the tests:
  without it they would find the real scanner on the desk.

**A stack, both sides, on the real scanner (2026-09-27, 19:11)**: 4 sheets, 8 sides in 10.9 s,
3 blank backs left out, 5 pages kept, read in 9.8 s. The same stack through NAPS2 and sane's
escl had given one page, three times. What that scan taught:

- From its feeder the scanner announces 3508 lines in each JPEG and sends 3472 or 3488: a
  feeder does not know a sheet's length before it has passed. Tolerant readers (Pillow, Qt,
  poppler) fill the rest with grey; Tesseract's refuses the file, so no page was ever set
  upright. `jpeg_mend` counts the restart markers and writes the true height in the header, the
  picture untouched (checked on the five real files: same pixels but the last line). After it,
  the sheet fed upside down was seen (turn 180, confidence 17).
- Its pictures are lightly compressed: 19 MB for the five pages. `jpeg_slim` writes a page again
  at the app's quality (85) when that takes off more than a third: 4.8 MB, the same words read.
  The scanner's own `CompressionFactor` (0 to 11 on this one) means something else on every
  model and was left alone.

**The whole chain again, after the corrections (19:51)**: the same stack, 8 sides in 11.8 s, 3
blank backs left out, the sheet fed upside down turned by itself, the five pages read in 8.4 s,
a PDF of 4.9 MB that Ghostscript and poppler accept, its text layer the words read (1109 of
1111).

- A page scanned a little short (3472 lines) made a PDF page 3 mm shorter than A4: `write_pdf`
  now makes the page the sheet (A4 or Letter) when the picture is within 3 % of it, the picture
  at the top.

Not yet run on the real scanner: cancel in the middle of a stack.

## NAPS2, as it is driven

- NAPS2 is **needed only by the scanners that cannot be asked directly**, and is not shipped.
  It is said in the README, the package description, the installer, the screen shown when no
  scanner is found (with « get NAPS2 »), and the settings.
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

## Windows and macOS (2026-09-27)

Built and tested only on GitHub's machines (`.github/workflows/desktop-builds.yml`): there is no
Windows or Mac here, and no runner has a scanner.

State on 2026-09-27 (run 36330762778, commit 7057c6b): the four jobs pass — on each system the
three test suites, the build, the built app's self-test (3.7 s on Windows, installed by its
installer; on macOS taken out of its disk image), and the real NAPS2 installed on the runner
(8.1.4 on Windows by Chocolatey, 8.3.2 on macOS by Homebrew): found, no scanner listed, « not
found » understood on every driver, our own profile read. **No scan with a real scanner has
been made on Windows or macOS.** Sizes: installer 85 MB, disk images 105 and 112 MB (Tesseract
and its libraries 89 MB unpacked, of which ICU 30 and the models 27; PyQt5; numpy; pdfium).

What Windows taught, all of it true of the app and not only of the tests:

- **A file still open cannot be moved, replaced or deleted there.** Filing right after « + page »
  failed because the page's thumbnail was being drawn. `move`, `replace`, `remove`,
  `remove_tree` (parts/00_head.py) ask again for up to 4 s on Windows; nothing in the app calls
  `shutil.move`, `os.replace`, `os.remove` or `shutil.rmtree` directly any more.
- **An error nobody catches ends a PyQt5 program on the spot** (on every system). `unexpected`
  is the app's `sys.excepthook`: the error goes to `errors.log` in the data folder and the
  status line says so; the app goes on.
- **A widget without parent belongs to Python**, which destroys it as soon as nothing names it:
  `clear(layout)` did `setParent(None)`, and a click that emptied its own row destroyed the
  button in the middle of its click. It survived on Linux and macOS by luck.
- Python writes in cp1252 there unless told otherwise: the app names `utf-8` at every `open`,
  the tests reconfigure their output.
- Qt's « offscreen » platform has no fonts on Windows (every text is elided to nothing): the
  window tests use the real desktop there.

- **Tesseract travels with the app**: conda-forge's build (old systems too), gathered with the
  libraries it needs by `tools/bundle_tesseract.py`, with the models for orientation and English;
  the app finds it in `tesseract/` beside itself and tells it where its models are
  (`TESSDATA_PREFIX`). Other languages: the best model, fetched at the first reading, as on Linux.
- **PDF pages as pictures**: pypdfium2 where it is installed (the builds carry it), poppler's
  pdftoppm otherwise (the .deb). pdfium does one thing at a time: `_pdfium_lock`.
- **NAPS2 on Windows speaks the system's language** whatever the environment says (.NET
  Framework; `SetCulturesFromConfig` is only called with `--progress`), and nobody says in which
  code page a program without a console writes. `parts/18_naps2_words.py` (written by
  `tools/naps2_messages.py` from NAPS2's own resources) has its sentences in 46 languages;
  `error_in` tries the likely code pages until the line is one of them. A sentence not
  recognised is shown as NAPS2 wrote it, and « automatic » still goes on to the glass.
- Drivers: Windows `wia` then `twain`, macOS `apple` then `escl`; the scanners are listed by
  name and a scan names its scanner (`--device`), NAPS2 looking for it again each time.
- Windows: every program is started with CREATE_NO_WINDOW (otherwise a console flashes at each
  page read); cancelling ends NAPS2's process (no signal to send there).
- Windows: an installer (Inno Setup, per user, no administrator rights) rather than one big
  .exe, which would unpack 200 MB at every start.
- The tests' places and drivers: `READERS_SCANNER_HOME`, `READERS_SCANNER_DRIVER`,
  `READERS_SCANNER_TESSERACT`, `READERS_SCANNER_NAPS2`, `READERS_SCANNER_SCANIMAGE`.
