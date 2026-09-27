

# ------------------------------------------------------------------------------------------
# Pages: the four looks (the phone's clean-up, on numpy), blank pages, the page as shown
# ------------------------------------------------------------------------------------------

DPI = 300
JPEG_QUALITY = 85


def _max3(a, r):
    """Maximum over a (2r+1)² neighbourhood."""
    p = np.pad(a, r, mode="edge")
    out = a.copy()
    h, w = a.shape
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            np.maximum(out, p[dy:dy + h, dx:dx + w], out=out)
    return out


def _mean3(a):
    p = np.pad(a, 1, mode="edge")
    h, w = a.shape
    return sum(p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)) / 9.0


def _background(lum):
    """The brightness the paper would have at each pixel without ink: the brightest value of
    blocks larger than a letter, widened and smoothed, spread back over every pixel. Floored at
    half the paper's usual brightness, so a photo on the page is not blown out to white."""
    h, w = lum.shape
    b = max(8, max(w, h) // 64)
    gh, gw = -(-h // b), -(-w // b)
    padded = np.pad(lum, ((0, gh * b - h), (0, gw * b - w)), mode="edge")
    grid = padded.reshape(gh, b, gw, b).max(axis=(1, 3)).astype(np.float32)
    grid = _mean3(_mean3(_max3(grid, 2)))
    paper = np.sort(grid, axis=None)[min(grid.size - 1, int(grid.size * 0.9))]
    grid = np.maximum(grid, max(40.0, paper * 0.5))
    return np.asarray(Image.fromarray(grid, mode="F").resize((w, h), Image.BILINEAR), dtype=np.float32)


def _levels(v):
    t = np.clip((v - 40.0) / 195.0, 0.0, 1.0)
    return np.clip((t * t * (3 - 2 * t) * 0.5 + t * 0.5) * 255.0, 0, 255).astype(np.uint8)


def apply_look(img, look):
    """clean: the paper turns white, shadows evened out, colours kept. grey: the same, in grey.
    bw: black ink on white. original: untouched."""
    if look not in ("clean", "grey", "bw"):
        return img
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    lum = (rgb[..., 0] * 77 + rgb[..., 1] * 150 + rgb[..., 2] * 29) / 256.0
    bg = _background(lum)
    if look == "clean":
        return Image.fromarray(_levels(rgb * (255.0 / bg)[..., None]), "RGB")
    flat = lum * 255.0 / bg
    if look == "grey":
        return Image.fromarray(_levels(flat), "L")
    flat = np.minimum(flat, 255.0)
    h, w = flat.shape
    r = max(6, max(w, h) // 80)
    integral = np.pad(flat.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(h) - r, 0, h)[:, None]; y1 = np.clip(np.arange(h) + r + 1, 0, h)[:, None]
    x0 = np.clip(np.arange(w) - r, 0, w)[None, :]; x1 = np.clip(np.arange(w) + r + 1, 0, w)[None, :]
    mean = (integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]) / ((y1 - y0) * (x1 - x0))
    return Image.fromarray(np.where((flat < mean * 0.86) | (flat < 90), 0, 255).astype(np.uint8), "L")


def is_blank(path):
    """A page with nothing on it (the back of a one-sided sheet in a two-sided scan): after
    evening out the paper, hardly any pixel is clearly darker than it. The edges are left out
    (shadows of the sheet), and single specks of dust do not count."""
    try:
        img = Image.open(path)
        img.draft("L", (img.width // 2, img.height // 2))
        img = img.convert("L")
        img.thumbnail((1200, 1200))
        lum = np.asarray(img, dtype=np.float32)
    except (OSError, ValueError):
        return False
    h, w = lum.shape
    my, mx = int(h * 0.04), int(w * 0.04)
    flat = (lum * 255.0 / _background(lum))[my:h - my, mx:w - mx]
    ink = _mean3(flat) < 165            # a speck of dust is averaged away, show-through is too pale
    # measured at this size: an empty sheet 0, a page number alone 50, a short letter 8000
    return int(ink.sum()) < 16


def open_upright(path, rotation=0, draft=None):
    img = Image.open(path)
    if draft:
        img.draft("RGB", draft)
    img = img.convert("RGB")
    if rotation % 360:
        img = img.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[rotation % 360])
    return img


def render_page(src, out, rotation, look):
    """The page as shown and as it goes into the PDF. An untouched scan is kept as it is."""
    if look == "original" and rotation % 360 == 0:
        if os.path.abspath(src) != os.path.abspath(out):
            shutil.copyfile(src, out)
        return
    img = apply_look(open_upright(src, rotation), look)
    img.save(out + ".tmp", "JPEG", quality=80 if look == "bw" else JPEG_QUALITY, dpi=(DPI, DPI))
    replace(out + ".tmp", out)


# Tesseract's glyphless font (tessdata/pdf.ttf, Apache 2.0): every character an empty glyph half
# an em wide, so any language's text can lie invisibly over the page.
_GLYPHLESS = base64.b64decode(
    "AAEAAAAKAIAAAwAgT1MvMlbeyJQAAAEoAAAAYGNtYXAACgA0AAABkAAAAB5nbHlmFSJBJAAAAbgAAAAYaGVhZAt48WUAAACsAAAANmhoZWEMAgQCAAAA5AAAACRobXR4BAAAAAAAAYgAAAAI"
    "bG9jYQAMAAAAAAGwAAAABm1heHAABAAFAAABCAAAACBuYW1l8usW2gAAAdAAAABLcG9zdAABAAEAAAIcAAAAIAABAAAAAQAAsJRxEF8PPPUEBwgAAAAAAM+a/G4AAAAA1MOn8gAAAAAEAAgAAAAA"
    "EAACAAAAAAAAAAEAAAgA//8AAAQAAAAAAAQAAAEAAAAAAAAAAAAAAAAAAAACAAEAAAACAAQAAQAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAwAAAZAABQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAUA"
    "AQABAAAAAAAAAAAAAAAAAAAAAAAAAAAAR09PRwBAAAAAAAAB//8AAAABAAGAAAAAAAAAAAAAAAAAAAABAAAAAAAABAAAAAAAAAIAAQAAAAAAFAADAAAAAAAUAAYACgAAAAAAAAAAAAAAAAAMAAAA"
    "AQAAAAAEAAgAAAMAADEhESEEAPwACAAAAAADACoAAAADAAAABQAWAAAAAQAAAAAABQALABYAAwABBAkABQAWAAAAVgBlAHIAcwBpAG8AbgAgADEALgAwVmVyc2lvbiAxLjAAAAEAAAAAAAAAAAAA"
    "AAAAAQAAAAAAAAAAAAAAAAAAAAA=")
_TO_UNICODE = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identify-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfrange
<0000> <FFFF> <0000>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


def page_inches(path, w, h):
    """The width in inches a page picture stands for: from its resolution when it says one that
    makes sense, otherwise as if its long side were A4's (or Letter's, for a page of that shape)."""
    try:
        dpi = float(Image.open(path).info.get("dpi", (0, 0))[0])
    except (OSError, ValueError, TypeError):
        dpi = 0
    if 70 <= dpi <= 1300 and max(w, h) / dpi <= 20:
        return w / dpi
    long_side = 11.0 if abs(min(w, h) / max(w, h) - 8.5 / 11) < 0.012 else 11.69
    return w * long_side / max(w, h)


def write_pdf(pages, out, title="", layers=None):
    """The document's PDF: each page picture as it is (the JPEG goes in untouched), and over it,
    invisible, the lines read on it (`layers`: per page, lines of words with their boxes in the
    picture's pixels), so the PDF can be searched and its text copied."""
    objs = []                  # bytes of each object, numbered from 1

    def add(body):
        objs.append(body if isinstance(body, bytes) else body.encode("latin-1"))
        return len(objs)

    def stream(data, extra=""):
        return f"<< {extra} /Length {len(data)} >>\nstream\n".encode("latin-1") + data + b"\nendstream"

    catalog, tree = add(b""), add(b"")
    info = add("<< /Title <FEFF" + title.encode("utf-16-be").hex().upper() + "> /Producer (Reader's Scanner) >>")
    font = None
    if layers and any(layers):
        file2 = add(stream(zlib.compress(_GLYPHLESS), f"/Filter /FlateDecode /Length1 {len(_GLYPHLESS)}"))
        descriptor = add(f"<< /Type /FontDescriptor /FontName /GlyphLessFont /FontFile2 {file2} 0 R /Ascent 1000 /CapHeight 1000 /Descent -1 /Flags 5 "
                         "/FontBBox [ 0 0 500 1000 ] /ItalicAngle 0 /StemV 80 >>")
        to_unicode = add(stream(_TO_UNICODE))
        gids = add(stream(zlib.compress(b"\x00\x01" * 65536), "/Filter /FlateDecode"))
        cid = add(f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /GlyphLessFont /CIDToGIDMap {gids} 0 R /DW 500 /FontDescriptor {descriptor} 0 R "
                  "/CIDSystemInfo << /Ordering (Identity) /Registry (Adobe) /Supplement 0 >> >>")
        font = add(f"<< /Type /Font /Subtype /Type0 /BaseFont /GlyphLessFont /DescendantFonts [ {cid} 0 R ] /Encoding /Identity-H /ToUnicode {to_unicode} 0 R >>")
    kids = []
    for i, path in enumerate(pages):
        img = Image.open(path)
        w, h = img.size
        if img.format == "JPEG" and img.mode in ("L", "RGB"):
            with open(path, "rb") as f:
                data = f.read()
            space = "/DeviceGray" if img.mode == "L" else "/DeviceRGB"
        else:                  # anything else becomes a JPEG on the way
            import io
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
            data, space = buf.getvalue(), "/DeviceRGB"
        k = page_inches(path, w, h) * 72.0 / w          # points per pixel
        pw, ph = w * k, h * k
        image = add(stream(data, f"/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {space} /BitsPerComponent 8 /Filter /DCTDecode"))
        ops = [f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q"]
        lines = (layers[i] if layers and i < len(layers) else None) or []
        if lines and font:
            ops.append("BT 3 Tr")
            for line in lines:
                top, bottom = min(wd[2] for wd in line), max(wd[4] for wd in line)
                size = max(3.0, (bottom - top) * k * 0.8)
                base = ph - (top + (bottom - top) * 0.8) * k
                for j, (text, left, _t, right, _b) in enumerate(line):
                    text += " "
                    units = len(text.encode("utf-16-be")) // 2
                    until = line[j + 1][1] if j + 1 < len(line) else right + size / k * 0.25
                    stretch = max(10.0, min(1000.0, 100.0 * max(1.0, until - left) * k / (units * 0.5 * size)))
                    ops.append(f"/F0 {size:.2f} Tf {stretch:.1f} Tz 1 0 0 1 {left * k:.2f} {base:.2f} Tm <{text.encode('utf-16-be').hex().upper()}> Tj")
            ops.append("ET")
        content = add(stream(zlib.compress("\n".join(ops).encode("latin-1")), "/Filter /FlateDecode"))
        fonts = f"/Font << /F0 {font} 0 R >> " if font else ""
        kids.append(add(f"<< /Type /Page /Parent {tree} 0 R /MediaBox [ 0 0 {pw:.2f} {ph:.2f} ] /Contents {content} 0 R "
                        f"/Resources << /XObject << /Im0 {image} 0 R >> {fonts}>> >>"))
    if not kids:
        return None
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    objs[tree - 1] = f"<< /Type /Pages /Count {len(kids)} /Kids [ {' '.join(f'{n} 0 R' for n in kids)} ] >>".encode()
    with open(out + ".tmp", "wb") as f:
        f.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for n, body in enumerate(objs, 1):
            offsets.append(f.tell())
            f.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
        xref = f.tell()
        f.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
        for o in offsets:
            f.write(f"{o:010d} 00000 n \n".encode())
        f.write(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    replace(out + ".tmp", out)
    return out


def plain_pdf(pages, out, title=""):
    """A PDF of the pages without text, when nothing can read them."""
    return write_pdf(pages, out, title)


def reading_copy(page, look, out):
    """The page as the reader wants it: grey, the paper evened out to white. Commas and dots get
    lost on a raw scan (measured: « TVA 8,1 % : 7,97 » read « TVA 81% :797 »); on this copy they
    are read. A page already cleaned is read as it is."""
    if look in ("clean", "grey", "bw"):
        return page
    apply_look(Image.open(page).convert("RGB"), "grey").save(out, "JPEG", quality=92, dpi=(DPI, DPI))
    return out


_pdfium_lock = threading.Lock()      # pdfium does one thing at a time


def pdf_pictures(pdf, out_dir, stem, dpi, first=1, last=None, quality=88):
    """The pages of a PDF as JPEG files in `out_dir`, in order (`first`…`last`, counted from 1).
    With pypdfium2 when it is installed (the Windows and macOS builds carry it), otherwise with
    poppler's pdftoppm."""
    os.makedirs(out_dir, exist_ok=True)
    try:
        import pypdfium2
    except ImportError:
        pypdfium2 = None
    if pypdfium2 is not None:
        files = []
        with _pdfium_lock:
            doc = pypdfium2.PdfDocument(pdf)
            try:
                for i in range(max(1, first) - 1, min(last or len(doc), len(doc))):
                    out = os.path.join(out_dir, f"{stem}-{i + 1:04d}.jpg")
                    doc[i].render(scale=dpi / 72.0).to_pil().convert("RGB").save(out, "JPEG", quality=quality, dpi=(dpi, dpi))
                    files.append(out)
            finally:
                doc.close()
        return files
    exe = shutil.which("pdftoppm")
    if not exe:
        raise RuntimeError(_("poppler-utils is needed to read a PDF"))
    for f in os.listdir(out_dir):
        if f.startswith(stem + "-"):
            remove(os.path.join(out_dir, f))
    cmd = [exe, "-r", str(dpi), "-jpeg", "-jpegopt", f"quality={quality}", "-f", str(max(1, first))] + (["-l", str(last)] if last else [])
    subprocess.run(cmd + [pdf, os.path.join(out_dir, stem)], capture_output=True, timeout=600, **quiet())
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith(stem + "-") and f.endswith(".jpg"))


def import_image(path, out):
    """Any picture as a page: upright (EXIF), JPEG."""
    from PIL import ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    dpi = img.info.get("dpi", (0, 0))[0] or max(72, round(max(img.size) / 11.69))
    img.save(out, "JPEG", quality=90, dpi=(dpi, dpi))


# ------------------------------------------------------------------------------------------
# Reading the text: Tesseract, on this computer. The system's models (the distribution's
# packages) read at once; the « best » ones, more accurate, are downloaded into the app's own
# folder — on request, or by themselves for a language the system does not have.
# ------------------------------------------------------------------------------------------

LANGS = ("eng", "fra", "deu", "ita", "spa", "por", "rus")
LANG_NAMES = {"eng": "English", "fra": "français", "deu": "Deutsch", "ita": "italiano", "spa": "español", "por": "português", "rus": "русский"}
MODEL_MB = {"eng": 15, "fra": 4, "deu": 9, "ita": 9, "spa": 14, "por": 8, "rus": 15}
BEST_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main/%s.traineddata"


def default_lang():
    return {"fr": "fra", "de": "deu", "it": "ita", "es": "spa", "pt": "por", "ru": "rus"}.get(_LANG, "eng")


class ReadError(Exception):
    pass


class Reader:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "tessdata")
        self._system = None
        self.downloading = {}          # language → percent
        self.prefer_best = True        # the most accurate model, fetched at the first reading in a language
        self.refused = {}              # language → when its download last failed

    @staticmethod
    def exe():
        """Tesseract: the one named by the environment, the one inside the app, the system's."""
        named = os.environ.get("READERS_SCANNER_TESSERACT")
        if named:
            return shutil.which(named)
        return bundled("tesseract", "tesseract.exe") or bundled("tesseract", "bin", "tesseract") or shutil.which("tesseract")

    @staticmethod
    def env():
        """The one inside the app is told where its own models are (orientation, English)."""
        if not os.environ.get("READERS_SCANNER_TESSERACT") and bundled("tesseract", "tessdata"):
            return dict(os.environ, TESSDATA_PREFIX=bundled("tesseract", "tessdata"))
        return dict(os.environ)

    def system(self):
        """(the system's tessdata folder, its languages)."""
        if self._system is None:
            folder, langs = None, []
            if self.exe():
                try:
                    out = subprocess.run([self.exe(), "--list-langs"], capture_output=True, text=True, timeout=20, env=self.env(), **quiet())
                    lines = (out.stdout + out.stderr).splitlines()
                    m = re.search(r'"([^"]+)"', lines[0]) if lines else None
                    folder = m.group(1) if m else None
                    langs = [l.strip() for l in lines[1:] if l.strip()]
                except (OSError, subprocess.SubprocessError):
                    pass
            self._system = (folder, langs)
        return self._system

    def has_best(self, lang):
        p = os.path.join(self.dir, lang + ".traineddata")
        return os.path.exists(p) and os.path.getsize(p) > 500_000

    def remove_best(self, lang):
        try:
            remove(os.path.join(self.dir, lang + ".traineddata"))
        except OSError:
            pass

    def _fetch(self, url, out, lang=None):
        r = requests.get(url, stream=True, timeout=(15, 60), headers={"User-Agent": f"{APP}-desktop/{VERSION}"})
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0) or MODEL_MB.get(lang, 10) * 1_000_000
        done = 0
        with open(out + ".part", "wb") as f:
            for chunk in r.iter_content(64 * 1024):
                f.write(chunk)
                done += len(chunk)
                if lang:
                    self.downloading[lang] = min(99, done * 100 // total)
        replace(out + ".part", out)

    def download(self, lang):
        """The best model of a language, into the app's folder. True when it is there."""
        if self.has_best(lang):
            return True
        if time.time() - self.refused.get(lang, 0) < 600:
            return False               # no network a moment ago: not at every document
        os.makedirs(self.dir, exist_ok=True)
        self.downloading[lang] = 0
        try:
            self._fetch(BEST_URL % lang, os.path.join(self.dir, lang + ".traineddata"), lang)
            return self.has_best(lang)
        except (OSError, requests.RequestException):
            self.refused[lang] = time.time()
            return False
        finally:
            self.downloading.pop(lang, None)

    def model_for(self, lang):
        """(tessdata folder or None for the system's, the reader's name)."""
        if self.prefer_best and (self.has_best(lang) or self.download(lang)):
            return self.dir, "tesseract-best"
        if lang in self.system()[1]:
            return None, "tesseract-fast"
        if self.has_best(lang) or self.download(lang):
            return self.dir, "tesseract-best"
        raise ReadError(_("no reading model for %1 — it could not be downloaded", LANG_NAMES.get(lang, lang)))

    def read(self, pages, lang, work, progress=None):
        """Reads the pictures (one per page). Returns (the text of each page, the lines of each
        page as lists of (word, left, top, right, bottom), the reader's name). `work`: a path
        to write beside."""
        if not self.exe():
            raise ReadError(_("Tesseract is not installed: the pages are kept without their text"))
        folder, read_by = self.model_for(lang)
        listing = work + ".list"
        with open(listing, "w", encoding="utf-8") as f:
            f.write("\n".join(pages) + "\n")
        cmd = [self.exe(), listing, work, "-l", lang, "--dpi", str(DPI)]
        if folder:
            cmd += ["--tessdata-dir", folder]
        cmd += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=1"]
        env = dict(self.env(), OMP_THREAD_LIMIT=str(max(1, min(4, os.cpu_count() or 1))))
        errors = []
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace", env=env, **quiet())
            for line in proc.stderr:
                m = re.match(r"Page (\d+)", line)
                if m and progress:
                    progress(min(len(pages), int(m.group(1))), len(pages))
                elif line.strip():
                    errors.append(line.strip())
            proc.wait()
        except OSError as e:
            raise ReadError(str(e))
        finally:
            try:
                remove(listing)
            except OSError:
                pass
        if proc.returncode != 0 or not os.path.exists(work + ".tsv"):
            raise ReadError(errors[-1] if errors else "tesseract")
        try:
            with open(work + ".txt", encoding="utf-8", errors="replace") as f:
                text = f.read().split("\f")
            remove(work + ".txt")
        except OSError:
            text = []
        text = [t.strip() for t in text[:len(pages)]]
        text += [""] * (len(pages) - len(text))
        layers = [[] for _p in pages]
        lines = {}
        with open(work + ".tsv", encoding="utf-8", errors="replace") as f:
            for row in f:
                c = row.rstrip("\n").split("\t", 11)
                if len(c) < 12 or c[0] != "5" or not c[11].strip():
                    continue
                try:
                    page, left, top, width, height = int(c[1]) - 1, int(c[6]), int(c[7]), int(c[8]), int(c[9])
                except ValueError:
                    continue
                if 0 <= page < len(pages):
                    key = (page, c[2], c[3], c[4])
                    if key not in lines:
                        lines[key] = []
                        layers[page].append(lines[key])
                    lines[key].append((c[11].strip(), left, top, left + width, top + height))
        remove(work + ".tsv")
        return text, layers, read_by


def upright_rotations(files, reader):
    """Sheets fed upside down or sideways: how far each page must be turned to read upright
    (Tesseract's orientation detection, on a few pages at a time). 0 when it is not sure, when
    the page has too little text, or when the orientation model is not there."""
    exe = reader.exe()
    if not exe or "osd" not in reader.system()[1]:
        return {}

    def one(path):
        try:
            out = subprocess.run([exe, path, "-", "--psm", "0", "-l", "osd", "--dpi", str(DPI)], capture_output=True, text=True, errors="replace", timeout=60,
                                 env=reader.env(), **quiet())
            turn = re.search(r"Rotate: (\d+)", out.stdout)
            sure = re.search(r"Orientation confidence: ([\d.]+)", out.stdout)
            if turn and sure and float(sure.group(1)) >= 2.5 and int(turn.group(1)) in (90, 180, 270):
                return int(turn.group(1))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return 0

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, min(4, os.cpu_count() or 1))) as pool:
        return {f: r for f, r in zip(files, pool.map(one, files)) if r}


class ReadQueue:
    """Reads filed documents one at a time, off the UI thread: makes the pages as shown, reads
    them, names the document after its first words. Documents still waiting when the app was
    closed are taken up again at the next start."""

    def __init__(self, store, reader, on_done=None, on_progress=None):
        self.store, self.reader = store, reader
        self.on_done, self.on_progress = on_done, on_progress
        self.q = queue.Queue()
        self.working = {}              # document → "2/5"
        self.errors = {}               # document → why it could not be read
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        for doc_id in store.pending():
            self.q.put(doc_id)

    def enqueue(self, doc_id):
        self.q.put(doc_id)

    def idle(self):
        return self.q.empty() and not self.working

    def _say(self, doc_id, text):
        if text is None:
            self.working.pop(doc_id, None)
        else:
            self.working[doc_id] = text
        if self.on_progress:
            self.on_progress()

    def _run(self):
        while True:
            doc_id = self.q.get()
            doc = self.store.get(doc_id)
            if doc is None or doc.get("ocr") != PENDING or doc.get("remote"):
                continue
            try:
                self._read(doc)
            except Exception as e:     # a page that cannot be opened, a disk that is full…
                self.errors[doc_id] = str(e)
                self.store.ocr_failed(doc_id, doc.get("rev", 0))
            self._say(doc_id, None)
            if self.on_done:
                self.on_done(doc_id)

    def _read(self, doc):
        doc_id, rev = doc["id"], doc.get("rev", 0)
        self._say(doc_id, "")
        pages = []
        for p in doc["pages"]:
            out = self.store.page_file(doc_id, p["id"])
            if not os.path.exists(out):
                render_page(self.store.src_file(doc_id, p["id"]), out, p.get("rotation", 0), p.get("look", "original"))
            pages.append(out)
        base = os.path.join(self.store.dir(doc_id), "ocr-out")
        copies = []
        try:
            for i, (p, f) in enumerate(zip(doc["pages"], pages)):
                copies.append(reading_copy(f, p.get("look", "original"), f"{base}-{i}.jpg"))
            text, layers, read_by = self.reader.read(copies, doc.get("lang", "eng"), base, lambda i, n: self._say(doc_id, f"{i}/{n}"))
            self.errors.pop(doc_id, None)
            named = doc if doc.get("named") else dict(doc, name=first_words(next((t for t in text if t.strip()), "")) or doc.get("name"))
            if not self.store.ocr_done(doc_id, rev, text, write_pdf(pages, base + ".pdf", title_of(named), layers), read_by):
                self.q.put(doc_id)     # its pages changed meanwhile: read again
        except ReadError as e:
            self.errors[doc_id] = str(e)
            self.store.ocr_failed(doc_id, rev, plain_pdf(pages, base + ".pdf", title_of(doc)))
        finally:
            for c in copies:
                if c not in pages:
                    try:
                        remove(c)
                    except OSError:
                        pass


# ------------------------------------------------------------------------------------------
# The scanner: NAPS2 does the acquisition (naps2.com, a separate program), through its
# console. It gets a settings folder of its own (NAPS2_TEST_DATA) holding one profile that
# names the device: a scan then starts at once, without NAPS2 looking for scanners again
# (10 s with SANE), and the user's own NAPS2 profiles are never touched.
# ------------------------------------------------------------------------------------------

NAPS2_URL = "https://www.naps2.com/download"
SOURCES = ("auto", "glass", "feeder", "duplex")
_ERRORS = (   # what NAPS2 says in English → our word for it; NAPS2_WORDS has the other languages
    ("No pages are in the feeder", "empty"), ("does not support using a feeder", "nofeeder"), ("does not support using duplex", "noduplex"),
    ("could not be found", "notfound"), ("scanner is offline", "offline"), ("scanner is busy", "busy"), ("cover is open", "cover"),
    ("paper jam", "jam"), ("warming up", "warming"), ("was interrupted", "comm"), ("SANE driver is not available", "nosane"),
    ("No device was specified", "notfound"), ("error occurred with the scanning driver", "driver"), ("unexpected error", "driver"),
    ("worker process crashed", "driver"),
)


def error_of(line):
    """Our word for what NAPS2 said on this line, or None. English where we can ask for it
    (Linux, macOS); on Windows NAPS2 speaks the system's language."""
    low = re.sub(r"\s+", " ", line).strip().lower()
    for needle, code in _ERRORS:
        if needle.lower() in low:
            return code
    if len(low) > 12:
        for sentence, code in NAPS2_WORDS.items():
            if sentence in low or (len(low) > 20 and sentence.startswith(low.rstrip(".。"))):
                return code
    return None


def error_in(raw):
    """(our word, NAPS2's words) for a line as NAPS2 wrote it, in bytes: on Windows nobody says
    which code page a program without a console writes in, so the likely ones are tried until
    the sentence is one NAPS2 has."""
    pages = ["utf-8"] + (["oem", "mbcs"] if sys.platform == "win32" else []) + ["cp850", "cp1252", "cp866", "cp1251", "cp852", "cp1250", "cp437",
                                                                                 "cp932", "cp936", "cp949", "cp950", "cp1253", "cp1254", "cp1255", "cp1256", "cp874"]
    for page in pages:
        try:
            line = raw.decode(page)
        except (UnicodeDecodeError, LookupError):
            continue
        code = error_of(line)
        if code:
            return code, line.strip()
    return None, ""


def error_text(code, detail=""):
    return {
        "empty": _("the feeder is empty"), "nofeeder": _("this scanner has no feeder"), "noduplex": _("this scanner cannot scan both sides"),
        "notfound": _("the scanner is not answering — is it switched on?"), "offline": _("the scanner is not answering — is it switched on?"),
        "busy": _("the scanner is busy"), "cover": _("the scanner's cover is open"), "jam": _("paper jam in the scanner"),
        "warming": _("the scanner is warming up — try again in a moment"), "comm": _("the connection to the scanner was interrupted"),
        "nosane": _("SANE is not installed (the scanner drivers)"), "cancelled": _("scan cancelled"),
        "nodevice": _("no scanner found — is it switched on?"), "nonaps2": _("no scanner found"),
        "multipick": _("two sheets went in together"),
    }.get(code) or (detail or _("the scan did not work"))


def _model_key(name):
    """The same scanner reached by several drivers gets the same key."""
    n = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", name.lower().replace("_", " "))
    n = re.sub(r"\b(hewlett[- ]?packard|hp|canon|epson|brother|fujitsu|ricoh|samsung|xerox|kodak|lexmark|kyocera)\b", " ", n)
    return re.sub(r"[^a-z0-9]", "", n) or re.sub(r"[^a-z0-9]", "", name.lower())


# the scanner asked directly first (parts/19_escl.py), then NAPS2's ways to it: the driverless
# ones, which work without the maker's software. sane's own « escl » comes last of all: with a
# stack in the feeder it handed over one page (HP ScanJet Pro 4500 fn1, 2026-09-27).
_BACKEND_ORDER = ("direct", "airscan")
_BACKEND_LAST = ("escl",)


def backend_rank(backend):
    if backend in _BACKEND_ORDER:
        return _BACKEND_ORDER.index(backend)
    return len(_BACKEND_ORDER) + (2 if backend in _BACKEND_LAST else 1)


def link_of(way):
    """"usb" or "net": how this way reaches the scanner."""
    if way.get("link"):
        return way["link"]
    words = f"{way.get('id') or ''} {way.get('name') or ''}".lower()
    return "usb" if "(usb)" in words or "/usb/" in words or ":usb:" in words or "//localhost" in words or "libusb" in words else "net"


def way_order(way):
    """The app chooses, nobody is asked: a way that failed twice running goes last; then the
    better driver; then, of two ways by the same driver, the cable before the network."""
    return (way.get("misses", 0) >= 2, backend_rank(way["backend"]), link_of(way) != "usb")


class Naps2:
    def __init__(self, data_dir):
        self.data = os.path.join(data_dir, "naps2")
        self.cmd = self.find()
        self.version = self._version() if self.cmd else None
        if self.version is None:
            self.cmd = None            # there, but it does not run: as good as absent
        self.proc = None
        self._cancelled = False
        self.heard = []                # what NAPS2 wrote during the last scans, for whoever must understand one
        self.alive = 0                 # when the scanner last answered
        self.direct = None             # the scanner being asked directly, while it scans
        self.asked_all = False

    @staticmethod
    def find():
        env = os.environ.get("READERS_SCANNER_NAPS2")
        if env:
            return shlex.split(env)
        if sys.platform == "win32":
            for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles(x86)"),
                         os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs"), os.environ.get("LOCALAPPDATA")):
                p = os.path.join(base or "", "NAPS2", "NAPS2.Console.exe")
                if base and os.path.exists(p):
                    return [p]
            exe = shutil.which("NAPS2.Console.exe") or shutil.which("naps2.console")
            return [exe] if exe else None
        if sys.platform == "darwin":
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                p = os.path.join(base, "NAPS2.app", "Contents", "MacOS", "NAPS2")
                if os.path.exists(p):
                    return [p, "console"]
            return None
        exe = shutil.which("naps2")
        if exe:
            return [exe, "console"]
        if shutil.which("flatpak"):
            try:
                if subprocess.run(["flatpak", "info", "com.naps2.Naps2"], capture_output=True, timeout=10, **quiet()).returncode == 0:
                    return ["flatpak", "run", "--command=naps2", "com.naps2.Naps2", "console"]
            except (OSError, subprocess.SubprocessError):
                pass
        return None

    @property
    def flatpak(self):
        return bool(self.cmd) and self.cmd[0] == "flatpak"

    @property
    def drivers(self):
        """NAPS2's drivers for this desktop, the usual one first."""
        named = os.environ.get("READERS_SCANNER_DRIVER")
        if named:
            return tuple(named.split(","))
        return ("wia", "twain") if sys.platform == "win32" else ("apple", "escl") if sys.platform == "darwin" else ("sane",)

    @property
    def driver(self):
        return self.drivers[0]

    def _env(self):
        env = dict(os.environ, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8", LANGUAGE="en")
        if not self.flatpak:
            os.makedirs(self.data, exist_ok=True)
            env["NAPS2_TEST_DATA"] = self.data
        return env

    def _version(self):
        try:
            out = subprocess.run(self.cmd + ["--help"], capture_output=True, timeout=60, env=self._env(), **quiet())
            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", said(out.stdout + out.stderr).strip().splitlines()[0])
            return m.group(1) if m else "?"
        except (OSError, subprocess.SubprocessError, IndexError):
            return None

    def devices(self, every=True):
        """Every way to every scanner: {"id" (None when only NAPS2 knows it), "name", "backend",
        "key"}, and "url" for a scanner that can be asked directly. Those are found in a moment;
        NAPS2's ways take ten seconds and more: with `every` false they are only looked for when
        no scanner answers by itself."""
        named = os.environ.get("READERS_SCANNER_DIRECT")
        if named is not None:              # the tests' scanners, and no others
            direct = [w for w in (escl_probe(u.strip()) for u in named.split(",") if u.strip()) if w]
        else:
            direct = escl_find()
        direct.sort(key=way_order)
        self.asked_all = not (direct and not every) and bool(self.cmd)      # were NAPS2's ways looked for too?
        if not self.asked_all:
            return direct
        found = self._devices()
        if named is None:                  # sane's escl names the address of a network scanner: it can be asked directly too
            known = {d["url"] for d in direct}
            for d in found:
                url = (d.get("id") or "")[5:] if (d.get("id") or "").startswith("escl:http") else None
                if url and url.rstrip("/") not in known and "localhost" not in url:
                    way = escl_probe(url.rstrip("/"))
                    if way and way["uuid"] not in {x["uuid"] for x in direct if x["uuid"]}:
                        direct.append(way)
                        known.add(way["url"])
        return sorted(direct + found, key=way_order)

    def _devices(self):
        found, asked = [], False
        scanimage = os.environ.get("READERS_SCANNER_SCANIMAGE") or shutil.which("scanimage")
        if self.driver == "sane" and scanimage and not self.flatpak:
            try:
                out = subprocess.run(shlex.split(scanimage) + ["-f", "%d\t%v\t%m\t%t%n"], capture_output=True, text=True, errors="replace", timeout=60, **quiet())
                asked = out.returncode == 0        # SANE answered: NAPS2, which asks SANE too, would find no more
                for line in out.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 3 and parts[0]:
                        backend = parts[0].split(":")[0]
                        model = parts[2].replace("_", " ").strip()
                        vendor = parts[1].strip()
                        initials = "".join(t[0] for t in re.split(r"[\s-]+", vendor) if t).lower()
                        known = vendor in ("eSCL", "WSD", "") or model.lower().startswith((vendor.lower() + " ", initials + " "))
                        name = model if known else f"{vendor} {model}"
                        found.append({"id": parts[0], "name": name, "backend": backend, "key": _model_key(parts[2])})
            except (OSError, subprocess.SubprocessError):
                pass
        for driver in self.drivers if self.cmd and not found and not asked else ():
            try:
                try:                   # the second driver is a last resort: it is not waited for a whole minute
                    listed = subprocess.run(self.cmd + ["--listdevices", "--driver", driver], capture_output=True, env=self._env(),
                                            timeout=90 if driver == self.driver else 25, **quiet()).stdout
                except subprocess.TimeoutExpired as late:
                    listed = late.stdout or b""
                for line in said(listed).splitlines():
                    line = line.strip()
                    if not line or error_of(line) or any(w in line for w in ("not available", "could not", "error")):
                        continue
                    m = re.match(r"^(.*\S)\s+\(([^()]+)\)$", line)
                    inner = m.group(2) if m else ""
                    found.append({"id": inner if inner.startswith("escl:") else None, "name": line, "driver": driver,
                                  "backend": (inner.split(":")[0] if driver == "sane" else "") or driver, "key": _model_key(m.group(1) if m else line)})
            except (OSError, subprocess.SubprocessError):
                pass
            if found:
                break                  # the usual driver sees it: the others are not asked
        return sorted(found, key=way_order)

    def _profile(self, device, source, pagesize, deskew):
        os.makedirs(self.data, exist_ok=True)
        x = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ArrayOfScanProfile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <ScanProfile>
    <Version>2</Version>
    <Device><ID>{x(device["id"])}</ID><Name>{x(device["name"])}</Name></Device>
    <DriverName>{device.get("driver") or self.driver}</DriverName>
    <DisplayName>readers-scanner</DisplayName>
    <IsDefault>true</IsDefault>
    <BitDepth>C24Bit</BitDepth>
    <PageSize>{pagesize}</PageSize>
    <Resolution>Dpi{DPI}</Resolution>
    <PaperSource>{source.capitalize()}</PaperSource>
    <AutoDeskew>{"true" if deskew else "false"}</AutoDeskew>
    <Quality>{JPEG_QUALITY}</Quality>
  </ScanProfile>
</ArrayOfScanProfile>
"""
        with open(os.path.join(self.data, "profiles.xml"), "w", encoding="utf-8") as f:
            f.write(xml)

    def scan(self, device, source, pagesize, out_dir, on_page=None):
        """One scan from one source. Returns (page files, error code or None, NAPS2's words)."""
        if device.get("url"):
            self._cancelled = False
            self.direct = Escl(device["url"])
            try:
                self.heard = self.heard[-200:] + [f"--- {source} · direct · {datetime.now():%H:%M:%S}"]
                files, code, words = self.direct.scan(source, pagesize, out_dir, on_page)
                self.heard.append(f"{len(files)} page(s) {code or ''} {words}".strip())
                return ([], "cancelled", "") if self._cancelled else (files, code, words)
            finally:
                self.direct = None
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(out_dir):
            remove(os.path.join(out_dir, f))
        deskew = source != "glass"       # a feeder pulls sheets askew; on the glass, leave the page as laid
        out = os.path.join(out_dir, "p$(nnnn).jpg")
        if device.get("id") and not self.flatpak:
            self._profile(device, source, pagesize, deskew)
            cmd = self.cmd + ["-p", "readers-scanner"]
        else:
            shown = device["name"] if (device.get("driver") or self.driver) != "sane" else re.sub(r"\s+\([^()]*\)$", "", device["name"])
            cmd = self.cmd + ["--noprofile", "--driver", device.get("driver") or self.driver, "--device", shown,
                              "--source", source, "--dpi", str(DPI), "--bitdepth", "color", "--pagesize", pagesize.lower()] + (["--deskew"] if deskew else [])
        cmd += ["-o", out, "--jpegquality", str(JPEG_QUALITY), "-f", "-v"]
        code, words = None, ""
        self._cancelled = False
        try:
            # a group of its own: NAPS2 scans through a helper process, and « cancel » must reach both
            own = {"creationflags": 0x08000000 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self._env(), **own)
            self.heard = self.heard[-200:] + [f"--- {source} · {device.get('backend') or device.get('driver')} · {datetime.now():%H:%M:%S}"]
            for raw in self.proc.stdout:
                line = said(raw).strip()
                line and self.heard.append(line)
                m = re.match(r"Scanned page (\d+)", line)
                if m and on_page:
                    on_page(int(m.group(1)))
                if code is None:
                    code, words = error_in(raw)
            self.proc.wait()
        except OSError as e:
            return [], "driver", str(e)
        finally:
            self.proc = None
        if self._cancelled:
            self._cancelled = False
            return [], "cancelled", ""
        files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.lower().endswith(".jpg"))
        if files:
            return files, None, ""
        return [], code or "unknown", words

    def cancel(self):
        direct = self.direct
        if direct is not None:
            self._cancelled = True
            direct.cancel()
        p = self.proc
        if p is not None:
            self._cancelled = True
            try:
                if sys.platform == "win32":    # no signal to send there: NAPS2 and its helper are ended
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=20, **quiet())
                else:                          # asked first, then told, the helper with it
                    group = os.getpgid(p.pid)
                    os.killpg(group, signal.SIGINT)

                    def insist(how):
                        try:
                            p.poll() is None and os.killpg(group, how)
                        except OSError:
                            pass
                    threading.Timer(1.5, insist, (signal.SIGTERM,)).start()
                    threading.Timer(4, insist, (signal.SIGKILL,)).start()
            except (OSError, subprocess.SubprocessError):
                pass


def page_size_of(cfg):
    fmt = cfg.get("format", "auto")
    if fmt == "auto":
        country = (os.environ.get("LC_ALL") or os.environ.get("LC_PAPER") or os.environ.get("LANG") or "").split(".")[0][-2:].upper()
        if not country.isalpha() or len(country) != 2:
            country = system_locale()[-2:].upper()
        return "Letter" if country in ("US", "CA", "MX", "PH", "CL", "CO") else "A4"
    return "Letter" if fmt == "letter" else "A4"


def scan_pages(naps2, cfg, source, out_dir, on_page=None, on_state=None):
    """A scan with the smart defaults: « automatic » takes the feeder when it holds paper and the
    glass otherwise; when the scanner does not answer on one driver, the next one is tried, and
    the scanners are looked for again once (an address may have changed). Returns a dict:
    files, source (the one used), error (code) and detail, blank (pages left out), device."""
    routes = list((cfg.get("device") or {}).get("routes") or [])
    if not naps2.cmd:
        routes = [r for r in routes if r.get("url")]
    searched = 0                       # 1: the scanners that answer by themselves were looked for; 2: NAPS2's too
    if not routes:
        on_state and on_state("searching")
        found = naps2.devices(every=False)
        routes = pick_routes(found, None)
        searched = 2 if naps2.asked_all or not naps2.cmd else 1
        if not routes:
            return {"files": [], "error": "nodevice" if naps2.cmd else "nonaps2", "detail": ""}
    key = routes[0]["key"]
    pagesize = page_size_of(cfg)
    last = ("unknown", "")
    missed = []                        # the ways that did not answer during this scan
    for src in (("feeder", "glass") if source == "auto" else (source,)):
        tries = list(routes)
        while tries:
            route = tries.pop(0)
            on_state and on_state(src)
            for patience in range(5):      # just after a scan the scanner may still be busy: a moment, not an error
                files, err, said = naps2.scan(route, src, pagesize, out_dir, on_page)
                if files or err in ("empty", "busy", "warming", "nofeeder", "noduplex", "cover", "jam", "multipick"):
                    naps2.alive = time.time()
                # « offline » from a scanner that answered a minute ago is the same moment of absence
                # (asked directly, a scanner that does not answer is not there: the next way at once)
                moment = err in ("busy", "warming") or (err in ("offline", "comm") and patience == 0 and time.time() - naps2.alive < 90 and not route.get("url"))
                if not moment or patience == 4:
                    break
                on_state and on_state("waiting")
                time.sleep(2.5)
            if files:
                blank = []
                if src in ("feeder", "duplex"):      # the backs of one-sided sheets, a separator sheet
                    blank = [f for f in files if is_blank(f)]
                    if len(blank) == len(files):
                        blank = []             # a stack of empty sheets is what was asked for
                # the order of preference stays (the driverless airscan first: on the scanner this was
                # tried on, sane's own escl gave one page of a stack); a way that failed twice running
                # goes behind the others
                for r in routes:
                    r["misses"] = 0 if r is route else r.get("misses", 0) + (1 if r in missed else 0)
                routes = sorted(routes, key=way_order)
                return {"files": [f for f in files if f not in blank], "blank": blank, "source": src, "error": None,
                        "device": {"key": key, "name": route["name"], "routes": routes}}
            last = (err, said)
            if err in ("notfound", "offline", "comm", "driver", "unknown") and route not in missed:
                missed.append(route)
            if err == "cancelled":
                return {"files": [], "error": err, "detail": ""}
            if err in ("empty", "nofeeder", "noduplex", "unknown") and src != "glass":
                break                          # nothing in the feeder: the glass, when automatic
            if err in ("notfound", "offline", "comm", "driver", "unknown"):
                while not tries and searched < 2:
                    # looked for again, once: first those that answer by themselves (a moment), then every way
                    on_state and on_state("searching")
                    again = pick_routes(naps2.devices(every=searched == 1), key)
                    searched = 2 if naps2.asked_all or not naps2.cmd else searched + 1
                    tries = [r for r in again if r.get("id") not in {x.get("id") for x in routes} or r.get("id") is None] if again else []
                    for r in again:        # what is known of a way is kept
                        r["misses"] = next((x.get("misses", 0) for x in routes if x.get("id") == r.get("id") and r.get("id")), 0)
                    # every way looked for: what is not found any more is forgotten; after the quick
                    # look, NAPS2's ways, which it did not ask, stay
                    kept = [x for x in routes if not x.get("url") and x.get("id") not in {r.get("id") for r in again}] if searched == 1 else []
                    routes = (again + kept) if again else routes
                continue
            return {"files": [], "error": err, "detail": said, "source": src}
        else:
            if source == "auto" and src == "feeder" and last[0] in ("notfound", "offline", "comm", "driver"):
                break                          # nobody answers: the glass would not either
    return {"files": [], "error": last[0], "detail": last[1]}


def pick_routes(devices, key):
    """The ways to one scanner: the one asked for, or the first found."""
    if not devices:
        return []
    key = key if key and any(d["key"] == key for d in devices) else devices[0]["key"]
    return [d for d in devices if d["key"] == key]
