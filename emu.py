# -*- coding: utf-8 -*-
"""Unified remote control helper for MuMu instance 0.
Reads the mumu-cli / winshot command from E:\soul\cmd.txt and runs it.
Must be launched via PsExec -i 1 so it executes in the interactive session
where the player window + NemuShell live. Output -> E:\soul\emu_out.txt.
"""
import subprocess, os

BASE = r"E:\soul"
cmd = open(os.path.join(BASE, "cmd.txt"), encoding="utf-8").read().strip()
r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
out = "RC=%d\nCMD=%r\nSTDOUT:\n%s\nSTDERR:\n%s" % (r.returncode, cmd, r.stdout, r.stderr)
open(os.path.join(BASE, "emu_out.txt"), "w", encoding="utf-8").write(out)
print(out)
