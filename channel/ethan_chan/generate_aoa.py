#!/usr/bin/env python3
"""generate_aoa.py — write the flat unit-tap UL channel for the AoA experiment.

aoa_tti_experiment.py (delay 0) stages channel_aoa_flat_real.txt verbatim as the
UL signal channel (channel_real.txt) and a matching all-zeros imag. The channel is
a flat, unit-magnitude, zero-phase SISO tap (|H|=1): it carries NO angle info by
itself — the per-antenna AoA phase pi*a*sin(theta0) is injected in the gNB rx path
(simulator.c, reading channel/aoa_steer.txt). So this file just needs to be a long
column of 1.0 (one tap per slot), longer than any run consumes.

  Run: python3 generate_aoa.py
"""
import os

ETHAN_CHAN_DIR = "/home/ways_lab/repos/Tiny_Twin/channel/ethan_chan"
OUT = os.path.join(ETHAN_CHAN_DIR, "channel_aoa_flat_real.txt")
NROWS = 600000   # ~300 s at 2000 slots/s; flat, so any run length is covered

os.makedirs(ETHAN_CHAN_DIR, exist_ok=True)
with open(OUT, "w") as f:
    f.write("1.000000\n" * NROWS)
print(f"wrote flat unit-tap channel: {NROWS} rows -> {OUT}")
