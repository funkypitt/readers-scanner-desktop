#!/usr/bin/env python3
"""Two computers and one WebDAV folder: what is scanned on one arrives on the other, renamed,
moved and deleted alike; the PDF comes down only when asked. Needs wsgidav (pip install wsgidav
cheroot, or WSGIDAV=/path/to/wsgidav). Run: python3 tests/test_sync.py"""
import glob, json, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
PY = sys.executable.replace("\\", "/")
TMP = os.path.realpath(tempfile.mkdtemp(prefix="rs-sync-")).replace("\\", "/")
os.environ.update(READERS_SCANNER_HOME=TMP, READERS_SCANNER_DRIVER="sane", READERS_SCANNER_DIRECT="", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import faulthandler
faulthandler.enable()          # a crash in Qt says where it happened
import readers_scanner as rs

WSGIDAV = os.environ.get("WSGIDAV") or shutil.which("wsgidav")
if not WSGIDAV:
    sys.exit("wsgidav not found: pip install wsgidav cheroot, or set WSGIDAV")
PAGES = TMP + "/pages"
subprocess.run([PY, HERE + "/make_pages.py", PAGES], check=True)
failed = []


def check(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(label)


with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
ROOT = TMP + "/dav"
os.makedirs(ROOT)
with open(TMP + "/dav.yaml", "w") as f:
    f.write(f'host: 127.0.0.1\nport: {PORT}\nprovider_mapping:\n  "/": "{ROOT}"\nhttp_authenticator:\n  domain_controller: null\n  accept_basic: true\n'
            '  accept_digest: false\n  default_to_digest: false\nsimple_dc:\n  user_mapping:\n    "*":\n      "test":\n        password: "x"\nverbose: 1\n'
            'logging:\n  enable: false\n')
server = subprocess.Popen([WSGIDAV, "--config", TMP + "/dav.yaml"], stdout=open(TMP + "/dav.log", "w"), stderr=subprocess.STDOUT)
for _i in range(100):
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.2).close()
        break
    except OSError:
        time.sleep(0.1)
CFG = {"server": f"http://127.0.0.1:{PORT}", "folder": "Documents/Scans", "username": "test", "password": "x"}
SCANS = ROOT + "/Documents/Scans"


class Computer:
    def __init__(self, name):
        self.store = rs.Store(f"{TMP}/{name}")
        self.done = []
        self.queue = rs.ReadQueue(self.store, rs.Reader(f"{TMP}/models"), on_done=self.done.append)

    def scan(self, pages, name=None, folder="", created=None, read=True):
        doc_id = rs.new_id()
        os.makedirs(self.store.dir(doc_id))
        ps = []
        for f in pages:
            pid = rs.new_id()[:8]
            shutil.copyfile(f"{PAGES}/{f}", self.store.src_file(doc_id, pid))
            ps.append({"id": pid, "rotation": 0, "look": "original"})
        created = created or rs.now_ms()
        self.store.put({"id": doc_id, "created": created, "modified": created, "name": name, "named": bool(name), "folder": folder, "lang": "fra",
                        "pages": ps, "ocr": rs.PENDING, "rev": 0, "readBy": "", "remote": False, "pageCount": 0})
        if read:
            self.queue.enqueue(doc_id)
            t0 = time.time()
            while doc_id not in self.done and time.time() - t0 < 240:
                time.sleep(0.1)
        return doc_id

    def ensure_pdf(self, d):
        return self.store.pdf_file(d["id"]) if self.store.has_pdf(d["id"]) else None

    def sync(self, cfg=CFG):
        return rs.sync_run(self.store, cfg, self.ensure_pdf, timeout=20)


def there():
    found = (os.path.relpath(p, SCANS).replace("\\", "/") for p in glob.glob(SCANS + "/**", recursive=True) if os.path.isfile(p))
    return sorted(f for f in found if not f.startswith(".readers-scanner/"))


def described():
    return {os.path.basename(p)[:-5]: json.load(open(p, encoding="utf-8")) for p in glob.glob(SCANS + "/.readers-scanner/*.json")}


def pdf_text(path):
    return subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True).stdout


