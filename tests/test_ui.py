#!/usr/bin/env python3
"""The window, driven like a user would (offscreen), with the stand-in scanner.
Run: QT_QPA_PLATFORM=offscreen python3 tests/test_ui.py [SHOTS_DIR]"""
import os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="rs-ui-")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else TMP + "/shots"
os.makedirs(SHOTS, exist_ok=True)
LANG = os.environ.get("TEST_LANG", "fr_CH.UTF-8")
os.environ.update(XDG_CONFIG_HOME=TMP + "/config", XDG_DATA_HOME=TMP + "/data", FAKE_SCANNER=TMP + "/scanner", LANG=LANG, LC_ALL=LANG,
                  READERS_SCANNER_NAPS2=f"{sys.executable} {HERE}/fake_naps2.py", READERS_SCANNER_SCANIMAGE=f"{sys.executable} {HERE}/fake_scanimage.py")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(HERE))
import readers_scanner as rs
from PyQt5 import QtCore, QtWidgets, QtTest

_ = rs._
PAGES = TMP + "/pages"
subprocess.run([sys.executable, HERE + "/make_pages.py", PAGES], check=True, capture_output=True)
SC = os.environ["FAKE_SCANNER"]
failed, clicks = [], 0
sys.stdout.reconfigure(line_buffering=True)


def check(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(label)


def scanner(feeder=(), glass=None, flags=()):
    for f in os.listdir(SC) if os.path.isdir(SC) else []:
        p = os.path.join(SC, f)
        if f != "devices.txt":
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    os.makedirs(SC + "/feeder", exist_ok=True)
    for i, f in enumerate(feeder):
        shutil.copyfile(f"{PAGES}/{f}", f"{SC}/feeder/{i:03d}.jpg")
    if glass:
        shutil.copyfile(f"{PAGES}/{glass}", SC + "/glass.jpg")
    for f in flags:
        open(f"{SC}/{f}", "w").close()
    if not os.path.exists(SC + "/devices.txt"):
        with open(SC + "/devices.txt", "w") as f:
            f.write("escl:http://localhost:60001\tHP\tScanJet Pro 4500 fn1 (USB)\tplaten,adf scanner\n"
                    "airscan:e0:HP ScanJet Pro 4500 fn1 (USB)\teSCL\tHP ScanJet Pro 4500 fn1 (USB)\tip=127.0.0.1\n")


app = QtWidgets.QApplication(sys.argv)


def wait(cond, seconds=60):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        if cond():
            app.processEvents()
            return True
        time.sleep(0.02)
    return False


def settle(ms=400):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        app.processEvents(); time.sleep(0.01)


def shot(w, name):
    settle(500)
    w.grab().save(f"{SHOTS}/{name}.png")


def click(widget):
    global clicks
    clicks += 1
    QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton)
    app.processEvents()


def chip(w, label):
    for i in range(w.review.folders.count()):
        b = w.review.folders.itemAt(i).widget()
        if b.text() == label:
            return b


def rows(w):
    return [(w.list.item(i).data(rs.KIND), w.list.item(i).text(), w.list.item(i).data(rs.SUB)) for i in range(w.list.count())]


def row(w, text):
    return next(w.list.item(i) for i in range(w.list.count()) if w.list.item(i).text() == text)


answers = {"text": [], "yes": True, "files": [], "save": None, "dir": None}
QtWidgets.QInputDialog.getText = staticmethod(lambda *a, **k: (answers["text"].pop(0), True) if answers["text"] else ("", False))
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes if answers["yes"] else QtWidgets.QMessageBox.No)
QtWidgets.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: (answers["files"], ""))
QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (answers["save"], ""))
QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: answers["dir"])
opened = []
from PyQt5 import QtGui
QtGui.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toLocalFile() or url.toString()) or True)

