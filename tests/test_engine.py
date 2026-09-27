#!/usr/bin/env python3
"""The engine without a window: names, looks, blank pages, the scan's smart defaults (with the
stand-in for NAPS2), reading (the real Tesseract), the PDF. Run: python3 tests/test_engine.py"""
import os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="rs-test-")
os.environ.update(XDG_CONFIG_HOME=TMP + "/config", XDG_DATA_HOME=TMP + "/data", FAKE_SCANNER=TMP + "/scanner", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8",
                  READERS_SCANNER_NAPS2=f"{sys.executable} {HERE}/fake_naps2.py", READERS_SCANNER_SCANIMAGE=f"{sys.executable} {HERE}/fake_scanimage.py")
sys.path.insert(0, os.path.dirname(HERE))
import readers_scanner as rs

PAGES = TMP + "/pages"
subprocess.run([sys.executable, HERE + "/make_pages.py", PAGES], check=True, capture_output=True)
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
check("three ways to one scanner, driverless first", [d["backend"] for d in devs] == ["airscan", "escl", "hpaio"] and len({d["key"] for d in devs}) == 1, str(devs))
cfg = {"format": "a"}
out = TMP + "/incoming"

scanner(feeder=["facture-1.jpg", "facture-2.jpg"], glass="contrat.jpg")
seen = []
r = rs.scan_pages(n, cfg, "auto", out, on_page=seen.append)
check("automatic: paper in the feeder → the feeder, both pages", r["error"] is None and r["source"] == "feeder" and len(r["files"]) == 2 and seen == [1, 2], str(r))
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

scanner(feeder=["facture-1.jpg"], flags=["offline-airscan"])
r = rs.scan_pages(n, cfg, "auto", out)
check("one driver does not answer → the next one, remembered first", r["error"] is None and r["device"]["routes"][0]["backend"] == "escl", str(r.get("device")))

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
check("NAPS2 absent: known, nothing crashes", missing.version is None and rs.scan_pages(missing, cfg, "auto", out)["error"] in ("driver", "unknown", "offline", "nonaps2"))
os.environ["READERS_SCANNER_NAPS2"] = f"{sys.executable} {HERE}/fake_naps2.py"

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
check(f"German, which the system does not have ({took:.1f} s), the user's name kept", ok and d["name"] == "Mietvertrag Zürich" and d["readBy"] == "tesseract-best"
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

os.environ["READERS_SCANNER_TESSERACT"] = "/nonexistent/tesseract"
f = file_doc(["contrat.jpg"])
d, _t = wait(f)
check("without Tesseract: the pages kept as a PDF, the reason said", d["ocr"] == rs.FAILED and store.has_pdf(f) and "Tesseract" in q.errors.get(f, ""), str(q.errors))
del os.environ["READERS_SCANNER_TESSERACT"]

store2 = rs.Store(rs.DATA_DIR + "/scans")
check("everything is on disk: a second start finds the same documents", {d["id"] for d in store2.all()} == {a, b, c, e, f} and store2.text(a) == store.text(a))

print()
print("FAILED: " + ", ".join(failed) if failed else "all engine tests passed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
