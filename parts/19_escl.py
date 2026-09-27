# ------------------------------------------------------------------------------------------
# Scanners asked directly. Most scanners sold since about 2015 speak eSCL (« AirScan »,
# « Mopria »): HTTP and a little XML, over the network, or over USB through ipp-usb on Linux.
# Nothing to install, no driver; the scanner says itself whether its feeder is loaded, hands
# its pages over one by one, and names what goes wrong by a code instead of a sentence.
# ------------------------------------------------------------------------------------------

ESCL_NS = {"scan": "http://schemas.hp.com/imaging/escl/2011/05/03", "pwg": "http://www.pwg.org/schemas/2010/12/sm"}
_ADF_STATES = {   # what the scanner says of its feeder → our word for it
    "ScannerAdfEmpty": "empty", "ScannerAdfJam": "jam", "ScannerAdfMispick": "jam", "ScannerAdfMultipickDetected": "multipick",
    "ScannerAdfDoorOpen": "cover", "ScannerAdfHatchOpen": "cover", "ScannerAdfInputTrayFailed": "jam", "ScannerAdfInputTrayOverloaded": "jam",
    "ScannerAdfDuplexPageTooShort": "jam", "ScannerAdfDuplexPageTooLong": "jam",
}


class EsclError(Exception):
    def __init__(self, code, words=""):
        super().__init__(words or code)
        self.code, self.words = code, words