# ---- 1. first start ------------------------------------------------------------------------
scanner(feeder=["facture-1.jpg", "facture-2.jpg"])
w = rs.Main(); w.resize(1180, 800); w.show()
check("first start: the welcome, the folders", w.stack.currentWidget() is w.message and [r[0] for r in rows(w)] == ["all", "new"], str(rows(w)))
check("the scanner is found by itself", wait(lambda: (w.cfg.get("device") or {}).get("name", "").startswith("HP ScanJet"), 20) and w.scanner_line.text() == "HP ScanJet Pro 4500 fn1", w.scanner_line.text())
shot(w, "01-first-start")
expected = {"fr": "fra", "de": "deu", "es": "spa", "pt": "por", "ru": "rus", "en": "eng"}[LANG[:2]]
check("the text is read in the system's language unless told otherwise", w.cfg["lang"] == expected, w.cfg["lang"])
w.cfg["lang"] = "fra"; w.show_choices()          # the test's pages are French letters

# ---- 2. the shortest way: scan, Enter ------------------------------------------------------
clicks = 0
click(w.scan_button)
check("scanning is said, « scan » becomes « cancel »", w.scanning and w.scan_button.text() == _("cancel"))
check("two pages arrive in the review", wait(lambda: w.stack.currentWidget() is w.review and len(w.session["pages"]) == 2, 30))
check("the name field has the keyboard", wait(lambda: w.review.name.hasFocus(), 3))
shot(w, "02-review")
QtTest.QTest.keyClick(w.review.name, QtCore.Qt.Key_Return); clicks += 1
check("Enter files it: the document is open, its pages shown", w.stack.currentWidget() is w.doc_view and w.current and len(w.pages.pictures) == 2)
check("a document in two actions (scan, Enter)", clicks == 2, str(clicks))
doc1 = w.current
check("read and named by itself", wait(lambda: w.store.get(doc1)["ocr"] == rs.DONE, 60) and w.store.get(doc1)["name"] == "Facture d'électricité" and w.head.text() == "Facture d'électricité", w.head.text())
wait(lambda: all(p.image is not None for p in w.pages.pictures), 10)
shot(w, "03-document")
check("the list is on « all scans », the document chosen", w.place is None and rows(w)[0][1] == "Facture d'électricité" and w.list.currentItem().text() == "Facture d'électricité", str(rows(w)))
click(w.actions["text"])
check("its text, in paragraphs", "106,37 CHF" in w.text.toPlainText() and "Voici le décompte" in w.text.toPlainText())
shot(w, "04-text")
click(w.actions["text"])

# ---- 3. the glass, one page after the other, a new folder ----------------------------------
scanner(glass="contrat.jpg")
clicks = 0
click(w.scan_button)
check("empty feeder: the glass, by itself", wait(lambda: w.stack.currentWidget() is w.review and len(w.session["pages"]) == 1, 30))
scanner(glass="facture-2.jpg")
add = [w.review.grid.itemAt(i).widget() for i in range(w.review.grid.count())][-1]
click(add)
check("« + page »: a second page from the glass", wait(lambda: w.stack.currentWidget() is w.review and len(w.session["pages"]) == 2, 30))
answers["text"] = ["Logement"]
click(chip(w, "+ " + _("new folder")))
doc2 = w.current
check("a new folder made while filing: the document is in it", w.store.get(doc2)["folder"] == "Logement" and w.place == "Logement" and w.stack.currentWidget() is w.doc_view)
check("three clicks and a folder name", clicks == 3, str(clicks))
wait(lambda: w.store.get(doc2)["ocr"] == rs.DONE, 60)
check("named « Contrat de bail »", w.store.get(doc2)["name"] == "Contrat de bail", str(w.store.get(doc2)["name"]))

# ---- 4. both sides, blank backs, upside down, the review's tools ---------------------------
w.cfg["source"] = "duplex"; w.show_choices()
scanner(feeder=["upside-down.jpg", "blank.jpg", "facture-1.jpg", "blank-showthrough.jpg", "facture-2.jpg", "only-number.jpg"])
click(w.scan_button)
check("both sides: four pages, two blank backs left out", wait(lambda: w.stack.currentWidget() is w.review and w.session and len(w.session["pages"]) == 4, 60) and len(w.session["blank"]) == 2,
      str(w.session and (len(w.session["pages"]), len(w.session["blank"]))))
