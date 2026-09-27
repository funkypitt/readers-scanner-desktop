#!/usr/bin/env python3
"""With the real NAPS2 installed and no scanner (a build machine): it is found, its version is
read, it lists no scanner, and what it answers when asked for a scanner that does not exist is
understood. Run: python3 tests/real_naps2.py"""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import readers_scanner as rs

failed = []


def check(label, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    cond or failed.append(label)


tmp = tempfile.mkdtemp(prefix="rs-real-")
n = rs.Naps2(tmp)
check("NAPS2 is found where its installer puts it, and answers", bool(n.cmd) and n.version not in (None, "?"), f"{n.cmd} {n.version}")
if n.cmd:
    t0 = time.time()
    devs = n.devices()
    check("no scanner on a build machine, and no sentence taken for one", devs == [], f"{devs} in {time.time() - t0:.1f} s")
    r = rs.scan_pages(n, {"format": "a"}, "auto", tmp + "/in")
    check("a scan without scanner: said so", r["error"] == "nodevice", str(r))
    for driver in n.drivers:
        t0 = time.time()
        files, code, words = n.scan({"id": None, "name": "No Such Scanner 3000", "driver": driver, "key": "x"}, "glass", "A4", tmp + "/in")
        check(f"{driver}: asked for a scanner that does not exist, NAPS2's answer is understood", files == [] and code in ("notfound", "offline", "nodevice", "nosane", "driver"),
              f"{code}: « {words} » in {time.time() - t0:.1f} s")
    if n.drivers[0] != "sane" and os.path.exists(os.path.join(n.data, "profiles.xml")) is False:
        n._profile({"id": "nowhere", "name": "No Such Scanner 3000", "driver": n.driver}, "glass", "A4", False)
        import subprocess
        out = subprocess.run(n.cmd + ["-p", "readers-scanner", "-o", os.path.join(tmp, "p$(nnnn).jpg"), "-f", "-v"], capture_output=True, timeout=120, env=n._env())
        words = rs.said(out.stdout).strip().splitlines()
        check("our own profile is the one NAPS2 reads (its data folder is ours)", not any("profile is unavailable" in l for l in words), " / ".join(words)[-300:])
print("FAILED: " + ", ".join(failed) if failed else "all good with the real NAPS2")
sys.exit(1 if failed else 0)
