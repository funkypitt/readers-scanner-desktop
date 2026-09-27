#!/usr/bin/env python3
"""A stand-in for a scanner that speaks eSCL (AirScan), for the tests: the answers of an HP
ScanJet Pro 4500 fn1 (its capabilities as it gave them on 2026-09-27, shortened), pages taken
from the same folder as tests/fake_naps2.py:

  $FAKE_SCANNER/feeder/*.jpg  the sheets in the feeder (both sides: one file per side)
  $FAKE_SCANNER/glass.jpg     what lies on the glass
  $FAKE_SCANNER/nofeeder      the scanner has no feeder
  $FAKE_SCANNER/busy          a number: « 503 » that many times before a scan starts
  $FAKE_SCANNER/jam, multipick  what the feeder says went wrong
  $FAKE_SCANNER/asreal        the pictures as the real scanner sends them from its feeder: lightly
                              compressed, and announcing 3508 lines where they hold 3472
  $FAKE_SCANNER/slow          seconds per page
  $FAKE_SCANNER/direct.txt    every request, for the tests to read

    url, stop = fake_escl.serve(folder)        # in the test's own process
"""
import http.server, io, os, re, threading, time

CAPS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
 <pwg:Version>2.62</pwg:Version>
 <pwg:MakeAndModel>HP ScanJet Pro 4500 fn1</pwg:MakeAndModel>
 <scan:UUID>A9D95434-0000-4b46-89EC-FAKE00000001</scan:UUID>
 <scan:Platen><scan:PlatenInputCaps>
  <scan:MinWidth>8</scan:MinWidth><scan:MaxWidth>2550</scan:MaxWidth><scan:MinHeight>8</scan:MinHeight><scan:MaxHeight>4200</scan:MaxHeight>
  <scan:SettingProfiles><scan:SettingProfile>
   <scan:ColorModes><scan:ColorMode>BlackAndWhite1</scan:ColorMode><scan:ColorMode>Grayscale8</scan:ColorMode><scan:ColorMode>RGB24</scan:ColorMode></scan:ColorModes>
   <scan:DocumentFormats><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat><pwg:DocumentFormat>application/pdf</pwg:DocumentFormat><scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt></scan:DocumentFormats>
   <scan:SupportedResolutions><scan:DiscreteResolutions>
    %(dpi)s
   </scan:DiscreteResolutions></scan:SupportedResolutions>
  </scan:SettingProfile></scan:SettingProfiles>
 </scan:PlatenInputCaps></scan:Platen>
 %(adf)s
