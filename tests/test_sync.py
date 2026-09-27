#!/usr/bin/env python3
"""Two computers and one WebDAV folder: what is scanned on one arrives on the other, renamed,
moved and deleted alike; the PDF comes down only when asked. Needs wsgidav (pip install wsgidav
cheroot, or WSGIDAV=/path/to/wsgidav). Run: python3 tests/test_sync.py"""
import glob, json, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
PY = sys.executable.replace("\\", "/")
TMP = os.path.realpath(tempfile.mkdtemp(prefix="rs-sync-")).replace("\\", "/")
os.environ.update(READERS_SCANNER_HOME=TMP, READERS_SCANNER_DRIVER="sane", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
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
    return sorted(os.path.relpath(p, SCANS) for p in glob.glob(SCANS + "/**", recursive=True) if os.path.isfile(p) and "/.readers-scanner/" not in p)


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
    check("a scan goes up: dated, named after its first words, in its folder", r == (1, 0, 0) and there() == [f"Factures/{stamp} Facture d'électricité.pdf"], f"{r} {there()}")
    m = described().get(a1) or {}
    check("with its description: text of both pages, the name not chosen by hand", m.get("format") == "readers-scanner" and m.get("pages") == 2 and len(m.get("text", [])) == 2
          and "106,37 CHF" in m["text"][0] and m.get("named") is False and m.get("folder") == "Factures" and m.get("pdf") == there()[0], str(m)[:300])
    check("nothing changed, nothing sent", A.sync() == (0, 0, 0))

    r = B.sync()
    d = B.store.get(a1)
    check("the other computer receives it: name, folder, number of pages", r == (0, 1, 0) and d and d["remote"] and d["name"] == "Facture d'électricité" and d["folder"] == "Factures"
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