check("the sheet fed upside down is already upright", w.session["pages"][0]["rotation"] == 180, str([p["rotation"] for p in w.session["pages"]]))
check("the blank pages are said, and can be kept", w.review.blank.isVisible() and "2" in w.review.blank.text(), w.review.blank.text())
check("nothing left over from the screen before", len([c for c in w.review.grid_host.findChildren(QtWidgets.QWidget) if isinstance(c, (rs.Tile, rs.AddTile)) and c.isVisible()]) == 5
      and len(w.message.findChildren(QtWidgets.QPushButton)) <= 3)
wait(lambda: all(t.picture.image is not None for t in w.review.tiles.values()), 10)
shot(w, "05-review-duplex")
tiles = [w.review.grid.itemAt(i).widget() for i in range(w.review.grid.count())]
ids = [p["id"] for p in w.session["pages"]]
tiles[3].remove.emit(tiles[3].page)
check("delete a page", [p["id"] for p in w.session["pages"]] == ids[:3])
tiles = [w.review.grid.itemAt(i).widget() for i in range(w.review.grid.count())]
tiles[2].earlier.emit(tiles[2].page)
check("move a page earlier", [p["id"] for p in w.session["pages"]] == [ids[0], ids[2], ids[1]])
tiles = [w.review.grid.itemAt(i).widget() for i in range(w.review.grid.count())]
tiles[1].turn.emit(tiles[1].page)
check("turn a page", w.session["pages"][1]["rotation"] == 90)
tiles[1].turn.emit(tiles[1].page); tiles[1].turn.emit(tiles[1].page); tiles[1].turn.emit(tiles[1].page)
w._picked("look", "clean", w.look_picked)
check("the look is for every page, and remembered", all(p["look"] == "clean" for p in w.session["pages"]) and w.cfg["look"] == "clean" and _("clean") in w.review.look.text())
wait(lambda: all(t.picture.image is not None for t in w.review.tiles.values()), 15)
shot(w, "06-review-clean")

# a second window would find the pages again: closing the app does not lose a scan
s2 = rs.Main.restore_session
probe = type("P", (), {"session": None, "session_dir": w.session_dir, "store": w.store, "session_file": lambda self: os.path.join(w.session_dir, "session.json")})()
s2(probe)
check("pages not filed yet survive a restart", probe.session is not None and len(probe.session["pages"]) == 3)

# leaving the review for a document, and coming back
w.to_folders(); settle(100)
check("the scan not filed yet is a row of the list", rows(w)[0][0] == "session", str(rows(w)[:2]))
click_item = row(w, _("all scans")); w.item_clicked(click_item)
w.list.setCurrentItem(row(w, "Facture d'électricité")); settle(200)
check("a document can be looked at meanwhile", w.stack.currentWidget() is w.doc_view and w.current == doc1)
w.item_clicked(w.list.item(0))
check("and the review is back in one click", w.stack.currentWidget() is w.review and len(w.session["pages"]) == 3)
w.review.name.setText("Dossier complet")
click(chip(w, "Logement"))
doc3 = w.current
d3 = w.store.get(doc3)
check("a name typed, a click on the folder", d3["name"] == "Dossier complet" and d3["named"] and d3["folder"] == "Logement" and len(d3["pages"]) == 3)
check("the name field is empty for the next scan", w.review.name.text() == "")
wait(lambda: w.store.get(doc3)["ocr"] == rs.DONE, 90)
check("the typed name is kept after reading; the text is there", w.store.get(doc3)["name"] == "Dossier complet" and "Note de frais" in w.store.text(doc3)[0], str(w.store.text(doc3))[:120])
w.cfg["source"] = "auto"; w._picked("look", "original", w.look_picked)

