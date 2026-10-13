#!/usr/bin/env python3
"""
plot_ul_iperf.py - UL iperf goodput overlays for the LOW-anchor 2-UE sweep.

UE1 = Path-B NeRF @28 dB anchor (fading), the SAME in every run.
UE2 = constant-SNR competitor, swept 8 / 10 / 20 / 25 dB.

Two overlay figures:
  1. multiue_lowSNR_nerf_ul_overlay.png - UE1 (NeRF) UL goodput vs time, one line
     per UE2 dB run (how PF shrinks/grows UE1's share vs competitor strength).
  2. multiue_lowSNR_cell_ul_overlay.png - cell UL total (UE1+UE2) vs time.

These runs use the FIXED iperf port, so each UE's UL lands in its own file
(iperf_ul_ue1.txt = UE1, iperf_ul_ue2.txt = UE2). The parse still keys by source
port + tunnel IP (10.0.0.2 = UE1, 10.0.0.3 = UE2) so it is robust either way.
Baseline = single-UE Path-B NeRF @~30 dB (no 28 dB single-UE run exists), shown
as an approximate no-competitor reference.
"""

import os
import re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
# (folder, UE2 dB, color) - near-anchor (red) -> far-below (blue)
RUNS = [
    ("ue1nerf28_ue2const25db", 25, "#d62728"),
    ("ue1nerf28_ue2const20db", 20, "#ff7f0e"),
    ("ue1nerf28_ue2const10db", 10, "#2ca02c"),
    ("ue1nerf28_ue2const8db",   8, "#1f77b4"),
]
# No single-UE baseline here: no 28 dB single-UE NeRF run exists, and the 30 dB
# one collapsed at ~73 s (misleading mean). Run one at --snr 28 if a reference is
# wanted. Set to a (folder, label) tuple to overlay it.
BASELINE = None
WARMUP_S = 10.0       # skip the ramp when reporting mean goodput
SMOOTH_S = 3          # rolling-mean window (s) for the overlay lines

HDR = re.compile(r"\[\s*(\d+)\]\s+local\s+\S+\s+port\s+\d+\s+connected with\s+([\d.]+)\s+port\s+(\d+)")
DAT = re.compile(r"\[\s*(\d+)\]\s+([\d.]+)-\s*([\d.]+)\s+sec.*?([\d.]+)\s+([KMGT]?)bits/sec")
UNIT = {"K": 1e-3, "M": 1.0, "G": 1e3, "T": 1e6, "": 1e-6}   # -> Mbps

ALLOWED_IP = lambda ip: ip == "192.168.70.134" or ip.startswith("10.0.0.")


def parse_file(path):
    """Parse ONE iperf-server file -> (t, mbps) for its dominant real flow, or
    None. Each file holds a single UE (fixed port), but iperf double-reports the
    same flow as two stream IDs ([1]=NAT IP, [2]=tunnel IP) that share a source
    port, so we key by source PORT (de-duping the NAT report) and pick the port
    with the most samples. cid->port is LOCAL to this file (stream IDs collide
    across files, so they must not share a dict)."""
    if not os.path.exists(path):
        return None
    conn_ip, conn_port, port_series = {}, {}, {}
    with open(path, errors="ignore") as f:
        for line in f:
            h = HDR.search(line)
            if h:
                cid, ip, port = h.group(1), h.group(2), h.group(3)
                conn_port[cid] = port
                conn_ip.setdefault(port, set()).add(ip)
                continue
            d = DAT.search(line)
            if not d:
                continue
            cid, t0, t1 = d.group(1), float(d.group(2)), float(d.group(3))
            if not (0.5 <= t1 - t0 <= 1.5):
                continue
            port = conn_port.get(cid)
            if port is None:
                continue
            mbps = float(d.group(4)) * UNIT[d.group(5)]
            s = port_series.setdefault(port, {})
            s[t1] = max(s.get(t1, 0.0), mbps)
    ports = [p for p in port_series if any(ALLOWED_IP(ip) for ip in conn_ip.get(p, ()))]
    if not ports:
        return None
    best = max(ports, key=lambda p: len(port_series[p]))   # dominant flow
    t = np.array(sorted(port_series[best]))
    return t, np.array([port_series[best][x] for x in t])


