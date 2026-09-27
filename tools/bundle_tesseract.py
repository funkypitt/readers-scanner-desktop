#!/usr/bin/env python3
"""Gathers Tesseract as the Windows and macOS builds carry it: the program, the libraries it
needs and no others, the models for the orientation and for English (the other languages are
fetched by the app, the most accurate ones, when they are first needed).

    tools/bundle_tesseract.py PREFIX OUT

PREFIX is a conda environment holding conda-forge's tesseract (built for old systems too:
macOS 10.13 on Intel, 11 on Apple Silicon). OUT gets:

    Windows   tesseract.exe, *.dll, tessdata/
    macOS     bin/tesseract, lib/*.dylib, tessdata/
    both      LICENSES.txt: the packages the files come from, and their licences

It ends by running what it gathered, away from PREFIX: a bundle that does not start fails here.
Windows needs `pip install pefile`."""
import glob, json, os, shutil, subprocess, sys, urllib.request

PREFIX, OUT = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
WINDOWS = sys.platform == "win32"
MODELS = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/%s.traineddata"
shutil.rmtree(OUT, ignore_errors=True)
taken = {}                   # name as asked for (lower case on Windows) → the file copied


def windows_needs(path):
    import pefile
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"], pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])] + [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
    pe.close()
    return names


def windows_find(name):
    for folder in (os.path.join(PREFIX, "Library", "bin"), PREFIX, os.path.join(PREFIX, "Library", "mingw-w64", "bin")):
        for f in os.listdir(folder) if os.path.isdir(folder) else ():
            if f.lower() == name.lower():
                return os.path.join(folder, f)
    return None              # Windows' own


def mac_needs(path):
    out = subprocess.run(["otool", "-L", path], capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    return [l.strip().split(" (")[0] for l in out]


def mac_find(name):
    if name.startswith(("/usr/lib/", "/System/")):
        return None          # macOS' own
    p = os.path.join(PREFIX, "lib", os.path.basename(name)) if name.startswith(("@rpath/", "@loader_path/")) else name
    return os.path.realpath(p) if os.path.exists(p) else sys.exit(f"needed and not found: {name}")


def gather(path, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(path, dest)
    os.chmod(dest, 0o755)
    for name in (windows_needs if WINDOWS else mac_needs)(path):
        key = name.lower() if WINDOWS else os.path.basename(name)
        if key in taken:
            continue
        found = (windows_find if WINDOWS else mac_find)(name)
        if not found:
            continue
        taken[key] = found
        gather(found, os.path.join(OUT, os.path.basename(name)) if WINDOWS else os.path.join(OUT, "lib", os.path.basename(name)))


if WINDOWS:
    exe = os.path.join(OUT, "tesseract.exe")
    gather(os.path.join(PREFIX, "Library", "bin", "tesseract.exe"), exe)
else:
    exe = os.path.join(OUT, "bin", "tesseract")
    gather(os.path.realpath(os.path.join(PREFIX, "bin", "tesseract")), exe)

os.makedirs(os.path.join(OUT, "tessdata"))
for lang in ("eng", "osd"):
    urllib.request.urlretrieve(MODELS % lang, os.path.join(OUT, "tessdata", lang + ".traineddata"))

# whose files these are
files = {os.path.realpath(exe)} | {os.path.realpath(p) for p in taken.values()}
origin = {os.path.realpath(os.path.join(PREFIX, "Library", "bin", "tesseract.exe") if WINDOWS else os.path.join(PREFIX, "bin", "tesseract"))} | files
packages = []
for meta in sorted(glob.glob(os.path.join(PREFIX, "conda-meta", "*.json"))):
    m = json.load(open(meta, encoding="utf-8"))
    if any(os.path.realpath(os.path.join(PREFIX, f)) in origin for f in m.get("files", [])):
        packages.append((m["name"], m["version"], m.get("license") or "?"))
with open(os.path.join(OUT, "LICENSES.txt"), "w", encoding="utf-8") as f:
    f.write("Tesseract and the libraries it needs, as built by conda-forge (https://conda-forge.org).\n"
            "Each package's own licence text is in its source, linked from https://anaconda.org/conda-forge/<name>.\n\n")
    for name, version, licence in packages:
        f.write(f"{name} {version}: {licence}\n")
    f.write("\ntessdata/eng.traineddata, tessdata/osd.traineddata: https://github.com/tesseract-ocr/tessdata_fast (Apache-2.0)\n")

size = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(OUT) for f in fs)
print(f"{len(taken)} libraries from {len(packages)} packages, {size / 1e6:.0f} MB in {OUT}")
for name, version, licence in packages:
    print(f"  {name} {version}: {licence}")

# does it run, on its own?
env = {k: v for k, v in os.environ.items() if k.upper() not in ("PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "CONDA_PREFIX")}
env["PATH"] = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32") if WINDOWS else "/usr/bin:/bin"
env["TESSDATA_PREFIX"] = os.path.join(OUT, "tessdata")
run = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, env=env)
print((run.stdout + run.stderr).strip())
if run.returncode != 0 or "eng" not in run.stdout + run.stderr or "osd" not in run.stdout + run.stderr:
    sys.exit(f"what was gathered does not run (exit code {run.returncode})")
print(subprocess.run([exe, "--version"], capture_output=True, text=True, env=env).stdout.splitlines()[0])