class Escl:
    """One scanner at one address ("http://192.168.1.120:8080", "http://localhost:60001")."""

    def __init__(self, url, timeout=8):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self.http.verify = False           # scanners sign their own certificates
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        self.http.headers["User-Agent"] = f"{APP}-desktop/{VERSION}"
        self.job = None
        self._cancelled = False
        self._caps = None

    # ---- what it is and how it is --------------------------------------------------------

    def _get(self, path, timeout=None):
        try:
            r = self.http.get(self.url + path, timeout=(min(3, timeout or self.timeout), timeout or self.timeout))
        except requests.RequestException as e:
            raise EsclError("offline", str(e))
        if r.status_code == 503:
            raise EsclError("busy", "503")
        if r.status_code != 200:
            raise EsclError("offline", f"{path}: HTTP {r.status_code}")
        return r

    @staticmethod
    def _xml(text):
        try:
            return ET.fromstring(text)
        except ET.ParseError as e:
            raise EsclError("driver", str(e))

    def caps(self):
        """{"name", "uuid", "version", "glass": {...} or None, "feeder": {...} or None}; a source:
        {"width", "height" (300ths of an inch), "dpi": [...], "modes": [...], "formats": [...], "duplex": bool}."""
        if self._caps is None:
            root = self._xml(self._get("/eSCL/ScannerCapabilities").content)

            def text(node, path):
                found = node.find(path, ESCL_NS) if node is not None else None
                return (found.text or "").strip() if found is not None else ""

            def source(caps, duplex=False):
                if caps is None:
                    return None
                dpi = sorted({int(x.text) for x in caps.iterfind(".//scan:DiscreteResolution/scan:XResolution", ESCL_NS) if (x.text or "").strip().isdigit()})
                ranges = caps.find(".//scan:ResolutionRange/scan:XResolutionRange", ESCL_NS)
                if not dpi and ranges is not None:
                    low, high = int(text(ranges, "scan:Min") or 75), int(text(ranges, "scan:Max") or 600)
                    dpi = [d for d in (75, 100, 150, 200, 300, 400, 600, 1200) if low <= d <= high]
                return {"width": int(text(caps, "scan:MaxWidth") or 2550), "height": int(text(caps, "scan:MaxHeight") or 3508), "dpi": dpi,
                        "modes": sorted({(x.text or "").strip() for x in caps.iterfind(".//scan:ColorMode", ESCL_NS)}),
                        "formats": sorted({(x.text or "").strip() for x in caps.iterfind(".//pwg:DocumentFormat", ESCL_NS)}
                                          | {(x.text or "").strip() for x in caps.iterfind(".//scan:DocumentFormatExt", ESCL_NS)}),
                        "duplex": duplex}

            adf = root.find("scan:Adf", ESCL_NS)
            two = adf.find("scan:AdfDuplexInputCaps", ESCL_NS) if adf is not None else None
            one = adf.find("scan:AdfSimplexInputCaps", ESCL_NS) if adf is not None else None
            options = {(x.text or "").strip() for x in adf.iterfind(".//scan:AdfOption", ESCL_NS)} if adf is not None else set()
            self._caps = {"name": text(root, "pwg:MakeAndModel") or "scanner", "uuid": text(root, "scan:UUID"), "version": text(root, "pwg:Version") or "2.0",
                          "glass": source(root.find("scan:Platen/scan:PlatenInputCaps", ESCL_NS)),
                          "feeder": source(one if one is not None else two, duplex=two is not None or "Duplex" in options),
                          "both": source(two, duplex=True), "knows_if_loaded": "DetectPaperLoaded" in options}
        return self._caps

    def status(self):
        """(state: "Idle", "Processing", "Stopped"…, feeder: "ScannerAdfLoaded", "ScannerAdfEmpty"… or "")."""
        root = self._xml(self._get("/eSCL/ScannerStatus").content)
        state, adf = root.find("pwg:State", ESCL_NS), root.find("scan:AdfState", ESCL_NS)
        return ((state.text or "").strip() if state is not None else "", (adf.text or "").strip() if adf is not None else "")

    def job_reasons(self, job):
        """Why a job ended, as the scanner tells it afterwards."""
        try:
            root = self._xml(self._get("/eSCL/ScannerStatus").content)
        except EsclError:
            return []
        for info in root.iterfind(".//scan:JobInfo", ESCL_NS):
            uri = info.find("pwg:JobUri", ESCL_NS)
            if uri is not None and (uri.text or "").strip().rstrip("/") == job.rstrip("/"):
                return [(x.text or "").strip() for x in info.iterfind(".//pwg:JobStateReason", ESCL_NS)]
        return []

    # ---- a scan ------------------------------------------------------------------------------

    def _settings(self, source, pagesize, dpi):
        caps = self.caps()
        box = caps["glass" if source == "glass" else "both" if source == "duplex" and caps.get("both") else "feeder"]
        if box is None:
            raise EsclError("nofeeder" if source != "glass" else "driver")
        if source == "duplex" and not caps["feeder"]["duplex"]:
            raise EsclError("noduplex")
        width, height = (2550, 3300) if pagesize == "Letter" else (2480, 3508)
        width, height = min(width, box["width"]), min(height, box["height"])
        dpi = dpi if dpi in box["dpi"] or not box["dpi"] else min(box["dpi"], key=lambda d: (abs(d - dpi), -d))
        mode = "RGB24" if "RGB24" in box["modes"] or not box["modes"] else box["modes"][0]
        ext = float(caps["version"]) >= 2.1 if re.fullmatch(r"\d+(\.\d+)?", caps["version"]) else False
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<scan:ScanSettings xmlns:scan="{ESCL_NS["scan"]}" xmlns:pwg="{ESCL_NS["pwg"]}">'
                f'<pwg:Version>{caps["version"]}</pwg:Version>'
                '<pwg:ScanRegions><pwg:ScanRegion><pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>'
                f'<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset><pwg:Width>{width}</pwg:Width><pwg:Height>{height}</pwg:Height>'
                '</pwg:ScanRegion></pwg:ScanRegions>'
                f'<pwg:InputSource>{"Platen" if source == "glass" else "Feeder"}</pwg:InputSource>'
                f'<scan:ColorMode>{mode}</scan:ColorMode>'
                '<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>'
                + ('<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>' if ext else '')
                + f'<scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>'
                + (f'<scan:Duplex>{"true" if source == "duplex" else "false"}</scan:Duplex>' if source != "glass" else '')
                + '</scan:ScanSettings>'), dpi

    def scan(self, source, pagesize, out_dir, on_page=None, dpi=None):
        """One scan from one source ("glass", "feeder", "duplex"). Returns (page files, error code
        or None, the scanner's words). The pages are written as they come."""
        dpi = dpi or DPI
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(out_dir):
            remove(os.path.join(out_dir, f))
        self._cancelled, self.job, files = False, None, []
        try:
            state, adf = self.status()     # is it there at all? a cable pulled out is known in a moment
            if source != "glass" and adf in _ADF_STATES:
                return [], _ADF_STATES[adf], adf
            body, dpi = self._settings(source, pagesize, dpi)
            for patience in range(8):
                try:
                    r = self.http.post(self.url + "/eSCL/ScanJobs", data=body.encode("utf-8"), headers={"Content-Type": "text/xml"}, timeout=(3, 30))
                except requests.RequestException as e:
                    return [], "offline", str(e)
                if r.status_code != 503 or self._cancelled:
                    break
                time.sleep(2)              # busy with the scan before
            if self._cancelled:
                return [], "cancelled", ""
            if r.status_code == 503:
                return [], "busy", "503"
            if r.status_code not in (200, 201) or not r.headers.get("Location"):
                state, adf = self.status()
                return [], _ADF_STATES.get(adf) or ("empty" if r.status_code == 409 and source != "glass" else "driver"), f"HTTP {r.status_code} {adf}".strip()
            self.job = urlparse(r.headers["Location"]).path.rstrip("/")     # the address it gives may be one only it knows
            quiet_tries = 0
            while not self._cancelled:
                try:
                    page = self.http.get(self.url + self.job + "/NextDocument", timeout=(10, 180))
                except requests.RequestException as e:
                    if files:
                        break
                    return [], "comm", str(e)
                if page.status_code == 200 and page.content:
                    out = os.path.join(out_dir, f"p{len(files) + 1:04d}.jpg")
                    with open(out, "wb") as f:
                        f.write(page.content)
                    self._stamp(out, dpi)
                    files.append(out)
                    quiet_tries = 0
                    on_page and on_page(len(files))
                    if source == "glass":
                        break
                elif page.status_code == 503 and quiet_tries < 30:
                    quiet_tries += 1       # the page is not ready yet
                    time.sleep(1)
                else:
                    break                  # 404: no more pages
            if self._cancelled:
                return [], "cancelled", ""
            if files:
                return files, None, ""
            reasons = self.job_reasons(self.job)
            state, adf = self.status()
            return [], _ADF_STATES.get(adf) or ("empty" if source != "glass" else "driver"), " ".join(reasons + [adf]).strip()
        except EsclError as e:
            return [], e.code, e.words
        finally:
            job, self.job = self.job, None
            if job and (self._cancelled or not files):
                try:
                    self.http.delete(self.url + job, timeout=5)
                except requests.RequestException:
                    pass

    @staticmethod
    def _stamp(path, dpi):
        """The resolution written in the file when the scanner left it out (the PDF's page size
        comes from it) — without touching the picture."""
        try:
            with open(path, "r+b") as f:
                head = f.read(20)
                if head[:4] == b"\xff\xd8\xff\xe0" and head[6:11] == b"JFIF\x00" and (head[13] == 0 or head[14:16] in (b"\x00\x00", b"\x00\x01")):
                    f.seek(13)
                    f.write(bytes([1]) + dpi.to_bytes(2, "big") + dpi.to_bytes(2, "big"))
        except OSError:
            pass

    def cancel(self):
        self._cancelled = True
        job = self.job
        if job:
            try:
                requests.delete(self.url + job, timeout=5, verify=False)
            except requests.RequestException:
                pass