def parse_run(folder):
    """-> {'ue1': (t,mbps) or None, 'total': (t,mbps) or None}. UE1 = ue1.txt,
    UE2 = ue2.txt (fixed-port runs give one clean flow per file)."""
    ue1 = parse_file(os.path.join(BASE, folder, "iperf_ul_ue1.txt"))
    ue2 = parse_file(os.path.join(BASE, folder, "iperf_ul_ue2.txt"))
    flows = [s for s in (ue1, ue2) if s is not None]
    if not flows:
        return {"ue1": ue1, "total": None}
    grids = [s[0] for s in flows]
    tg = np.union1d(*grids) if len(grids) > 1 else grids[0]
    tot = np.zeros_like(tg, dtype=float)
    for t, y in flows:
        tot += np.interp(tg, t, y, left=0, right=0)
    return {"ue1": ue1, "total": (tg, tot)}


def smooth(ser, win_s=SMOOTH_S):
    t, y = ser
    if len(y) < 2 or win_s <= 1:
        return t, y
    k = max(1, int(round(win_s)))
    return t, np.convolve(y, np.ones(k) / k, mode="same")


def win_mean(ser):
    """Mean goodput over the ACTIVE delivery window: from WARMUP_S to the last
    second with non-zero goodput. Excludes the attach ramp and any dead tail so
    runs that end/collapse early (e.g. UE1 starved at 159 s @ UE2=25 dB) stay
    comparable to full-length runs."""
    t, y = ser
    nz = np.nonzero(y > 0.05)[0]
    if len(nz) == 0:
        return float("nan")
    m = (t >= WARMUP_S) & (t <= t[nz[-1]])
    return float(np.mean(y[m])) if m.any() else float("nan")


parsed = [(db, color, parse_run(folder)) for folder, db, color in RUNS]
base_r = parse_run(BASELINE[0]) if BASELINE else {"ue1": None}
base_mean = win_mean(base_r["ue1"]) if base_r["ue1"] else float("nan")


def overlay(key, title, fname, ylabel):
    fig, ax = plt.subplots(figsize=(11, 6))
    bser = base_r["ue1"]
    if bser is not None:
        bts, bys = smooth(bser)
        ax.plot(bser[0], bser[1], color="k", lw=0.6, alpha=0.18)
        ax.plot(bts, bys, color="k", lw=2.2, ls="--",
                label=f"{BASELINE[1]}   (mean {base_mean:.1f} Mbps)", zorder=5)
    rows = []
    for db, color, r in parsed:
        ser = r[key]
        lab = f"UE2 @ {db} dB"
        if ser is None:
            rows.append((db, float("nan")))
            lab += " (no data / no-attach)"
            ax.plot([], [], color=color, lw=2.0, label=lab)
            continue
        ts, ys = smooth(ser)
        m = win_mean(ser)
        ax.plot(ser[0], ser[1], color=color, lw=0.6, alpha=0.20)
        ax.plot(ts, ys, color=color, lw=2.0, label=f"{lab}   (mean {m:.1f} Mbps)")
        rows.append((db, m))
    ax.set_xlabel("iperf time (s)")
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9, loc="upper left", title="competitor strength")
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    out = os.path.join(BASE, fname)
    fig.savefig(out, dpi=140)
    print("wrote", out)
    return rows


r1 = overlay("ue1", "UE1 NeRF (Path B @28 dB) UL goodput vs competitor strength",
             "multiue_lowSNR_nerf_ul_overlay.png", "UE1 NeRF UL goodput (Mbps)")
r2 = overlay("total", "Cell UL total (UE1 NeRF + UE2 const) @28 dB anchor",
             "multiue_lowSNR_cell_ul_overlay.png", "cell UL total goodput (Mbps)")

print("\nmean UL goodput (Mbps, active window t >= %gs):" % WARMUP_S)
print("%-22s %14s %14s" % ("scenario", "UE1 NeRF", "cell total"))
if BASELINE and base_r["ue1"] is not None:
    print("%-22s %14.2f %14.2f" % (BASELINE[1], base_mean, base_mean))
m1, m2 = dict(r1), dict(r2)
for folder, db, _ in RUNS:
    print("%-22s %14.2f %14.2f" % (f"vs UE2 @ {db} dB", m1.get(db, float('nan')), m2.get(db, float('nan'))))
