#!/usr/bin/env python3
"""Writes parts/18_naps2_words.py: what NAPS2 says when a scan fails, in every language it
speaks. On Windows NAPS2 answers in the system's language whatever it is asked, so its English
sentences are not enough there. Source: NAPS2.Sdk/Lang/Resources/SdkResources*.resx at the tag
given (default v8.2.1). Needs the gh command. Run: tools/naps2_messages.py [TAG]"""
import base64, json, os, re, subprocess, sys
import xml.etree.ElementTree as ET

TAG = sys.argv[1] if len(sys.argv) > 1 else "v8.2.1"
HERE = os.path.dirname(os.path.abspath(__file__))
DIR = "NAPS2.Sdk/Lang/Resources"
# resource name → our word for it (parts/20_engine.py, error_text)
KEYS = {"NoPagesInFeeder": "empty", "NoFeederSupport": "nofeeder", "NoDuplexSupport": "noduplex", "DeviceNotFound": "notfound",
        "DeviceOffline": "offline", "DeviceBusy": "busy", "DeviceCoverOpen": "cover", "DevicePaperJam": "jam", "DeviceWarmingUp": "warming",
        "DeviceCommunicationFailure": "comm", "SaneNotAvailable": "nosane", "NoDeviceSelected": "notfound", "UnknownDriverError": "driver",
        "WorkerCrash": "driver"}


def gh(path):
    return subprocess.run(["gh", "api", f"repos/cyanfish/naps2/contents/{path}?ref={TAG}"], capture_output=True, text=True, check=True).stdout


def strings(name):
    raw = base64.b64decode(json.loads(gh(f"{DIR}/{name}"))["content"]).decode("utf-8-sig")
    return {d.get("name"): (d.findtext("value") or "").strip() for d in ET.fromstring(raw).iter("data")}


def norm(s):
    return re.sub(r"\s+", " ", s).strip().rstrip(".。").lower()


files = sorted(f["name"] for f in json.loads(gh(DIR)) if re.fullmatch(r"SdkResources(\.[\w-]+)?\.resx", f["name"]))
english = strings("SdkResources.resx")
missing = [k for k in KEYS if k not in english]
if missing:
    print("names in NAPS2's resources:", ", ".join(sorted(english)))
    sys.exit("not found there: " + ", ".join(missing))
words, langs = {}, []
for name in files:
    lang = name[len("SdkResources."):-len(".resx")] or "en"
    table = english if lang == "en" else strings(name)
    langs.append(lang)
    for key, code in KEYS.items():
        text = norm(table.get(key) or "")
        if text and words.setdefault(text, code) != code:
            # a translation that says two things with one sentence: the first meaning is kept
            print(f"  {lang}: « {text} » stands for {words[text]} and for {code}; kept: {words[text]}")
out = os.path.join(HERE, "..", "parts", "18_naps2_words.py")
with open(out, "w", encoding="utf-8") as f:
    f.write(f"# Written by tools/naps2_messages.py from NAPS2 {TAG} ({len(langs)} languages): do not edit by hand.\n")
    f.write("# What NAPS2 says when a scan fails (lower case, no final stop) → our word for it.\n")
    f.write("NAPS2_WORDS = {\n")
    for text in sorted(words):
        f.write(f"    {text!r}: {words[text]!r},\n")
    f.write("}\n\n\n")
print(f"{len(words)} sentences in {len(langs)} languages → {os.path.relpath(out)}")
for k in KEYS:
    print(f"  {k}: {english[k]}")