# ---- 5. the list: folders, search, rename, move, delete -------------------------------------
w.to_folders(); settle(100)
check("the folders with their counts", [(r[1], r[2]) for r in rows(w)] == [(_("all scans"), "3"), ("Logement", "2"), ("+ " + _("new folder"), "")], str(rows(w)))
shot(w, "07-folders")
w.find.setText("loyer"); settle(200)
check("search finds the text of a page, in any folder", [r[1] for r in rows(w)] == ["Contrat de bail"] and "Loyer mensuel" in rows(w)[0][2], str(rows(w)))
shot(w, "08-search")
w.find.clear(); settle(100)
answers["text"] = ["Bail 2026"]
w.rename_doc(w.store.get(doc2))
check("rename", w.store.get(doc2)["name"] == "Bail 2026" and w.store.get(doc2)["named"])
answers["text"] = [""]
w.rename_doc(w.store.get(doc2))
check("an emptied name gives the first words back", w.store.get(doc2)["name"] == "Contrat de bail" and not w.store.get(doc2)["named"], str(w.store.get(doc2)["name"]))
w.move_docs([w.store.get(doc1)], "Logement")
check("move", w.store.get(doc1)["folder"] == "Logement" and w.store.count("Logement") == 3)
answers["text"] = ["Maison"]
w.rename_folder("Logement")
check("rename a folder: its documents follow", w.store.folder_names() == ["Maison"] and w.store.count("Maison") == 3)
answers["save"] = TMP + "/copie.pdf"
w.open_doc(doc1); w.save_copy()
check("save a copy", wait(lambda: os.path.exists(TMP + "/copie.pdf") and os.path.getsize(TMP + "/copie.pdf") > 100_000, 10))
w.open_pdf()
check("open the PDF: a file with the document's name, for the system's viewer", wait(lambda: len(opened) == 1, 10) and os.path.basename(opened[0]) == rs.file_name_of(w.store.get(doc1)) and os.path.exists(opened[0]), str(opened))
answers["dir"] = TMP + "/images"; os.makedirs(TMP + "/images")
w.save_images([w.store.get(doc1)])
check("the pages as pictures", wait(lambda: len(os.listdir(TMP + "/images")) == 2, 10), str(os.listdir(TMP + "/images")))
w.copy_text()
check("copy the text", "Total à payer" in app.clipboard().text())
w.delete_docs([w.store.get(doc3)])
check("delete", w.store.get(doc3) is None and w.store.count() == 2 and not os.path.exists(w.store.dir(doc3)))
w.delete_folder("Maison")
check("delete a folder: its documents stay in all scans", w.store.folder_names() == [] and w.store.count() == 2 and w.place == rs.FOLDERS)

# ---- 6. add pages to a document, edit its pages ---------------------------------------------
scanner(feeder=["facture-2.jpg"])
w.edit_pages(w.store.get(doc2), scan=True)
check("add pages from the scanner: the document's pages and the new one", wait(lambda: w.stack.currentWidget() is w.review and w.session and len(w.session["pages"]) == 3, 30) and w.session["doc"] == doc2)
check("an existing document is saved, not filed again", w.review.save_row.isVisible() and not w.review.filing.isVisible())
shot(w, "09-edit-pages")
click(w.review.save)
check("saved: three pages, read again", w.current == doc2 and len(w.store.get(doc2)["pages"]) == 3 and wait(lambda: w.store.get(doc2)["ocr"] == rs.DONE and w.store.get(doc2)["rev"] == 1, 60))
check("its text follows", len(w.store.text(doc2)) == 3 and "salutations" in w.store.text(doc2)[2])
w.edit_pages(w.store.get(doc2))
w.discard_session()
check("discard while editing: the document as it was", w.session is None and len(w.store.get(doc2)["pages"]) == 3 and w.current == doc2)

# ---- 7. when things go wrong ----------------------------------------------------------------
scanner(flags=["offline"])
click(w.scan_button)
check("scanner switched off: said in plain words, with what to do", wait(lambda: not w.scanning, 40) and w.stack.currentWidget() is w.message
      and w.message.title.text() == _("the scanner is not answering — is it switched on?") and w.message.sub.text() != "", w.message.title.text())
