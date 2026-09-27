#!/usr/bin/env python3
"""The engine without a window: names, looks, blank pages, the scan's smart defaults (with the
stand-in for NAPS2), reading (the real Tesseract), the PDF. Run: python3 tests/test_engine.py"""
import os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
PY = sys.executable.replace("\\", "/")
TMP = os.path.realpath(tempfile.mkdtemp(prefix="rs-test-")).replace("\\", "/")
os.environ.update(READERS_SCANNER_HOME=TMP, READERS_SCANNER_DRIVER="sane", READERS_SCANNER_DIRECT="", FAKE_SCANNER=TMP + "/scanner", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8",
                  READERS_SCANNER_NAPS2=f"{PY} {HERE}/fake_naps2.py", READERS_SCANNER_SCANIMAGE=f"{PY} {HERE}/fake_scanimage.py")
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import faulthandler
faulthandler.enable()          # a crash in Qt says where it happened
import readers_scanner as rs

PAGES = TMP + "/pages"
subprocess.run([PY, HERE + "/make_pages.py", PAGES], check=True)
SC = os.environ["FAKE_SCANNER"]
failed = []


def check(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(label)


def scanner(feeder=(), glass=None, flags=(), devices=None):
    shutil.rmtree(SC, ignore_errors=True)
    os.makedirs(SC + "/feeder")
    for i, f in enumerate(feeder):
        shutil.copyfile(f"{PAGES}/{f}", f"{SC}/feeder/{i:03d}.jpg")
    if glass:
        shutil.copyfile(f"{PAGES}/{glass}", SC + "/glass.jpg")
    for f in flags:
        open(f"{SC}/{f}", "w").close()
    with open(SC + "/devices.txt", "w") as f:
        f.write(devices if devices is not None else
                "escl:http://localhost:60001\tHP\tScanJet Pro 4500 fn1 (USB)\tplaten,adf scanner\n"
                "airscan:e0:HP ScanJet Pro 4500 fn1 (USB)\teSCL\tHP ScanJet Pro 4500 fn1 (USB)\tip=127.0.0.1\n"
                "hpaio:/usb/HP_ScanJet_Pro_4500_fn1?serial=X\tHewlett-Packard\tHP_ScanJet_Pro_4500_fn1\tall-in-one\n")


def log():
    try:
        return open(SC + "/log.txt").read().splitlines()
    except OSError:
        return []


# --- names ---------------------------------------------------------------------------------
check("first words of a letter", rs.first_words("Swisscom SA\nFacture septembre 2026\nMonsieur,") == "Swisscom SA Facture septembre 2026")
check("debris lines skipped", rs.first_words("| ~ — ! . ,\n~~ i |\nAssurance maladie\nDécompte de prestations") == "Assurance maladie Décompte")
check("cyrillic", rs.first_words("Договор аренды квартиры\n№ 12") == "Договор аренды квартиры")
check("nothing readable", rs.first_words("| | ~ . , ;\n\n") is None)
d = {"created": 1790423589298, "name": "Facture: d'électricité / 2026"}
check("file name safe, dated", rs.file_name_of(d) == rs.stamp_of(d["created"]) + " Facture d'électricité 2026.pdf", rs.file_name_of(d))
check("folder name", rs.folder_name_of("  Impôts / 2026. ") == "Impôts 2026" and rs.folder_name_of(" .. ") is None)
check("reflow joins prose, keeps lists", rs.reflow("Nous vous remercions de votre confiance. Voici le\ndécompte de votre consommation pour la période du\n1er juillet au 31 août 2026.\n\nTotal : 412 kWh\nTVA : 7,97 CHF")
      == "Nous vous remercions de votre confiance. Voici le décompte de votre consommation pour la période du 1er juillet au 31 août 2026.\n\nTotal : 412 kWh\nTVA : 7,97 CHF")

# --- pages ---------------------------------------------------------------------------------
check("blank page seen as blank", rs.is_blank(PAGES + "/blank.jpg"))
check("show-through and dust: still blank", rs.is_blank(PAGES + "/blank-showthrough.jpg"))
check("a page number alone is not blank", not rs.is_blank(PAGES + "/only-number.jpg"))
check("a letter is not blank", not rs.is_blank(PAGES + "/facture-1.jpg"))
import numpy as np
from PIL import Image
for look in ("clean", "grey", "bw"):
    out = f"{TMP}/look-{look}.jpg"
    t0 = time.time()
    rs.render_page(PAGES + "/facture-1.jpg", out, 0, look)
    a = np.asarray(Image.open(out).convert("L"), dtype=np.float32)
    paper = float(np.median(a)); left, right = float(a[:, 20:200].mean()), float(a[:, -200:-20].mean())
    check(f"look {look}: white paper, even from edge to edge, ink kept", paper > 250 and abs(left - right) < 3 and float(a.min()) < 60,
          f"paper {paper:.0f} left {left:.0f} right {right:.0f} min {a.min():.0f} in {time.time() - t0:.1f}s")
rs.render_page(PAGES + "/facture-1.jpg", TMP + "/turned.jpg", 90, "original")
check("a turned page is wider than high", Image.open(TMP + "/turned.jpg").size == (3508, 2480))
rs.render_page(PAGES + "/facture-1.jpg", TMP + "/same.jpg", 0, "original")
check("an untouched scan is kept byte for byte", open(TMP + "/same.jpg", "rb").read() == open(PAGES + "/facture-1.jpg", "rb").read())

# --- the scanner ---------------------------------------------------------------------------
scanner()
n = rs.Naps2(rs.DATA_DIR)
check("NAPS2 found, version read", n.cmd and n.version == "8.2.1", str(n.version))
devs = n.devices()
check("three ways to one scanner, driverless first", [d["backend"] for d in devs] == ["airscan", "hpaio", "escl"] and len({d["key"] for d in devs}) == 1, str(devs))
cfg = {"format": "a"}
out = TMP + "/incoming"

scanner(feeder=["facture-1.jpg", "facture-2.jpg"], glass="contrat.jpg")
seen = []
r = rs.scan_pages(n, cfg, "auto", out, on_page=seen.append)
check("automatic: paper in the feeder → the feeder, both pages", r["error"] is None and r["source"] == "feeder" and len(r["files"]) == 2 and seen == [1, 2], str(r))
check("the pages come at 300 dpi, the size of the sheet", [Image.open(f).size for f in r["files"]] == [(2480, 3508)] * 2, str([Image.open(f).size for f in r["files"]]))
check("the scanner is remembered with its ways", r["device"]["routes"][0]["backend"] == "airscan" and len(r["device"]["routes"]) == 3)
cfg["device"] = r["device"]
check("no search for scanners once one is known (a profile names it)", not any("--listdevices" in l or "--device" in l for l in log()) and any("-p readers-scanner" in l for l in log()))

scanner(glass="contrat.jpg")
r = rs.scan_pages(n, cfg, "auto", out)
check("automatic: empty feeder → the glass", r["error"] is None and r["source"] == "glass" and len(r["files"]) == 1, str(r))

scanner(glass="contrat.jpg", flags=["nofeeder"])
r = rs.scan_pages(n, cfg, "auto", out)
check("automatic: a scanner without feeder → the glass", r["error"] is None and r["source"] == "glass", str(r))

scanner()
r = rs.scan_pages(n, cfg, "feeder", out)
check("feeder asked, feeder empty → said so", r["error"] == "empty" and rs.error_text(r["error"]) == "the feeder is empty", str(r))

scanner(feeder=["facture-1.jpg", "blank.jpg", "facture-2.jpg", "blank-showthrough.jpg"])
r = rs.scan_pages(n, cfg, "duplex", out)
check("both sides: the two blank backs left out, kept aside", r["error"] is None and len(r["files"]) == 2 and len(r["blank"]) == 2, str(r))

scanner(feeder=["facture-1.jpg"])
open(SC + "/busy", "w").write("2")
states = []
r = rs.scan_pages(n, cfg, "auto", out, on_state=states.append)
check("the scanner still busy with the scan before: a moment's patience, then the pages", r["error"] is None and len(r["files"]) == 1 and states.count("waiting") == 2, f"{r} {states}")

scanner(feeder=["facture-1.jpg"], flags=["offline-airscan"])
r = rs.scan_pages(n, cfg, "auto", out)
check("one driver does not answer → the next one; the order of preference stays", r["error"] is None and [x["backend"] for x in r["device"]["routes"]] == ["airscan", "hpaio", "escl"]
      and any("hpaio" in l for l in open(n.data + "/profiles.xml")), str(r.get("device")))
cfg["device"] = r["device"]
scanner(feeder=["facture-1.jpg"], flags=["offline-airscan"])
r = rs.scan_pages(n, cfg, "auto", out)
check("the same driver silent twice running: it goes behind the others", r["error"] is None and [x["backend"] for x in r["device"]["routes"]] == ["hpaio", "escl", "airscan"], str(r.get("device")))
cfg["device"] = r["device"]
scanner(feeder=["facture-1.jpg"])
n.alive = 0

scanner(feeder=["facture-1.jpg"], flags=["offline"])
t0 = time.time()
r = rs.scan_pages(n, cfg, "auto", out)
check("scanner off → said so, after one new search", r["error"] == "offline" and sum("--listdevices" in l for l in log()) == 0, str(r))

scanner(feeder=["facture-1.jpg"], devices="escl:http://localhost:60002\tHP\tScanJet Pro 4500 fn1 (USB)\tplaten,adf scanner\n")
r = rs.scan_pages(n, cfg, "auto", out)
check("the scanner's address changed → found again by itself", r["error"] is None and r["device"]["routes"][0]["id"].endswith("60002"), str(r))

scanner(devices="")
r = rs.scan_pages(n, {"format": "a"}, "auto", out)
check("no scanner at all → said so", r["error"] == "nodevice", str(r))

scanner(feeder=["facture-1.jpg"] * 5, flags=[])
open(SC + "/slow", "w").write("0.4")
import threading
threading.Timer(0.7, n.cancel).start()
r = rs.scan_pages(n, cfg, "feeder", out)
check("cancel stops the scan", r["error"] == "cancelled", str(r))

os.environ["READERS_SCANNER_NAPS2"] = "/nonexistent/naps2"
missing = rs.Naps2(rs.DATA_DIR)
check("NAPS2 absent and no scanner that answers by itself: said so", missing.version is None and rs.scan_pages(missing, cfg, "auto", out)["error"] == "nonaps2")
os.environ["READERS_SCANNER_NAPS2"] = f"{PY} {HERE}/fake_naps2.py"

# --- the scanner asked directly (eSCL) -----------------------------------------------------
sys.path.insert(0, HERE)
import fake_escl
scanner(feeder=["facture-1.jpg", "facture-2.jpg"], glass="contrat.jpg")
url, stop = fake_escl.serve(SC)
os.environ["READERS_SCANNER_DIRECT"] = url


def asked():
    try:
        return open(SC + "/direct.txt").read().splitlines()
    except OSError:
        return []


d = rs.Naps2(TMP + "/direct")
t0 = time.time()
ways = d.devices(every=False)
check("a scanner that answers by itself is found at once, and NAPS2 is not asked", [(x["backend"], x["name"], x["feeder"], x["duplex"]) for x in ways] == [("direct", "HP ScanJet Pro 4500 fn1", True, True)]
      and time.time() - t0 < 2 and [l for l in log() if l != "--help"] == [], f"{ways} {log()}")
ways = d.devices()
check("with NAPS2's ways behind it, one scanner all the same", [x["backend"] for x in ways] == ["direct", "airscan", "hpaio", "escl"] and len({x["key"] for x in ways}) == 1, str([(x["backend"], x["key"]) for x in ways]))
seen = []
known = {"format": "a"}
scanner(feeder=["facture-1.jpg", "facture-2.jpg"], glass="contrat.jpg")
r = rs.scan_pages(d, known, "auto", out, on_page=seen.append)
check("automatic: the scanner says its feeder is loaded → both sheets, as they come", r["error"] is None and r["source"] == "feeder" and seen == [1, 2]
      and [Image.open(f).size for f in r["files"]] == [(2480, 3508)] * 2 and [Image.open(f).info.get("dpi") for f in r["files"]] == [(300, 300)] * 2, f"{r} {seen}")
check("asked in its own words: the feeder, one side, 300 dpi; its job's address made ours", "POST /eSCL/ScanJobs Feeder 300" in asked() and any(a.startswith("GET /eSCL/ScanJobs/15/NextDocument") for a in asked()), str(asked()))
check("NAPS2 had no part in it", [l for l in log() if l != "--help"] == [] and r["device"]["routes"][0]["backend"] == "direct", str(log()))
known["device"] = r["device"]
scanner(glass="contrat.jpg")
t0 = time.time()
r = rs.scan_pages(d, known, "auto", out)
check("automatic: it says its feeder is empty → the glass, without trying the feeder", r["error"] is None and r["source"] == "glass" and len(r["files"]) == 1
      and not any("Feeder" in a for a in asked()), f"{r} {asked()}")
scanner()
t0 = time.time()
r = rs.scan_pages(d, known, "feeder", out)
check("feeder asked, feeder empty → said at once", r["error"] == "empty" and time.time() - t0 < 1, f"{r} {time.time() - t0:.2f}")
scanner(feeder=["facture-1.jpg", "blank.jpg", "facture-2.jpg", "blank-showthrough.jpg"])
r = rs.scan_pages(d, known, "duplex", out)
check("both sides: asked as such, the blank backs left out", r["error"] is None and len(r["files"]) == 2 and len(r["blank"]) == 2 and "POST /eSCL/ScanJobs Feeder duplex 300" in asked(), f"{r} {asked()}")
scanner(feeder=["facture-1.jpg"])
open(SC + "/busy", "w").write("2")
r = rs.scan_pages(d, known, "auto", out)
check("busy with the scan before: waited for", r["error"] is None and len(r["files"]) == 1 and sum(a.startswith("POST") for a in asked()) == 3, f"{r} {asked()}")
for flag, word, text in (("jam", "jam", "paper jam in the scanner"), ("multipick", "multipick", "two sheets went in together")):
    scanner(feeder=["facture-1.jpg"], flags=[flag])
    r = rs.scan_pages(d, known, "auto", out)
    check(f"the scanner says « {flag} »: said in words, nothing scanned", r["error"] == word and rs.error_text(word) == text and not any(a.startswith("POST") for a in asked()), f"{r}")
scanner(glass="contrat.jpg", flags=["nofeeder"])
d2 = rs.Naps2(TMP + "/direct2")
r = rs.scan_pages(d2, {"format": "a"}, "auto", out)
check("a scanner without feeder: the glass", r["error"] is None and r["source"] == "glass" and r["device"]["routes"][0]["feeder"] is False, str(r))
scanner(feeder=["facture-1.jpg"] * 5)
open(SC + "/slow", "w").write("0.4")
threading_timer = __import__("threading").Timer(0.9, d.cancel)
threading_timer.start()
t0 = time.time()
r = rs.scan_pages(d, known, "feeder", out)
check("cancel: the scanner is told, the sheets not yet taken stay in the feeder", r["error"] == "cancelled" and any(a.startswith("DELETE /eSCL/ScanJobs/") for a in asked())
      and 1 <= len(os.listdir(SC + "/feeder")) <= 4 and time.time() - t0 < 3, f"{r} {asked()[-3:]} {os.listdir(SC + '/feeder')}")
# the pictures as the real scanner sends them
scanner(feeder=["upside-down.jpg", "facture-1.jpg"], flags=["asreal"])
r = rs.scan_pages(d, known, "feeder", out)
sizes = []
for f in r["files"]:
    with Image.open(f) as im:
        im.load()                      # a strict reading: a file that announces more lines than it holds fails here
        sizes.append((im.size, im.info.get("dpi")))
check("a file announcing more lines than it holds is set right: any program reads it", r["error"] is None and sizes == [((2480, 3472), (300, 300))] * 2, str(sizes))
check("and much lighter than the scanner made it", all(os.path.getsize(f) < 1_500_000 for f in r["files"]), str([os.path.getsize(f) for f in r["files"]]))
turn = rs.upright_rotations(r["files"], rs.Reader(rs.DATA_DIR))
check("so the sheet fed upside down is seen", turn == {r["files"][0]: 180}, str(turn))
whole = TMP + "/whole.jpg"
shutil.copyfile(PAGES + "/facture-1.jpg", whole)
check("a whole and light picture is left as it is, byte for byte", rs.jpeg_mend(whole) is None and rs.jpeg_slim(whole, 300) == 0 and open(whole, "rb").read() == open(PAGES + "/facture-1.jpg", "rb").read())

# plugged in and on the network: two ways to one scanner
cable = url.replace("127.0.0.1", "localhost")
os.environ["READERS_SCANNER_DIRECT"] = f"{url},{cable}"
both = rs.Naps2(TMP + "/both")
ways = both.devices(every=False)
check("by cable and by the network: one scanner, the cable first", [(x["link"], x["url"]) for x in ways] == [("usb", cable), ("net", url)] and len({x["key"] for x in ways}) == 1, str(ways))
scanner(feeder=["facture-1.jpg"])
r = rs.scan_pages(both, {"format": "a"}, "auto", out)
check("the scan goes by the cable", r["error"] is None and r["device"]["routes"][0]["link"] == "usb" and len(r["device"]["routes"]) == 2, str(r.get("device")))
stale = dict(r["device"], routes=[dict(r["device"]["routes"][0], url="http://localhost:9", id="http://localhost:9")] + r["device"]["routes"][1:])
scanner(feeder=["facture-1.jpg"])
t0 = time.time()
r = rs.scan_pages(both, {"format": "a", "device": stale}, "auto", out)
check("the cable pulled out: known in a moment, the scan goes by the network", r["error"] is None and len(r["files"]) == 1 and time.time() - t0 < 3
      and [x["misses"] for x in r["device"]["routes"]] == [1, 0], f"{r.get('device')} {time.time() - t0:.1f} s")
scanner(feeder=["facture-1.jpg"])
r = rs.scan_pages(both, {"format": "a", "device": r["device"]}, "auto", out)
check("twice running: the network goes first from now on", r["error"] is None and [x["link"] for x in r["device"]["routes"]] == ["net", "usb"], str(r.get("device")))
os.environ["READERS_SCANNER_DIRECT"] = url
os.environ["READERS_SCANNER_NAPS2"] = "/nonexistent/naps2"
alone = rs.Naps2(TMP + "/alone")
scanner(feeder=["facture-1.jpg"])
r = rs.scan_pages(alone, {"format": "a"}, "auto", out)
check("without NAPS2 at all: a scanner that answers by itself scans", alone.cmd is None and r["error"] is None and len(r["files"]) == 1, str(r))
os.environ["READERS_SCANNER_NAPS2"] = f"{PY} {HERE}/fake_naps2.py"
stop()
scanner(feeder=["facture-1.jpg"])
d.alive = 0
r = rs.scan_pages(d, known, "auto", out)
check("it no longer answers at its address: NAPS2's ways are looked for, and the scan is made", r["error"] is None and len(r["files"]) == 1 and any("--listdevices" in l or "-p readers-scanner" in l for l in log()), f"{r} {log()}")
os.environ["READERS_SCANNER_DIRECT"] = ""

# --- as on Windows and macOS ---------------------------------------------------------------
# no SANE there: NAPS2 lists the scanners by name, one driver after the other, and on Windows
# it answers in the system's language, in the console's code page
os.environ["READERS_SCANNER_DRIVER"] = "wia,twain"
scanner(feeder=["facture-1.jpg", "facture-2.jpg"], devices="twain:0\tCanon\tDR-C225 TWAIN\tscanner\n")
w = rs.Naps2(TMP + "/windows")
devs = w.devices()
check("no scanner on the usual driver: the next driver is asked", [(d["name"], d["driver"], d["id"]) for d in devs] == [("Canon DR-C225 TWAIN", "twain", None)]
      and [l for l in log() if "--listdevices" in l] == ["--listdevices --driver wia", "--listdevices --driver twain"], f"{devs} {log()}")
r = rs.scan_pages(w, {"format": "a"}, "auto", out)
check("and the scan goes through that driver, the scanner called by its name", r["error"] is None and len(r["files"]) == 2
      and any("--driver twain --device Canon DR-C225 TWAIN --source feeder" in l for l in log()), f"{r} {log()}")
known = {"format": "a", "device": r.get("device")}
for page, words in (("cp850", "as a French Windows console writes"), ("cp1252", "as a French Windows program writes"), ("utf-8", "in UTF-8")):
    scanner(glass="contrat.jpg", devices="twain:0\tCanon\tDR-C225 TWAIN\tscanner\n")
    open(SC + "/speaks", "w").write("fr " + page)
    r = rs.scan_pages(w, known, "auto", out)
    check(f"NAPS2 says in French that the feeder is empty ({words}): understood, the glass is taken", r["error"] is None and r["source"] == "glass", str(r))
scanner(devices="twain:0\tCanon\tDR-C225 TWAIN\tscanner\n", flags=["offline"])
open(SC + "/speaks", "w").write("fr cp850")
r = rs.scan_pages(w, known, "feeder", out)
check("« Le scanner sélectionné est éteint. » → the scanner is not answering", r["error"] == "offline" and rs.error_text(r["error"]).startswith("the scanner is not answering"), str(r))
check("every language NAPS2 speaks is known", len(rs.NAPS2_WORDS) > 400 and rs.error_of("In der Zuführung sind keine Seiten.") == "empty" and rs.error_in("Не найден выбранный сканер.".encode("cp866"))[0] == "notfound"
      and rs.error_of("Scanned page 3.") is None and rs.error_of("Exporting image 1 of 2...") is None, str(rs.error_in("Не найден выбранный сканер.".encode("cp866"))))
os.environ["READERS_SCANNER_DRIVER"] = "sane"

# --- a PDF's pages as pictures -----------------------------------------------------------------
rs.write_pdf([PAGES + "/facture-1.jpg", PAGES + "/facture-2.jpg", PAGES + "/contrat.jpg"], TMP + "/three.pdf", "three")
pics = rs.pdf_pictures(TMP + "/three.pdf", TMP + "/pics", "p", 150)
check("the pages of a PDF as pictures, in order, at the size asked", len(pics) == 3 and all(Image.open(p).size[0] in (1240, 1241) and Image.open(p).size[1] in (1754, 1755) for p in pics)
      and abs(float(np.asarray(Image.open(pics[2]).convert("L")).mean()) - float(np.asarray(Image.open(PAGES + "/contrat.jpg").convert("L")).mean())) < 2, str([Image.open(p).size for p in pics]))
one = rs.pdf_pictures(TMP + "/three.pdf", TMP + "/pics", "q", 100, 2, 2)
check("one page alone", len(one) == 1 and Image.open(one[0]).size[0] in range(824, 830), str(one))

# --- reading -------------------------------------------------------------------------------
reader = rs.Reader(rs.DATA_DIR)
turn = rs.upright_rotations([PAGES + "/upside-down.jpg", PAGES + "/facture-1.jpg", PAGES + "/blank.jpg"], reader)
check("a sheet fed upside down is set upright, the others left alone", turn == {PAGES + "/upside-down.jpg": 180}, str(turn))

store = rs.Store(rs.DATA_DIR + "/scans")
done = []
q = rs.ReadQueue(store, reader, on_done=done.append)


def file_doc(pages, lang="fra", name=None, folder="", look="original"):
    doc_id = rs.new_id()
    os.makedirs(store.dir(doc_id))
    ps = []
    for f in pages:
        pid = rs.new_id()[:8]
        shutil.copyfile(f"{PAGES}/{f}", store.src_file(doc_id, pid))
        ps.append({"id": pid, "rotation": 180 if f == "upside-down.jpg" else 0, "look": look})
    store.put({"id": doc_id, "created": rs.now_ms(), "modified": rs.now_ms(), "name": name, "named": bool(name), "folder": folder, "lang": lang,
               "pages": ps, "ocr": rs.PENDING, "rev": 0, "readBy": "", "remote": False, "pageCount": 0})
    q.enqueue(doc_id)
    return doc_id


def wait(doc_id, seconds=120):
    t0 = time.time()
    while doc_id not in done and time.time() - t0 < seconds:
        time.sleep(0.2)
    return store.get(doc_id), time.time() - t0


a = file_doc(["facture-1.jpg", "facture-2.jpg"])
d, took = wait(a)
text = store.text(a)
check(f"two pages read ({took:.1f} s), named after their first words", d["ocr"] == rs.DONE and d["name"] == "Facture d'électricité" and d["named"] is False, str(d.get("name")))
check("the text of each page, the accents right", len(text) == 2 and "106,37 CHF" in text[0] and "décompte" in text[0] and "salutations" in text[1], str(text)[:200])
check("the most accurate model is used by itself: figures and small words right", d["readBy"] == "tesseract-best" and "TVA 8,1 % : 7,97 CHF" in text[0] and "412 kWh" in text[0] and "1er juillet" in text[0], text[0])
pdf = store.pdf_file(a)
info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
check("the PDF: two A4 pages", "Pages:           2" in info and "595" in info and "841" in info.replace("842", "841"), info)
layer = subprocess.run(["pdftotext", pdf, "-"], capture_output=True, text=True).stdout
check("the PDF can be searched (text layer)", "Total à payer" in layer and "salutations" in layer)
check("the PDF holds the scans as they are (no re-compression)", abs(os.path.getsize(pdf) - sum(os.path.getsize(f"{PAGES}/{f}") for f in ("facture-1.jpg", "facture-2.jpg"))) < 60_000,
      str(os.path.getsize(pdf)))
check("copied from the PDF, the lines come in reading order", layer.index("Facture") < layer.index("Madame") < layer.index("Consommation totale") < layer.index("Total à payer") < layer.index("salutations")
      and "Montant hors taxes : 98,40 CHF" in layer, layer[:300])
check("the PDF carries the document's title", "Facture" in info and rs.stamp_of(d["created"]) in info, info[:200])
boxes = subprocess.run(["pdftotext", "-bbox", "-f", "1", "-l", "1", pdf, "-"], capture_output=True, text=True).stdout
import re as _re
m = _re.search(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">Facture<', boxes)
# the title was drawn at x 230, y 260…370 px of 2480×3508 at 300 dpi → 55 pt, 62…89 pt
check("a word found in the PDF lies where it is printed on the page", bool(m) and abs(float(m.group(1)) - 55) < 6 and 50 < float(m.group(2)) < 75 and 80 < float(m.group(4)) < 100, str(m and m.groups()))

b = file_doc(["vertrag.jpg"], lang="deu", name="Mietvertrag Zürich")
d, took = wait(b, 240)
ok = d["ocr"] == rs.DONE
check(f"German, read with the most accurate model ({took:.1f} s), the user's name kept", ok and d["name"] == "Mietvertrag Zürich" and d["readBy"] == "tesseract-best"
      and "Nebenkosten" in store.text(b)[0], str(d) + str(q.errors))

ru = file_doc(["facture-1.jpg"], lang="rus")
d, _t = wait(ru, 240)
rs.write_pdf([PAGES + "/blank.jpg"], TMP + "/ru.pdf", "Договор", [[[("Договор", 300, 300, 700, 380), ("аренды", 720, 300, 1100, 380)]]])
check("any alphabet in the PDF's text and title (Cyrillic)", "Договор аренды" in subprocess.run(["pdftotext", TMP + "/ru.pdf", "-"], capture_output=True, text=True).stdout
      and "Договор" in subprocess.run(["pdfinfo", TMP + "/ru.pdf"], capture_output=True, text=True).stdout)
store.delete(ru)

c = file_doc(["upside-down.jpg"], look="clean")
d, _t = wait(c)
check("turned and cleaned before reading", d["ocr"] == rs.DONE and "Note de frais" in store.text(c)[0] and d["name"] == "Note de frais", str(store.text(c)))

e = file_doc(["blank.jpg"])
d, _t = wait(e)
check("a page without text: a document all the same, named by its date", d["ocr"] == rs.DONE and d["name"] is None and store.has_pdf(e) and rs.title_of(d) == rs.stamp_of(d["created"]))

store.read_again(a, "eng")
done.remove(a); q.enqueue(a)
d, _t = wait(a)
check("read again in another language", d["ocr"] == rs.DONE and d["lang"] == "eng" and d["rev"] == 1 and store.has_pdf(a))

reader.prefer_best = False
g = file_doc(["contrat.jpg"])
d, _t = wait(g)
check("the accurate models switched off: the system's model reads", d["ocr"] == rs.DONE and d["readBy"] == "tesseract-fast" and d["name"] == "Contrat de bail", str(d))
store.delete(g)
reader.prefer_best = True

hits = store.search("decompte")
check("search: no accents needed, a snippet shown", len(hits) == 1 and hits[0][0]["id"] == a and "décompte" in (hits[0][1] or ""), str(hits)[:200])
check("search in names", [d["id"] for d, s in store.search("zurich")] == [b])

own = os.environ.get("READERS_SCANNER_TESSERACT")
os.environ["READERS_SCANNER_TESSERACT"] = "/nonexistent/tesseract"
f = file_doc(["contrat.jpg"])
d, _t = wait(f)
check("without Tesseract: the pages kept as a PDF, the reason said", d["ocr"] == rs.FAILED and store.has_pdf(f) and "Tesseract" in q.errors.get(f, ""), str(q.errors))
del os.environ["READERS_SCANNER_TESSERACT"]
if own:
    os.environ["READERS_SCANNER_TESSERACT"] = own

store2 = rs.Store(rs.DATA_DIR + "/scans")
check("everything is on disk: a second start finds the same documents", {d["id"] for d in store2.all()} == {a, b, c, e, f} and store2.text(a) == store.text(a))

print()
print("FAILED: " + ", ".join(failed) if failed else "all engine tests passed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
