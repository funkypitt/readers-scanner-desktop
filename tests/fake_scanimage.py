#!/usr/bin/env python3
"""A stand-in for `scanimage -f …`: the devices of $FAKE_SCANNER/devices.txt."""
import os, sys, time
time.sleep(0.2)
try:
    for l in open(os.path.join(os.environ.get("FAKE_SCANNER", ""), "devices.txt"), encoding="utf-8"):
        if l.strip():
            sys.stdout.write(l if l.endswith("\n") else l + "\n")
except OSError:
    pass