shot(w, "10-scanner-off")
scanner(feeder=["facture-1.jpg"])
w.message_action("scan")
check("switched on again: « scan again » works", wait(lambda: w.stack.currentWidget() is w.review and len(w.session["pages"]) == 1, 40))
w.discard_session()
check("discard asks, then leaves nothing behind", w.session is None and not os.path.exists(w.session_dir))
scanner(feeder=["facture-1.jpg"] * 6); open(SC + "/slow", "w").write("0.5")
click(w.scan_button)
wait(lambda: _("page %1", 1) in w.message.title.text(), 20)
check("the pages are counted as they pass", _("page %1", 1)[:4] in w.message.title.text(), w.message.title.text())
shot(w, "11-scanning")
click(w.scan_button)
check("« cancel » stops it and comes back", wait(lambda: not w.scanning, 20) and w.session is None and w.stack.currentWidget() is w.doc_view)
scanner()
w.cfg["source"] = "feeder"
click(w.scan_button)
check("feeder chosen and empty: said, with the way out", wait(lambda: not w.scanning, 20) and w.message.title.text() == _("the feeder is empty") and "«" in w.message.sub.text())
w.cfg["source"] = "auto"

os.environ["READERS_SCANNER_NAPS2"] = "/nonexistent/naps2"
w.naps2 = rs.Naps2(rs.DATA_DIR)
click(w.scan_button)
check("without NAPS2: the page that says what it is and where to get it", w.stack.currentWidget() is w.message and w.message.title.text() == _("Reader's Scanner needs NAPS2") and "naps2.com" in w.message.sub.text())
shot(w, "12-naps2-missing")
w.message_action("naps2")
check("« get NAPS2 » opens naps2.com", opened[-1].startswith("https://www.naps2.com"))
answers["files"] = [PAGES + "/contrat.jpg", TMP + "/copie.pdf"]
w.message_action("import")
check("from files, without NAPS2: a picture and a two-page PDF → three pages", wait(lambda: w.stack.currentWidget() is w.review and w.session and len(w.session["pages"]) == 3, 60))
QtTest.QTest.keyClick(w.review.name, QtCore.Qt.Key_Return)
doc4 = w.current
check("filed and read like a scan", wait(lambda: w.store.get(doc4)["ocr"] == rs.DONE, 90) and w.store.get(doc4)["name"] == "Contrat de bail")
os.environ["READERS_SCANNER_NAPS2"] = f"{sys.executable} {HERE}/fake_naps2.py"
w.message_action("again")
check("NAPS2 installed meanwhile: « look again » finds it", w.naps2.cmd is not None)

# ---- 8. the look of the app -----------------------------------------------------------------
w.open_doc(doc1); wait(lambda: all(p.image is not None for p in w.pages.pictures), 10)
w.toggle_theme(); wait(lambda: all(p.image is not None for p in w.pages.pictures), 10)
shot(w, "13-dark")
w.toggle_theme()
dlg = rs.SettingsDialog(w); dlg.show(); settle(300)
dlg.grab().save(f"{SHOTS}/14-settings.png")
dlg.look_again()
check("settings: « look again » lists the scanner", wait(lambda: dlg.devices is not None, 20) and dlg.scanner.count() == 1 and "ScanJet" in dlg.scanner.currentText(), dlg.scanner.currentText())
dlg.close()

