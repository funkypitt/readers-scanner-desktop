#!/usr/bin/env python3
"""A stand-in for `naps2 console`, for the tests: same options, same words (NAPS2 8.2.1's
English messages), pages taken from a folder instead of a scanner.

  $FAKE_SCANNER/devices.txt   one line per way to a scanner: id<TAB>vendor<TAB>model<TAB>type
  $FAKE_SCANNER/feeder/*.jpg  the sheets in the feeder (taken away by a scan)
  $FAKE_SCANNER/glass.jpg     what lies on the glass
  $FAKE_SCANNER/offline       (a file) the scanner does not answer; "offline-<backend>": on that driver only
  $FAKE_SCANNER/nofeeder      the scanner has no feeder
  $FAKE_SCANNER/slow          seconds per page (a number in the file)
  $FAKE_SCANNER/log.txt       every call, for the tests to read
"""
import os, re, shutil, sys, time

root = os.environ.get("FAKE_SCANNER", "")
args = sys.argv[1:]
if args and args[0] == "console":
    args = args[1:]


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def devices():
    try:
        return [l.rstrip("\n").split("\t") for l in open(os.path.join(root, "devices.txt"), encoding="utf-8") if l.strip()]
    except OSError:
        return []


def display(d):
    backend = d[0].split(":")[0]
    if backend == "escl":
        return f"{d[1]} {d[2]} ({d[0]})"
    if backend == "airscan":
        return f"{d[2]} ({backend}:{d[3]})"
    return f"{d[2]} ({backend})"


with open(os.path.join(root, "log.txt"), "a", encoding="utf-8") as f:
    f.write(" ".join(args) + "\n")

if "--help" in args:
    print("naps2 8.2.1+fake\nCopyright 2009-2024 NAPS2 Contributors")
    sys.exit(0)
if "--listdevices" in args:
    time.sleep(0.2)
    for d in devices():
        print(display(d))
    sys.exit(0)

source, device_id, name = "glass", None, None
if "-p" in args or "--profile" in args:
    data = os.environ.get("NAPS2_TEST_DATA", "")
    try:
        xml = open(os.path.join(data, "profiles.xml"), encoding="utf-8").read()
    except OSError:
        print("The specified profile is unavailable or ambiguous."); sys.exit(0)
    if f"<DisplayName>{opt('-p') or opt('--profile')}</DisplayName>" not in xml:
        print("The specified profile is unavailable or ambiguous."); sys.exit(0)
    device_id = re.search(r"<ID>(.*?)</ID>", xml).group(1).replace("&amp;", "&")
    source = re.search(r"<PaperSource>(.*?)</PaperSource>", xml).group(1).lower()
else:
    name = opt("--device")
    source = opt("--source", "glass")
    time.sleep(0.3)          # NAPS2 looks for the scanners first
    match = [d for d in devices() if name and name.lower() in display(d).lower()]
    if not match:
        print("The selected scanner could not be found."); sys.exit(0)
    device_id = match[0][0]

out = opt("-o") or opt("--output")
print("Beginning scan...")
print("Starting scan 1 of 1...", flush=True)
backend = (device_id or "").split(":")[0]
known = [d[0] for d in devices()]


def fail(words):
    print(words)
    print("0 page(s) scanned.")
    print("No scanned pages to export.")
    sys.exit(0)


if os.path.exists(os.path.join(root, "offline")) or os.path.exists(os.path.join(root, "offline-" + backend)) or device_id not in known:
    fail("The selected scanner is offline.")
pages = []
if source in ("feeder", "duplex"):
    if os.path.exists(os.path.join(root, "nofeeder")):
        fail("The selected scanner does not support using a feeder. If your scanner does have a feeder, try using a different driver.")
    feeder = os.path.join(root, "feeder")
    pages = sorted(os.path.join(feeder, f) for f in os.listdir(feeder)) if os.path.isdir(feeder) else []
    if not pages:
        fail("No pages are in the feeder.")
else:
    glass = os.path.join(root, "glass.jpg")
    if os.path.exists(glass):
        pages = [glass]
try:
    slow = float(open(os.path.join(root, "slow")).read())
except (OSError, ValueError):
    slow = 0.05
for i, p in enumerate(pages):
    time.sleep(slow)
    print(f"Scanned page {i + 1}.", flush=True)
print(f"{len(pages)} page(s) scanned.")
if not pages:
    print("No scanned pages to export."); sys.exit(0)
print("Exporting...")
for i, p in enumerate(pages):
    dst = out.replace("$(nnnn)", f"{i + 1:04d}")
    shutil.copyfile(p, dst)
    print(f"Exporting image {i + 1} of {len(pages)}...")
    if source in ("feeder", "duplex"):
        os.remove(p)
print(f"Finished saving images to {os.path.dirname(out)}")