try:
    A, B = Computer("a"), Computer("b")
    A.store.add_folder("Factures")
    a1 = A.scan(["facture-1.jpg", "facture-2.jpg"], folder="Factures")
    stamp = rs.stamp_of(A.store.get(a1)["created"])
    r = A.sync()
    check("a scan nobody named goes up under its date, in its folder", r == (1, 0, 0) and there() == [f"Factures/{stamp}.pdf"], f"{r} {there()}")
    m = described().get(a1) or {}
    check("with its description: text of both pages, the name not chosen by hand", m.get("format") == "readers-scanner" and m.get("pages") == 2 and len(m.get("text", [])) == 2
          and "106,37 CHF" in m["text"][0] and m.get("named") is False and m.get("folder") == "Factures" and m.get("pdf") == there()[0], str(m)[:300])
    check("nothing changed, nothing sent", A.sync() == (0, 0, 0))

    r = B.sync()
    d = B.store.get(a1)
    check("the other computer receives it: name, folder, number of pages", r == (0, 1, 0) and d and d["remote"] and d["name"] is None and rs.title_of(d) == stamp and d["folder"] == "Factures"
          and rs.Store.page_count(d) == 2 and "Factures" in B.store.folder_names(), f"{r} {d}")
    check("found by its words before its PDF has come down", [x[0]["id"] for x in B.store.search("consommation DECOMPTE electricite")] == [a1] and not B.store.has_pdf(a1))
    got = rs.fetch_pdf(B.store, CFG, d)
    check("the PDF comes down when the document is opened, its text inside", bool(got) and "Total à payer" in pdf_text(got) and os.path.getsize(got) == os.path.getsize(A.store.pdf_file(a1)))
    check("receiving is not a change: nothing goes back up", B.sync() == (0, 0, 0) and A.sync() == (0, 0, 0))

    # renamed and moved on the other computer
    before = os.stat(f"{SCANS}/{there()[0]}").st_ino
    B.store.add_folder("Contrats")
    B.store.rename(a1, "SIL été 2026")
    B.store.move(a1, "Contrats")
    r = B.sync()
    check("renamed and moved there: the file moves on the server, it is not sent again", r == (1, 0, 0) and there() == [f"Contrats/{stamp} SIL été 2026.pdf"]
          and os.stat(f"{SCANS}/{there()[0]}").st_ino == before, f"{r} {there()}")
    r = A.sync()
    d = A.store.get(a1)
    check("the computer that scanned it follows: name, folder; its pages untouched", r == (0, 1, 0) and d["name"] == "SIL été 2026" and d["named"] and d["folder"] == "Contrats"
          and not d["remote"] and len(d["pages"]) == 2 and "Contrats" in A.store.folder_names(), f"{r} {d}")
    check("and quiet again", A.sync() == (0, 0, 0) and B.sync() == (0, 0, 0))

    # both rename between two syncs: the latest wins
    A.store.rename(a1, "Électricité (ancien nom)")
    time.sleep(0.05)
    B.store.rename(a1, "Électricité juillet-août")
    B.sync()
    A.sync()
    B.sync()
    check("renamed on both sides: the latest wins, on both", A.store.get(a1)["name"] == B.store.get(a1)["name"] == "Électricité juillet-août"
          and there() == [f"Contrats/{stamp} Électricité juillet-août.pdf"], f"{A.store.get(a1)['name']} / {B.store.get(a1)['name']} / {there()}")
    B.store.rename(a1, "Perdant")
    time.sleep(0.05)
    A.store.rename(a1, "Électricité SIL")
    B.sync()
    A.sync()
    B.sync()
    check("the same the other way round (the first to sync is not the winner)", A.store.get(a1)["name"] == B.store.get(a1)["name"] == "Électricité SIL"
          and there() == [f"Contrats/{stamp} Électricité SIL.pdf"], f"{A.store.get(a1)['name']} / {B.store.get(a1)['name']} / {there()}")

    # pages changed where it was scanned
    d = A.store.get(a1)
    d["pages"] = d["pages"][:1]
    d["modified"] = rs.now_ms()
    A.store.put(d)
    A.store.read_again(a1)
    A.done.clear()
    A.queue.enqueue(a1)
    t0 = time.time()
    while a1 not in A.done and time.time() - t0 < 120:
        time.sleep(0.1)
    r = A.sync()
    size = os.path.getsize(f"{SCANS}/{there()[0]}")
    check("a page removed: the PDF is sent again, one page", r == (1, 0, 0) and "Pages:           1" in subprocess.run(["pdfinfo", f"{SCANS}/{there()[0]}"], capture_output=True, text=True).stdout, str(r))
    r = B.sync()
    d = B.store.get(a1)
    check("the other computer forgets the old PDF and fetches the new one when asked", r == (0, 1, 0) and not B.store.has_pdf(a1) and rs.Store.page_count(d) == 1
          and os.path.getsize(rs.fetch_pdf(B.store, CFG, d)) == size, f"{r}")

    # same minute, same name
    t = rs.now_ms()
    b1 = B.scan(["contrat.jpg"], name="Bail", created=t)
    b2 = B.scan(["contrat.jpg"], name="Bail", created=t + 1)
    B.sync()
    s2 = rs.stamp_of(t)
    check("two documents of the same minute and name: both kept", f"{s2} Bail.pdf" in there() and f"{s2} Bail (2).pdf" in there(), str(there()))
    u1 = B.scan(["contrat.jpg"], created=t + 2)
    u2 = B.scan(["contrat.jpg"], created=t + 3)
    B.sync()
    check("two documents of the same minute that nobody named: both kept", f"{s2}.pdf" in there() and f"{s2} (2).pdf" in there(), str(there()))
    B.store.delete(u1), B.store.delete(u2)
    B.sync()
    check("and the numbering does not drift at the next syncs", B.sync() == (0, 0, 0) and A.sync() == (0, 2, 0) and A.sync() == (0, 0, 0) and len(there()) == 3, str(there()))

    # someone's own file in the folder
    shutil.copyfile(A.store.pdf_file(a1), SCANS + "/Contrats/mon fichier à moi.pdf")
    A.sync(), B.sync()
    check("a PDF put there by hand is left alone, and is not taken for a scan", os.path.exists(SCANS + "/Contrats/mon fichier à moi.pdf") and A.store.count() == 3 and B.store.count() == 3)

    # not read yet
    a2 = A.scan(["vertrag.jpg"], read=False)
    check("a document still being read waits", A.sync() == (0, 0, 0) and a2 not in described())
    A.store.delete(a2)

    # deleted
    B.store.delete(b2)
    r = B.sync()
    check("deleted there: gone from the server, file and description", r == (0, 0, 1) and f"{s2} Bail (2).pdf" not in there() and b2 not in described(), f"{r} {there()}")
    r = A.sync()
    check("and gone here at the next sync", r == (0, 0, 1) and A.store.get(b2) is None, str(r))
    A.store.delete(b1)                # a document received, deleted by the one who received it
    r = A.sync()
    check("a received document deleted here goes from the server too", r == (0, 0, 1) and f"{s2} Bail.pdf" not in there() and B.sync() == (0, 0, 1) and B.store.get(b1) is None, f"{r} {there()}")

    # deleted here, changed there meanwhile: it comes back rather than being lost
    c1 = A.scan(["contrat.jpg"], name="Contrat de bail")
    A.sync(), B.sync()
    A.store.delete(c1)
    B.store.rename(c1, "Contrat de bail 2026")
    B.sync()
    r = A.sync()
    check("deleted here but changed there since: kept (it comes back)", r == (0, 1, 0) and A.store.get(c1)["name"] == "Contrat de bail 2026" and A.store.get(c1)["remote"], str(r))

    # folders
    A.store.add_folder("Impôts 2026")
    A.sync(), B.sync()
    check("a new folder, even empty, exists on the other computer", "Impôts 2026" in B.store.folder_names() and os.path.isdir(SCANS + "/Impôts 2026"))
    B.store.delete_folder("Impôts 2026")
    B.sync(), A.sync()
    check("deleted there: gone from the server and from here", not os.path.isdir(SCANS + "/Impôts 2026") and "Impôts 2026" not in A.store.folder_names() and "Impôts 2026" not in B.store.folder_names(),
          f"{A.store.folder_names()} {B.store.folder_names()}")
    A.store.rename_folder("Contrats", "Contrats & baux")
    A.sync(), B.sync(), A.sync()
    d = B.store.get(a1)
    check("a folder renamed: its documents follow, the hand-put file stays where it was", d["folder"] == "Contrats & baux" and f"Contrats & baux/{stamp} Électricité SIL.pdf" in there()
          and "Contrats/mon fichier à moi.pdf" in there(), f"{d['folder']} {there()}")
    B.store.delete_folder("Contrats & baux")
    B.sync(), A.sync(), B.sync()
    check("a folder deleted: its documents stay, in « all scans »", A.store.get(a1) is not None and A.store.get(a1)["folder"] == "" and f"{stamp} Électricité SIL.pdf" in there()
          and not os.path.isdir(SCANS + "/Contrats & baux"), f"{A.store.get(a1)} {there()}")

    # a third computer, later
    C = Computer("c")
    r = C.sync()
    check("a computer that joins later receives everything, and sends nothing", r[0] == 0 and r[2] == 0 and {d["id"] for d in C.store.all()} == {d["id"] for d in A.store.all()} == {d["id"] for d in B.store.all()}
          and C.sync() == (0, 0, 0), str(r))

    # the folder on this computer: the PDFs as plain files, named and filed as on the server
    FILES = TMP + "/c-files"
    M = rs.Mirror(C.store)

    def here():
        return sorted(os.path.relpath(os.path.join(r, f), FILES).replace("\\", "/") for r, _d, fs in os.walk(FILES) for f in fs)

    def scans():           # the server's, without the file someone put there by hand
        return [f for f in there() if f != "Contrats/mon fichier à moi.pdf"]

    check("the folder on this computer: nothing in it until a PDF is here", M.run(FILES) == 0 and here() == [])
    n = rs.fetch_missing(C.store, CFG)
    check("every PDF from elsewhere comes down, and the folder is the server's", n == len(scans()) and M.run(FILES) == n and here() == scans(), f"{n} {here()} {scans()}")
    check("nothing changed: nothing written again", M.run(FILES) == 0)
    c1 = C.scan(["facture-1.jpg"], name="Garage", folder="Factures")
    M.run(FILES)
    mine = [f for f in here() if f.endswith(" Garage.pdf")]
    check("scanned here: in the folder as soon as it is read, before any sync", len(mine) == 1 and mine[0].startswith("Factures/") and "106,37 CHF" in pdf_text(FILES + "/" + mine[0]), str(here()))
    C.sync(); M.run(FILES)
    check("and after the sync the folder is still the server's", here() == scans(), f"{here()} {scans()}")
    with open(FILES + "/Factures/notes à moi.pdf", "w") as f:
        f.write("mine")
    C.store.rename(c1, "Garage Dupont"); C.store.move(a1, C.store.add_folder("Maison"))
    M.run(FILES)
    check("renamed and moved in the app: the files follow, before the sync", any(f.endswith(" Garage Dupont.pdf") for f in here()) and not any(f.endswith(" Garage.pdf") for f in here())
          and any(f.startswith("Maison/") for f in here()), str(here()))
    C.sync(); M.run(FILES)
    check("and after it: the server's again, someone's file left alone", [f for f in here() if f != "Factures/notes à moi.pdf"] == scans() and "Factures/notes à moi.pdf" in here(), f"{here()} {scans()}")
    edited = FILES + "/" + [f for f in here() if f.endswith(" Garage Dupont.pdf")][0]
    os.remove(edited)
    with open(edited, "w") as f:
        f.write("annotated by hand")
    M.run(FILES)
    check("a file changed by hand is left as it is", open(edited).read() == "annotated by hand")
    C.store.delete(c1); M.run(FILES)
    check("and stays when its document is deleted; the others' files go with theirs", os.path.exists(edited))
    os.remove(edited)
    C.sync(); A.sync()
    A.store.delete(a1); A.sync(); C.sync(); M.run(FILES)
    check("deleted on another computer: its file goes here", [f for f in here() if f != "Factures/notes à moi.pdf"] == scans() and not any(f.startswith("Maison/") for f in here()), f"{here()} {scans()}")
    M.run(TMP + "/c-other")
    check("another folder chosen: the files are there, the old one keeps only someone's file", here() == ["Factures/notes à moi.pdf"]
          and sorted(f for _r, _d, fs in os.walk(TMP + "/c-other") for f in fs) == sorted(os.path.basename(f) for f in scans()), str(here()))
    M.run("")
    check("no folder: the files stay where they are, the app forgets them", len([f for _r, _d, fs in os.walk(TMP + "/c-other") for f in fs]) == len(scans()) and M.run("") == 0)
    check("the same folder taken again: its files are recognised, none twice", (M.run(TMP + "/c-other"), M.run(TMP + "/c-other"))[1] == 0 and len([f for _r, _d, fs in os.walk(TMP + "/c-other") for f in fs]) == len(scans()))
    B.sync()

    # a description nobody can read
    with open(SCANS + "/.readers-scanner/zzz.json", "w") as f:
        f.write("{ not json")
    with open(SCANS + "/.readers-scanner/future.json", "w") as f:
        json.dump({"format": "readers-scanner", "version": 99, "id": "future"}, f)
    check("descriptions that cannot be read are skipped, not fatal", A.sync() == (0, 0, 0) and os.path.exists(SCANS + "/.readers-scanner/zzz.json"))

    # what goes wrong
    try:
        A.sync(dict(CFG, password="wrong"))
        check("wrong password: said in words", False)
    except rs.WebDavError as e:
        check("wrong password: said in words", str(e) == "wrong username or password", str(e))
    before = (A.store.count(), there())
    server.terminate()
    server.wait()
    try:
        A.sync()
        check("server unreachable: said in words, nothing lost", False)
    except rs.WebDavError as e:
        check("server unreachable: said in words, nothing lost", str(e) == "cannot reach the server" and (A.store.count(), there()) == before, str(e))
finally:
    server.terminate()

print()
if failed:
    print("FAILED: " + ", ".join(failed))
else:
    print("all sync tests passed")
sys.stdout.flush()
os._exit(1 if failed else 0)