# ---- 8b. with the phone (when wsgidav is there to play the server) ----------------------------
WSGIDAV = os.environ.get("WSGIDAV") or shutil.which("wsgidav")
server = None
if WSGIDAV:
    import socket
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0)); PORT = so.getsockname()[1]
    os.makedirs(TMP + "/dav")
    with open(TMP + "/dav.yaml", "w") as f:
        f.write(f'host: 127.0.0.1\nport: {PORT}\nprovider_mapping:\n  "/": {TMP}/dav\nhttp_authenticator:\n  domain_controller: null\n  accept_basic: true\n'
                '  accept_digest: false\n  default_to_digest: false\nsimple_dc:\n  user_mapping:\n    "*":\n      "test":\n        password: "x"\nverbose: 1\nlogging:\n  enable: false\n')
    server = subprocess.Popen([WSGIDAV, "--config", TMP + "/dav.yaml"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _i in range(100):
        try:
            socket.create_connection(("127.0.0.1", PORT), 0.2).close(); break
        except OSError:
            time.sleep(0.1)
    cfg = {"server": f"http://127.0.0.1:{PORT}", "folder": "Scans", "username": "test", "password": "x"}
    # the phone: another store, which scanned a letter into a folder this computer does not have
    phone = rs.Store(TMP + "/phone"); read = []
    pq = rs.ReadQueue(phone, rs.Reader(rs.DATA_DIR), on_done=read.append)
    pid, page = rs.new_id(), rs.new_id()[:8]
    os.makedirs(phone.dir(pid)); shutil.copyfile(PAGES + "/vertrag.jpg", phone.src_file(pid, page))
    phone.add_folder("Wohnung")
    phone.put({"id": pid, "created": rs.now_ms(), "modified": rs.now_ms(), "name": None, "named": False, "folder": "Wohnung", "lang": "deu",
               "pages": [{"id": page, "rotation": 0, "look": "original"}], "ocr": rs.PENDING, "rev": 0, "readBy": "", "remote": False, "pageCount": 0})
    pq.enqueue(pid); wait(lambda: pid in read, 240)
    rs.sync_run(phone, cfg, lambda d: phone.pdf_file(d["id"]), 20)
    before = w.store.count()
    w.cfg.update(cfg); w.sync()
    check("the account set: what the phone scanned is in the list, its folder too; what was scanned here went up", wait(lambda: not w.syncing and w.store.get(pid) is not None, 60)
          and "Wohnung" in w.store.folder_names() and w.store.count() == before + 1 and "↓" in w.status.text() and "↑" in w.status.text(), w.status.text())
    check("the PDFs are on the server under their dated names", sum(f.endswith(".pdf") for _r, _d, fs in os.walk(TMP + "/dav/Scans") for f in fs) == before + 1)
    check("not downloaded until it is opened", not w.store.has_pdf(pid))
    w.open_doc(pid)
    check("opened: its PDF comes down and its page shows", wait(lambda: w.store.has_pdf(pid) and len(w.pages.pictures) == 1 and w.pages.pictures[0].image is not None, 30))
    shot(w, "15-from-the-phone")
    w.find.setText("nebenkosten"); settle(500)
    check("and its words are found like the others'", w.list.count() >= 1 and any(w.list.item(i).data(QtCore.Qt.UserRole) == pid for i in range(w.list.count())),
          str([w.list.item(i).data(QtCore.Qt.UserRole) for i in range(w.list.count())]))
    w.find.setText(""); settle(300)
    phone_name = rs.title_of(w.store.get(pid))
    w.store.rename(pid, "Mietvertrag"); w.sync()
    wait(lambda: not w.syncing, 30)
    rs.sync_run(phone, cfg, lambda d: phone.pdf_file(d["id"]), 20)
    check("renamed here: the phone has the new name", phone.get(pid)["name"] == "Mietvertrag" and phone.get(pid)["named"], f"{phone_name} → {phone.get(pid)['name']}")

# ---- 9. leaving ------------------------------------------------------------------------------
scanner(feeder=["facture-1.jpg", "facture-2.jpg"])
click(w.scan_button)
wait(lambda: w.stack.currentWidget() is w.review and w.session and len(w.session["pages"]) == 2, 30)
QtTest.QTest.keyClick(w.review.name, QtCore.Qt.Key_Return)
doc5 = w.current
quit_called = []
QtWidgets.QApplication.quit = staticmethod(lambda: quit_called.append(1))
w.close()
check("closing right after filing: the window goes, the reading is finished first", not w.isVisible() and wait(lambda: bool(quit_called), 90) and rs.Store(rs.DATA_DIR + "/scans").get(doc5)["ocr"] == rs.DONE)
if server:
    check("and sent: its PDF is on the server before the app leaves", any(rs.file_name_of(rs.Store(rs.DATA_DIR + "/scans").get(doc5)) in fs for _r, _d, fs in os.walk(TMP + "/dav/Scans")))
    server.terminate()
else:
    print("skip the phone's part: wsgidav not found (pip install wsgidav cheroot, or WSGIDAV=…)")

print()
print("FAILED: " + ", ".join(failed) if failed else "all window tests passed")
print("screenshots in", SHOTS)
if not failed:
    shutil.rmtree(TMP + "/data", ignore_errors=True)
os._exit(1 if failed else 0)