def escl_probe(url, timeout=1.5):
    """The scanner at this address, as a way to it, or None."""
    try:
        e = Escl(url, timeout)
        caps = e.caps()
    except EsclError:
        return None
    # ipp-usb, which carries eSCL over the cable, listens on this computer under the name localhost
    return {"id": url, "url": url, "name": caps["name"], "backend": "direct", "key": _model_key(caps["name"]), "uuid": caps["uuid"],
            "link": "usb" if urlparse(url).hostname == "localhost" else "net",
            "feeder": caps["feeder"] is not None, "duplex": bool(caps["feeder"] and caps["feeder"]["duplex"])}


def escl_find(seconds=3.0):
    """The scanners that can be asked directly: over USB through ipp-usb (Linux: it listens on
    this computer, ports 60000 and up), and on the network (they announce themselves). A scanner
    plugged in and on the network is found twice: two ways to one scanner, the cable first."""
    found, seen = [], set()

    def add(url):
        way = escl_probe(url)
        if way and url not in seen:
            seen.add(url)
            found.append(way)

    if sys.platform.startswith("linux"):
        import socket
        for port in range(60000, 60016):
            try:
                socket.create_connection(("127.0.0.1", port), 0.15).close()
            except OSError:
                continue
            add(f"http://localhost:{port}")
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return found
    urls = []

    class Heard:
        def add_service(self, zc, kind, name):
            info = zc.get_service_info(kind, name, 1500)
            if info and info.parsed_addresses():
                address = next((a for a in info.parsed_addresses() if ":" not in a), info.parsed_addresses()[0])
                root = (info.properties.get(b"rs") or b"eSCL").decode("utf-8", "replace").strip("/")
                host = f"[{address}]" if ":" in address else address
                urls.append(("https" if kind.startswith("_uscans") else "http", host, info.port, root))

        update_service = remove_service = lambda self, *a: None

    try:
        zc = Zeroconf()
    except OSError:
        return found
    try:
        heard = Heard()
        browsers = [ServiceBrowser(zc, kind, heard) for kind in ("_uscan._tcp.local.", "_uscans._tcp.local.")]
        time.sleep(seconds)
        del browsers
    finally:
        zc.close()
    for scheme, host, port, root in sorted(set(urls)):      # http before https
        if root.lower() == "escl" and not host.startswith("127."):
            add(f"{scheme}://{host}:{port}")
    return found
