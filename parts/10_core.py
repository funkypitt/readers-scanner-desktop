
def _(key, *args):
    s = _TR.get(_LANG, {}).get(key, key)
    for i, a in enumerate(args):
        s = s.replace("%" + str(i + 1), str(a))
    return s


# ------------------------------------------------------------------------------------------
# Names: the phone's rules, so both sides call a document and its file alike
# ------------------------------------------------------------------------------------------

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WORD = re.compile(r"[^\W_][^\W_'’.\-]*(?:['’.\-]+[^\W_]+)*")


def folder_name_of(name):
    """A folder name the server and the phone both accept; None when nothing is left."""
    n = re.sub(r"\s+", " ", _BAD.sub(" ", name)).strip().strip(".").strip()
    return n[:60] or None


def stamp_of(created_ms):
    return datetime.fromtimestamp(created_ms / 1000).strftime("%Y-%m-%d %Hh%M")


def title_of(doc):
    """"2026-09-24 11h32" followed by the name, when there is one."""
    return stamp_of(doc["created"]) + (" " + doc["name"] if doc.get("name") else "")


def file_name_of(doc, ext="pdf"):
    return re.sub(r"\s+", " ", _BAD.sub(" ", title_of(doc))).strip()[:120] + "." + ext


def _is_word(t):
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    return (letters >= 2 and letters * 10 >= len(t) * 6) or (digits >= 2 and letters == 0 and len(t) <= 10)


def first_words(text, most=5, most_chars=40):
    """The name a document gets from its text when the user gave none: the first few real
    words of the page, whole lines until there are three, debris lines skipped."""
    words, full = [], False
    for line in text.splitlines():
        tokens = [m.group(0).strip(".-'’") for m in _WORD.finditer(line)]
        tokens = [t for t in tokens if t]
        good = [t for t in tokens if _is_word(t)]
        if not good or len(good) * 2 < len(tokens):
            if words:
                break
            continue
        for t in good:
            if len(words) >= most or len(" ".join(words)) + len(t) + 1 > most_chars:
                full = True
                break
            words.append(t)
        if full or len(words) >= 3:
            break
    while len(words) > 1 and len(words[-1]) <= 3 and words[-1].islower():
        words.pop()
    return " ".join(words) or None


def fold(s):
    """Lower case, accents off: how search compares."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def reflow(text):
    """The text as read, the paper's line breaks taken out: a line as long as the page is wide
    goes on with the next one; short lines stay (addresses, amounts); blank lines still part
    paragraphs. The phone's rule."""
    widest = max((len(l.strip()) for l in text.splitlines()), default=0)
    out = []
    for para in re.split(r"\n\s*\n", text):
        lines = [l.strip() for l in para.splitlines() if l.strip()]
        buf = ""
        for i, line in enumerate(lines):
            buf += line
            if i == len(lines) - 1:
                break
            nxt = lines[i + 1]
            long = len(line) >= widest * 0.72
            full = long and (not line.endswith((":", ".")) or nxt[:1].islower())
            if full and line.endswith("-") and nxt[:1].islower():
                buf = buf[:-1]
            elif full:
                buf += " "
            else:
                buf += "\n"
        out.append(buf)
    return "\n\n".join(out).strip()


def new_id():
    return uuid.uuid4().hex[:12]


def now_ms():
    return int(time.time() * 1000)


def when_label(millis):
    d = datetime.fromtimestamp(millis / 1000)
    today = date.today()
    if d.date() == today:
        return _("today") + " " + d.strftime("%H:%M")
    if d.date() == today - timedelta(days=1):
        return _("yesterday") + " " + d.strftime("%H:%M")
    return f"{d.day} {d.strftime('%b' if d.year == today.year else '%b %Y')}".lower() + " " + d.strftime("%H:%M")