</scan:ScannerCapabilities>
"""
ADF = """<scan:Adf>
  <scan:AdfSimplexInputCaps>
   <scan:MinWidth>8</scan:MinWidth><scan:MaxWidth>2550</scan:MaxWidth><scan:MinHeight>8</scan:MinHeight><scan:MaxHeight>36600</scan:MaxHeight>
   <scan:SettingProfiles><scan:SettingProfile>
    <scan:ColorModes><scan:ColorMode>Grayscale8</scan:ColorMode><scan:ColorMode>RGB24</scan:ColorMode></scan:ColorModes>
    <scan:DocumentFormats><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat></scan:DocumentFormats>
    <scan:SupportedResolutions><scan:DiscreteResolutions>%(dpi)s</scan:DiscreteResolutions></scan:SupportedResolutions>
   </scan:SettingProfile></scan:SettingProfiles>
  </scan:AdfSimplexInputCaps>
  <scan:AdfDuplexInputCaps>
   <scan:MinWidth>8</scan:MinWidth><scan:MaxWidth>2550</scan:MaxWidth><scan:MinHeight>8</scan:MinHeight><scan:MaxHeight>4200</scan:MaxHeight>
   <scan:SettingProfiles><scan:SettingProfile>
    <scan:ColorModes><scan:ColorMode>Grayscale8</scan:ColorMode><scan:ColorMode>RGB24</scan:ColorMode></scan:ColorModes>
    <scan:DocumentFormats><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat></scan:DocumentFormats>
    <scan:SupportedResolutions><scan:DiscreteResolutions>%(dpi)s</scan:DiscreteResolutions></scan:SupportedResolutions>
   </scan:SettingProfile></scan:SettingProfiles>
  </scan:AdfDuplexInputCaps>
  <scan:FeederCapacity>50</scan:FeederCapacity>
  <scan:AdfOptions><scan:AdfOption>DetectPaperLoaded</scan:AdfOption><scan:AdfOption>Duplex</scan:AdfOption><scan:AdfOption>MultipickDetection</scan:AdfOption></scan:AdfOptions>
 </scan:Adf>"""
DPI = "".join(f"<scan:DiscreteResolution><scan:XResolution>{d}</scan:XResolution><scan:YResolution>{d}</scan:YResolution></scan:DiscreteResolution>" for d in (75, 150, 200, 300, 600))


def serve(root, port=0):
    jobs = {}                  # number → {"pages": [...], "feeder": bool, "dpi": int}
    state = {"n": 14}

    def here(name):
        return os.path.join(root, name)

    def number(name, default=0.0):
        try:
            return float(open(here(name)).read())
        except (OSError, ValueError):
            return default

    def sheets():
        return sorted(os.path.join(here("feeder"), f) for f in os.listdir(here("feeder"))) if os.path.isdir(here("feeder")) else []

    class Scanner(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def note(self, words):
            with open(here("direct.txt"), "a", encoding="utf-8") as f:
                f.write(words + "\n")

        def answer(self, code, body=b"", kind="text/xml", headers=()):
            self.send_response(code)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.note("GET " + self.path)
            if self.path == "/eSCL/ScannerCapabilities":
                adf = "" if os.path.exists(here("nofeeder")) else ADF % {"dpi": DPI}
                return self.answer(200, (CAPS % {"dpi": DPI, "adf": adf}).encode())
            if self.path == "/eSCL/ScannerStatus":
                adf = ("ScannerAdfJam" if os.path.exists(here("jam")) else "ScannerAdfMultipickDetected" if os.path.exists(here("multipick"))
                       else "ScannerAdfLoaded" if sheets() else "ScannerAdfEmpty")
                body = ('<?xml version="1.0" encoding="UTF-8"?><scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" '
                        'xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"><pwg:Version>2.5</pwg:Version><pwg:State>Idle</pwg:State>'
                        + ("" if os.path.exists(here("nofeeder")) else f"<scan:AdfState>{adf}</scan:AdfState>") + "</scan:ScannerStatus>")
                return self.answer(200, body.encode())
            m = re.fullmatch(r"/eSCL/ScanJobs/(\d+)/NextDocument", self.path)
            job = jobs.get(int(m.group(1))) if m else None
            if not job or job.get("gone"):
                return self.answer(404)
            if job["feeder"]:
                left = sheets()
                if not left or job["given"] >= job["most"]:
                    return self.answer(404)
                page = left[0]
            else:
                if job["given"] or not os.path.exists(here("glass.jpg")):
                    return self.answer(404)
                page = here("glass.jpg")
            time.sleep(number("slow", 0.05))
            if job.get("gone"):
                return self.answer(404)
            data = open(page, "rb").read()
            if job["dpi"] != 300:
                from PIL import Image
                im = Image.open(io.BytesIO(data))
                out = io.BytesIO()
                im.resize((im.width * job["dpi"] // 300, im.height * job["dpi"] // 300)).save(out, "JPEG", quality=85)
                data = out.getvalue()
            if os.path.exists(here("asreal")):
                from PIL import Image
                im = Image.open(io.BytesIO(data)).convert("RGB")
                out = io.BytesIO()
                im.crop((0, 0, im.width, 3472)).save(out, "JPEG", quality=97, subsampling=2, restart_marker_rows=1)
                data = bytearray(out.getvalue())
                at = data.find(b"\xff\xc0")
                data[at + 5:at + 7] = (3508).to_bytes(2, "big")
                data = bytes(data)
            if job["feeder"]:
                os.remove(page)
            job["given"] += 1
            self.answer(200, data, "image/jpeg")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
            source = (re.search(r"<pwg:InputSource>(\w+)<", body) or [None, "Platen"])[1]
            duplex = "<scan:Duplex>true<" in body
            dpi = int((re.search(r"<scan:XResolution>(\d+)<", body) or [None, "300"])[1])
            self.note(f"POST {self.path} {source}{' duplex' if duplex else ''} {dpi}")
            if self.path != "/eSCL/ScanJobs":
                return self.answer(404)
            busy = int(number("busy"))
            if busy > 0:
                open(here("busy"), "w").write(str(busy - 1))
                return self.answer(503)
            if source == "Feeder" and (os.path.exists(here("nofeeder")) or not sheets()):
                return self.answer(409)
            state["n"] += 1
            jobs[state["n"]] = {"feeder": source == "Feeder", "dpi": dpi, "given": 0, "most": 10 ** 6}
            # the address a real scanner gives is one only it knows
            self.answer(201, headers=(("Location", f"http://ScanjetPro4500.local./eSCL/ScanJobs/{state['n']}"),))

        def do_DELETE(self):
            self.note("DELETE " + self.path)
            m = re.fullmatch(r"/eSCL/ScanJobs/(\d+)", self.path)
            if m and int(m.group(1)) in jobs:
                jobs[int(m.group(1))]["gone"] = True
                return self.answer(200)
            self.answer(404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Scanner)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop():
        server.shutdown()
        server.server_close()
    return f"http://127.0.0.1:{server.server_address[1]}", stop