# ------------------------------------------------------------------------------------------
# Documents on disk. A document scanned here keeps its pages (the scan as it came, and the
# page as shown); one from another device has only its description and text, and its PDF once
# it has been opened.
#
#   index.json                 documents, folders, folders deleted here
#   docs/<id>/<page>.src.jpg   the scan            docs/<id>/<page>.jpg   the page as shown
#   docs/<id>/doc.pdf          the searchable PDF  docs/<id>/text.json    the text of each page
# ------------------------------------------------------------------------------------------

LOOKS = ("original", "clean", "grey", "bw")
PENDING, DONE, FAILED = "PENDING", "DONE", "FAILED"


class Store:
    """Thread-safe: reading the text, sync and downloads run off the UI thread. `on_change`
    is called after every change."""

    def __init__(self, root):
        self.root = root
        self.docs_dir = os.path.join(root, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)
        self.index_file = os.path.join(root, "index.json")
        self.lock = threading.RLock()
        self.on_change = None
        try:
            with open(self.index_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.docs = {d["id"]: d for d in data.get("docs", [])}
        self.folders = data.get("folders", [])          # {"name", "onServer"}
        self.gone_folders = data.get("goneFolders", [])
        self._texts = {}

    def _save(self):
        tmp = self.index_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"docs": list(self.docs.values()), "folders": self.folders, "goneFolders": self.gone_folders}, f, indent=1, ensure_ascii=False)
        replace(tmp, self.index_file)
        if self.on_change:
            self.on_change()

    # ---- files ------------------------------------------------------------------------

    def dir(self, doc_id):
        return os.path.join(self.docs_dir, doc_id)

    def src_file(self, doc_id, page_id):
        return os.path.join(self.dir(doc_id), page_id + ".src.jpg")

    def page_file(self, doc_id, page_id):
        return os.path.join(self.dir(doc_id), page_id + ".jpg")

    def pdf_file(self, doc_id):
        return os.path.join(self.dir(doc_id), "doc.pdf")

    def has_pdf(self, doc_id):
        p = self.pdf_file(doc_id)
        return os.path.exists(p) and os.path.getsize(p) > 0

    def text(self, doc_id):
        """The text read on each page (an empty list until it has been read)."""
        with self.lock:
            if doc_id not in self._texts:
                try:
                    with open(os.path.join(self.dir(doc_id), "text.json"), encoding="utf-8") as f:
                        self._texts[doc_id] = json.load(f)
                except (OSError, ValueError):
                    return []
            return list(self._texts[doc_id])

    def _set_text(self, doc_id, pages):
        os.makedirs(self.dir(doc_id), exist_ok=True)
        path = os.path.join(self.dir(doc_id), "text.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(pages, f, ensure_ascii=False)
        replace(path + ".tmp", path)
        self._texts[doc_id] = list(pages)

    # ---- reading ----------------------------------------------------------------------

    def get(self, doc_id):
        with self.lock:
            d = self.docs.get(doc_id)
            return json.loads(json.dumps(d)) if d else None

    def all(self, folder=None):
        """Newest first; folder None = every document."""
        with self.lock:
            docs = [json.loads(json.dumps(d)) for d in self.docs.values() if folder is None or d.get("folder", "") == folder]
        return sorted(docs, key=lambda d: -d["created"])

    def count(self, folder=None):
        with self.lock:
            return sum(1 for d in self.docs.values() if folder is None or d.get("folder", "") == folder)

    @staticmethod
    def page_count(doc):
        return doc.get("pageCount", 0) if doc.get("remote") else len(doc.get("pages", []))

    def pending(self):
        with self.lock:
            return [d["id"] for d in sorted(self.docs.values(), key=lambda d: d["created"]) if d.get("ocr") == PENDING and not d.get("remote")]

    def search(self, query):
        """Names and text, without case or accents; every word asked must be there, in any
        order: (document, snippet or None)."""
        q = fold(query.strip())
        words = q.split()
        if not words:
            return []
        out = []
        for d in self.all():
            text = "\n".join(self.text(d["id"]))
            folded = fold(text)
            title = fold(title_of(d))
            if not all(w in folded or w in title for w in words):
                continue
            at = folded.find(q)
            n = len(q)
            if at < 0:
                at, n = min(((folded.find(w), len(w)) for w in words if w in folded), default=(-1, 0))
            if at >= 0 and len(folded) == len(text):
                a, b = max(0, at - 40), min(len(text), at + n + 60)
                out.append((d, ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("…" if b < len(text) else "")))
            else:
                out.append((d, None))
        return out

    # ---- folders ----------------------------------------------------------------------

    def folder_names(self):
        with self.lock:
            return sorted((f["name"] for f in self.folders), key=str.lower)

    def folder_by_name(self, name):
        with self.lock:
            return next((f["name"] for f in self.folders if f["name"].lower() == name.lower()), None)

    def add_folder(self, name):
        """The name kept (made safe for a file system); the existing one if taken; None if empty."""
        with self.lock:
            n = folder_name_of(name)
            if not n:
                return None
            have = self.folder_by_name(n)
            if have:
                return have
            self.folders.append({"name": n, "onServer": False})
            self.gone_folders = [g for g in self.gone_folders if g != n]
            self._save()
            return n

    def rename_folder(self, old, name):
        with self.lock:
            n = folder_name_of(name)
            if not n or n == old or any(f["name"].lower() == n.lower() and f["name"] != old for f in self.folders):
                return None
            if any(f["name"] == old and f.get("onServer") for f in self.folders):
                self.gone_folders.append(old)
            self.folders = [f for f in self.folders if f["name"] != old] + [{"name": n, "onServer": False}]
            self.gone_folders = [g for g in self.gone_folders if g != n]
            now = now_ms()
            for d in self.docs.values():
                if d.get("folder", "") == old:
                    d.update(folder=n, modified=now)
            self._save()
            return n

    def delete_folder(self, name):
        """The folder goes; its documents stay, in « all scans »."""
        with self.lock:
            if any(f["name"] == name and f.get("onServer") for f in self.folders):
                self.gone_folders.append(name)
            self.folders = [f for f in self.folders if f["name"] != name]
            now = now_ms()
            for d in self.docs.values():
                if d.get("folder", "") == name:
                    d.update(folder="", modified=now)
            self._save()

    # folder side of the sync
    def folder_on_server(self, name):
        with self.lock:
            have = self.folder_by_name(name)
            if have is None:
                self.folders.append({"name": name, "onServer": True})
                have = name
            else:
                for f in self.folders:
                    if f["name"] == have:
                        f["onServer"] = True
            self._save()
            return have

    def folder_gone_there(self, name):
        with self.lock:
            self.folders = [f for f in self.folders if f["name"] != name]
            for d in self.docs.values():
                if d.get("folder", "") == name:
                    d["folder"] = ""
            self._save()

    def folder_removed_there(self, name):
        with self.lock:
            self.gone_folders = [g for g in self.gone_folders if g != name]
            self._save()

    def folders_state(self):
        with self.lock:
            return [dict(f) for f in self.folders], list(self.gone_folders)

    # ---- writing ----------------------------------------------------------------------

    def put(self, doc):
        """A new or changed document scanned here; its pages' files are already in its folder."""
        with self.lock:
            os.makedirs(self.dir(doc["id"]), exist_ok=True)
            if doc.get("ocr") == PENDING:
                # new pages: the old text and PDF no longer match them
                for name in ("doc.pdf", "text.json"):
                    try:
                        remove(os.path.join(self.dir(doc["id"]), name))
                    except OSError:
                        pass
                self._texts.pop(doc["id"], None)
                shutil.rmtree(os.path.join(self.dir(doc["id"]), "render"), ignore_errors=True)
            keep = {"doc.pdf", "text.json", "render"}
            for p in doc.get("pages", []):
                keep |= {p["id"] + ".jpg", p["id"] + ".src.jpg"}
            for name in os.listdir(self.dir(doc["id"])):
                if name not in keep and not name.startswith("ocr-"):
                    path = os.path.join(self.dir(doc["id"]), name)
                    shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else remove(path)
            self.docs[doc["id"]] = doc
            self._save()

    def update(self, doc_id, **changes):
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None:
                return
            d.update(changes)
            self._save()

    def rename(self, doc_id, name):
        name = (name or "").strip()
        self.update(doc_id, name=name or None, named=bool(name), modified=now_ms())

    def move(self, doc_id, folder):
        self.update(doc_id, folder=folder or "", modified=now_ms())

    def delete(self, doc_id):
        with self.lock:
            self.docs.pop(doc_id, None)
            self._texts.pop(doc_id, None)
            shutil.rmtree(self.dir(doc_id), ignore_errors=True)
            self._save()

    def read_again(self, doc_id, lang=None):
        """Another language, or a better model: the text and the PDF are made again."""
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None or d.get("remote"):
                return
            for name in ("doc.pdf", "text.json"):
                try:
                    remove(os.path.join(self.dir(doc_id), name))
                except OSError:
                    pass
            self._texts.pop(doc_id, None)
            d.update(ocr=PENDING, lang=lang or d["lang"], rev=d.get("rev", 0) + 1, modified=now_ms())
            if not d.get("named"):
                d["name"] = None
            self._save()

    def ocr_done(self, doc_id, rev, pages, pdf, read_by):
        """Called once a revision of the document has been read."""
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None or d.get("rev", 0) != rev:
                return False
            self._set_text(doc_id, pages)
            if pdf:
                replace(pdf, self.pdf_file(doc_id))
            if not d.get("named"):
                d["name"] = first_words(next((p for p in pages if p.strip()), "")) or d.get("name")
            d.update(ocr=DONE, readBy=read_by)
            self._save()
            return True

    def ocr_failed(self, doc_id, rev, pdf=None):
        with self.lock:
            d = self.docs.get(doc_id)
            if d is None or d.get("rev", 0) != rev:
                return
            if pdf:
                replace(pdf, self.pdf_file(doc_id))
            self._set_text(doc_id, [""] * len(d.get("pages", [])))
            d.update(ocr=FAILED, readBy="")
            self._save()

    def put_remote(self, doc, text, drop_pdf):
        """A document described by another device, new here or changed there."""
        with self.lock:
            os.makedirs(self.dir(doc["id"]), exist_ok=True)
            self._set_text(doc["id"], text)
            if drop_pdf:
                try:
                    remove(self.pdf_file(doc["id"]))
                except OSError:
                    pass
                shutil.rmtree(os.path.join(self.dir(doc["id"]), "render"), ignore_errors=True)
            self.docs[doc["id"]] = doc
            self._save()

    def forget_server(self):
        """Another server or folder: every document scanned here goes up again as new; the ones
        from elsewhere (they live on the old server) go."""
        with self.lock:
            for d in list(self.docs.values()):
                if d.get("remote"):
                    self.docs.pop(d["id"])
                    shutil.rmtree(self.dir(d["id"]), ignore_errors=True)
            for f in self.folders:
                f["onServer"] = False
            self.gone_folders = []
            try:
                remove(os.path.join(self.root, "sync.json"))
            except OSError:
                pass
            self._save()


# ------------------------------------------------------------------------------------------
# WebDAV
# ------------------------------------------------------------------------------------------

class WebDavError(Exception):
    pass


def encode_segment(s):
    return quote(s, safe="")


def folder_url(cfg):
    folder = (cfg.get("folder") or "Scans").strip().strip("/") or "Scans"
    return cfg.get("server", "").strip().rstrip("/") + "/" + "/".join(encode_segment(p) for p in folder.split("/")) + "/"


class WebDav:
    def __init__(self, username, password, timeout=30):
        self.s = requests.Session()
        self.s.auth = (username, password)
        self.s.headers["User-Agent"] = f"{APP}-desktop/{VERSION}"
        self.timeout = (min(15, timeout), timeout)

    def _req(self, method, url, body=None, depth=None, headers=None, content_type="text/plain; charset=utf-8", allow=(), stream=False):
        h = dict(headers or {})
        if depth is not None:
            h["Depth"] = str(depth)
        if body is not None:
            h["Content-Type"] = content_type
        try:
            r = self.s.request(method, url, data=body.encode("utf-8") if isinstance(body, str) else body, headers=h, timeout=self.timeout, stream=stream)
        except requests.RequestException:
            raise WebDavError(_("cannot reach the server"))
        if r.status_code == 401:
            raise WebDavError(_("wrong username or password"))
        if r.status_code >= 400 and r.status_code not in allow:
            raise WebDavError(f"{method}: HTTP {r.status_code}")
        return r

    @staticmethod
    def _etag(v):
        if not v:
            return None
        v = v.strip()
        if v.startswith("W/"):
            v = v[2:]
        return v.strip('"') or None

    _PROPS = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/><d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>'

    def list(self, url):
        """What is directly inside the folder (the folder itself excluded)."""
        r = self._req("PROPFIND", url, self._PROPS, depth=1, content_type="application/xml; charset=utf-8")
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            raise WebDavError(_("not a WebDAV folder at this address"))
        here = unquote(urlparse(url).path).rstrip("/")
        out = []
        for resp in root.iter("{DAV:}response"):
            href = resp.findtext("{DAV:}href")
            if not href:
                continue
            path = unquote(urlparse(urljoin(url, href.strip())).path).rstrip("/")
            if path == here:
                continue
            out.append({"name": path.rsplit("/", 1)[-1], "etag": self._etag(resp.findtext(".//{DAV:}getetag")),
                        "dir": resp.find(".//{DAV:}resourcetype/{DAV:}collection") is not None})
        return out

    def etag_of(self, url):
        r = self._req("PROPFIND", url, self._PROPS, depth=0, content_type="application/xml; charset=utf-8", allow=(404,))
        if r.status_code == 404:
            return None
        try:
            return self._etag(ET.fromstring(r.content).findtext(".//{DAV:}getetag"))
        except ET.ParseError:
            return None

    def get_text(self, url):
        r = self._req("GET", url, allow=(404,))
        return None if r.status_code == 404 else r.content.decode("utf-8", "replace")

    def put_text(self, url, text, content_type="application/json; charset=utf-8"):
        r = self._req("PUT", url, text, content_type=content_type)
        return self._etag(r.headers.get("ETag")) or self.etag_of(url)

    def put_file(self, url, path, content_type):
        with open(path, "rb") as f:
            r = self._req("PUT", url, f, content_type=content_type)
        return self._etag(r.headers.get("ETag")) or self.etag_of(url)

    def download(self, url, out, progress=None):
        """False when the server does not have it."""
        r = self._req("GET", url, allow=(404,), stream=True)
        if r.status_code == 404:
            return False
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(out + ".part", "wb") as f:
            for chunk in r.iter_content(64 * 1024):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done * 100 // total)
        replace(out + ".part", out)
        return True

    def move(self, src, dst):
        """Renames a file on the server without sending it again; never overwrites."""
        self._req("MOVE", src, headers={"Destination": dst, "Overwrite": "F"})

    def delete(self, url):
        self._req("DELETE", url, allow=(404,))

    def mkcol(self, url):
        self._req("MKCOL", url, allow=(405, 301))

    def exists(self, url):
        return self._req("PROPFIND", url, depth=0, allow=(404,)).status_code != 404

    def mkdirs(self, url):
        if self.exists(url):
            return
        u = urlparse(url)
        path = ""
        for seg in [s for s in u.path.strip("/").split("/") if s]:
            path += "/" + seg
            at = f"{u.scheme}://{u.netloc}{path}/"
            if not self.exists(at):
                self.mkcol(at)


# ------------------------------------------------------------------------------------------
# Sync: the protocol of readers-scanner/docs/SYNC.md, the same as the phone's sync/Sync.kt.
#
#   Scans/<folder>/<date> <name>.pdf     a folder = a subfolder, one level deep
#   Scans/<date> <name>.pdf              « all scans » only
#   Scans/.readers-scanner/<id>.json     one description per document
# ------------------------------------------------------------------------------------------

META_DIR = ".readers-scanner"
META_FORMAT = "readers-scanner"
_sync_lock = threading.Lock()


def meta_build(doc, pdf, text, pages):
    return json.dumps({"format": META_FORMAT, "version": 1, "id": doc["id"], "created": doc["created"], "modified": doc.get("modified", doc["created"]),
                       "name": doc.get("name") or None, "named": bool(doc.get("named")), "folder": doc.get("folder", ""), "lang": doc.get("lang", "eng"),
                       "pages": pages, "text": list(text), "pdf": pdf, "readBy": doc.get("readBy", ""), "ocr": doc.get("ocr", DONE)},
                      ensure_ascii=False, indent=1)


def meta_parse(s):
    try:
        o = json.loads(s)
        if not isinstance(o, dict) or o.get("format") != META_FORMAT:
            return None
        name = o.get("name")
        return {"id": o["id"], "created": int(o["created"]), "modified": int(o.get("modified", o["created"])),
                "name": name if isinstance(name, str) and name.strip() else None, "named": bool(o.get("named")),
                "folder": o.get("folder") or "", "lang": o.get("lang") or "eng", "pages": int(o.get("pages", 0)),
                "text": [t for t in o.get("text", []) if isinstance(t, str)], "pdf": o["pdf"], "readBy": o.get("readBy") or "",
                "ocr": o.get("ocr") if o.get("ocr") in (DONE, FAILED) else DONE}
    except (ValueError, KeyError, TypeError):
        return None


def _key(d):
    """What the description depends on here."""
    return f"{d.get('rev', 0)}|{d.get('ocr')}|{title_of(d)}|{d.get('folder', '')}|{d.get('lang')}|{bool(d.get('remote'))}|{bool(d.get('named'))}"


def _pdf_key(d):
    return f"{d.get('rev', 0)}|{d.get('ocr')}"


def _read_state(store):
    try:
        with open(os.path.join(store.root, "sync.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_state(store, state):
    path = os.path.join(store.root, "sync.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    replace(path + ".tmp", path)


def sync_run(store, cfg, ensure_pdf, timeout=60):
    """Two-way sync with the WebDAV folder. `ensure_pdf(doc)` gives the PDF of a document
    scanned here. Returns (sent, received, deleted)."""
    with _sync_lock:
        return _sync_run(store, cfg, ensure_pdf, timeout)


def _sync_run(store, cfg, ensure_pdf, timeout):
    dav = WebDav(cfg.get("username", ""), cfg.get("password", ""), timeout)
    root = folder_url(cfg)
    dav.mkdirs(root)

    def url(path):
        return root + "/".join(encode_segment(p) for p in path.split("/"))

    def dir_url(folder):
        return root + encode_segment(folder) + "/"

    meta_dir = root + encode_segment(META_DIR) + "/"

    def meta_url(doc_id):
        return meta_dir + encode_segment(doc_id + ".json")

    entries = dav.list(root)
    dirs = {e["name"] for e in entries if e["dir"] and not e["name"].startswith(".")}
    pdfs = {e["name"]: e for e in entries if not e["dir"] and e["name"].lower().endswith(".pdf")}
    for d in dirs:
        for e in dav.list(dir_url(d)):
            if not e["dir"] and e["name"].lower().endswith(".pdf"):
                pdfs[f"{d}/{e['name']}"] = e
    if any(e["dir"] and e["name"] == META_DIR for e in entries):
        metas = {e["name"][:-5]: e for e in dav.list(meta_dir) if not e["dir"] and e["name"].endswith(".json")}
    else:
        dav.mkcol(meta_dir)
        metas = {}
    state = _read_state(store)
    folders, gone = store.folders_state()
    gone = set(gone)
    up = down = deleted = 0

    # --- folders
    present = set(dirs)
    for d in dirs:
        if d not in gone:
            store.folder_on_server(d)
    for f in folders:
        if f["name"] in dirs:
            continue
        if f.get("onServer"):
            store.folder_gone_there(f["name"])
        else:
            dav.mkcol(dir_url(f["name"]))
            store.folder_on_server(f["name"])
            present.add(f["name"])

    def ensure_dir(path):
        folder = path.rpartition("/")[0]
        if folder and folder not in present:
            dav.mkcol(dir_url(folder))
            store.folder_on_server(folder)
            present.add(folder)

    taken = set()

    def upload(d, sent):
        nonlocal up
        folder = d.get("folder", "")
        base = file_name_of(d)

        def at(name):
            return f"{folder}/{name}" if folder else name

        name, i = base, 2
        path = at(name)
        # another document, or someone's file, already has that name
        while path != (sent or {}).get("path") and (path in taken or path in pdfs or any(k != d["id"] and s.get("path") == path for k, s in state.items())):
            name = f"{base[:-4]} ({i}).pdf"
            path = at(name)
            i += 1
        if not d.get("remote") and (sent is None or sent.get("pdfKey") != _pdf_key(d) or sent["path"] not in pdfs):
            pdf = ensure_pdf(d)
            if not pdf:
                return
            ensure_dir(path)
            etag = dav.put_file(url(path), pdf, "application/pdf")
            if sent is not None and sent["path"] != path and sent["path"] in pdfs:
                dav.delete(url(sent["path"]))
        elif sent is not None and sent["path"] != path and sent["path"] in pdfs:
            ensure_dir(path)
            dav.move(url(sent["path"]), url(path))
            etag = dav.etag_of(url(path))
        else:
            if sent is None:
                return              # a document from elsewhere never goes up as a new file
            path, etag = sent["path"], sent.get("etag")
        taken.add(path)
        meta_etag = dav.put_text(meta_url(d["id"]), meta_build(d, path, store.text(d["id"]), Store.page_count(d)))
        state[d["id"]] = {"path": path, "etag": etag, "key": _key(d), "pdfKey": _pdf_key(d), "metaEtag": meta_etag or "?"}
        _write_state(store, state)
        up += 1

    def folder_here(name):
        return (store.folder_by_name(name) or store.folder_on_server(name)) if name else ""

    # --- 1. documents described on the server
    for doc_id, mf in metas.items():
        local = store.get(doc_id)
        sent = state.get(doc_id)
        if local is None:
            if sent is not None and (sent.get("metaEtag") is None or sent.get("metaEtag") == mf["etag"]):
                # deleted here, unchanged there: deleted there too
                if sent["path"] in pdfs:
                    dav.delete(url(sent["path"]))
                dav.delete(meta_url(doc_id))
                state.pop(doc_id)
                _write_state(store, state)
                deleted += 1
                continue
            m = meta_parse(dav.get_text(meta_url(doc_id)) or "")
            if m is None:
                continue
            doc = {"id": m["id"], "created": m["created"], "modified": m["modified"], "name": m["name"], "named": m["named"],
                   "folder": folder_here(m["folder"]), "lang": m["lang"], "pages": [], "ocr": m["ocr"], "rev": 0, "readBy": m["readBy"],
                   "remote": True, "pageCount": m["pages"]}
            store.put_remote(doc, m["text"], drop_pdf=True)
            state[doc_id] = {"path": m["pdf"], "etag": (pdfs.get(m["pdf"]) or {}).get("etag"), "key": _key(doc), "pdfKey": _pdf_key(doc), "metaEtag": mf["etag"]}
            _write_state(store, state)
            down += 1
            continue
        changed_there = sent is None or (sent.get("metaEtag") is not None and sent.get("metaEtag") != mf["etag"])
        changed_here = sent is None or sent.get("key") != _key(local)
        if changed_there:
            m = meta_parse(dav.get_text(meta_url(doc_id)) or "")
            if m is None:
                continue
            if not changed_here or m["modified"] > local.get("modified", local["created"]):
                # theirs is the latest
                if local.get("remote"):
                    pdf_changed = sent is None or sent.get("etag") != (pdfs.get(m["pdf"]) or {}).get("etag")
                    local.update(name=m["name"], named=m["named"], folder=folder_here(m["folder"]), lang=m["lang"], ocr=m["ocr"],
                                 readBy=m["readBy"], pageCount=m["pages"], modified=m["modified"])
                    store.put_remote(local, m["text"], drop_pdf=pdf_changed)
                else:
                    store.update(doc_id, name=m["name"], named=m["named"], folder=folder_here(m["folder"]), modified=m["modified"])
                now = store.get(doc_id)
                state[doc_id] = {"path": m["pdf"], "etag": (pdfs.get(m["pdf"]) or {}).get("etag"), "key": _key(now), "pdfKey": _pdf_key(now), "metaEtag": mf["etag"]}
                _write_state(store, state)
                down += 1
                continue
            # ours is the latest; its file is where they left it
            sent = dict(sent or {}, path=m["pdf"], etag=(pdfs.get(m["pdf"]) or {}).get("etag"))
        if (changed_here or (sent or {}).get("metaEtag") is None) and local.get("ocr") != PENDING:
            upload(local, sent)
        elif sent:
            taken.add(sent["path"])

    # --- 2. documents here without a description there
    for d in sorted(store.all(), key=lambda d: d["created"]):
        if d["id"] in metas:
            continue
        sent = state.get(d["id"])
        if sent is not None and sent.get("metaEtag") is not None:
            # its description went: deleted there — here too, unless it changed here since
            if sent.get("key") == _key(d):
                store.delete(d["id"])
                state.pop(d["id"])
                _write_state(store, state)
                deleted += 1
                continue
        if d.get("remote") or d.get("ocr") == PENDING:
            continue
        upload(d, sent)

    # --- 3. deleted here before ever being described (sent by an old version)
    for doc_id, s in list(state.items()):
        if store.get(doc_id) is not None or doc_id in metas:
            continue
        there = pdfs.get(s["path"])
        if there is not None and (s.get("etag") is None or there["etag"] is None or there["etag"] == s["etag"]):
            dav.delete(url(s["path"]))
            deleted += 1
        state.pop(doc_id)
        _write_state(store, state)

    # --- 4. folders deleted or renamed here: removed there once empty
    for g in gone:
        if g in dirs and not any(not e["dir"] for e in dav.list(dir_url(g))):
            dav.delete(dir_url(g))
        store.folder_removed_there(g)
    return up, down, deleted


def fetch_pdf(store, cfg, doc, progress=None):
    """Brings down the PDF of a document from elsewhere. None when the server does not have it."""
    path = (_read_state(store).get(doc["id"]) or {}).get("path")
    if not path:
        return None
    os.makedirs(store.dir(doc["id"]), exist_ok=True)
    dav = WebDav(cfg.get("username", ""), cfg.get("password", ""), 120)
    out = store.pdf_file(doc["id"])
    ok = dav.download(folder_url(cfg) + "/".join(encode_segment(p) for p in path.split("/")), out, progress)
    return out if ok else None
