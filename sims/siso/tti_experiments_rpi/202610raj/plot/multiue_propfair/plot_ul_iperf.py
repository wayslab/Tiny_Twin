#!/usr/bin/env python3
"""
plot_ul_iperf.py - UL iperf goodput overlays for the 2-UE prop-fair sweep.

UE1 = Path-B NeRF channel @35 dB (fading), the SAME in every run.
UE2 = a constant-SNR competitor, swept 35 / 20 / 10 / 5 dB.

Two overlay figures:
  1. multiue_nerf_ul_overlay.png - UE1 (NeRF) UL goodput vs time, one line per
     UE2 dB run. Shows how the PF scheduler shrinks UE1's share as the competitor
     gets stronger / grows it as the competitor weakens.
  2. multiue_cell_ul_overlay.png - cell UL total (UE1+UE2) vs time, one line per
     run. Shows aggregate cell efficiency vs the competitor's strength.

Data note: these runs predate the iperf per-UE port fix, so BOTH UEs' UL land in
iperf_ul_ue2.txt (server :5213). We disaggregate by **source port** (each client
has a unique ephemeral port, double-reported by NAT as 192.168.70.134:PORT and as
the UE tunnel IP 10.0.0.2/3:PORT). Label by tunnel IP where present, else by
complement. The 5 dB run has only 1 RNTI (UE2 failed to attach before the warmup
fix) -> its single real flow is UE1, and cell total == UE1 there.
"""

import os
import re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
# (folder, UE2 dB, color) - strong competitor (red) -> weak/absent (blue)
RUNS = [
    ("ue1nerf35_ue2const35db", 35, "#d62728"),
    ("ue1nerf35_ue2const20db", 20, "#ff7f0e"),
    ("ue1nerf35_ue2const10db", 10, "#2ca02c"),
    ("ue1nerf35_ue2const5db",   5, "#1f77b4"),
]
# single-UE Path-B NeRF @35 dB UDP baseline (no competitor) - the reference UE1
# would hit if it had the whole cell. Relative to BASE (this script's dir).
BASELINE = ("../snr-35dB_udp/pathB_nerf", "single UE (no competitor)")

WARMUP_S = 10.0       # skip the ramp when reporting mean goodput
SMOOTH_S = 3          # rolling-mean window (s) for the overlay lines

HDR = re.compile(r"\[\s*(\d+)\]\s+local\s+\S+\s+port\s+\d+\s+connected with\s+([\d.]+)\s+port\s+(\d+)")
DAT = re.compile(r"\[\s*(\d+)\]\s+([\d.]+)-\s*([\d.]+)\s+sec.*?([\d.]+)\s+([KMGT]?)bits/sec")
UNIT = {"K": 1e-3, "M": 1.0, "G": 1e3, "T": 1e6, "": 1e-6}   # -> Mbps

# a real UL flow enters the server from the UPF NAT IP or a UE tunnel IP; a flow
# from any other host (e.g. 192.168.70.152 in the 5 dB run) is a wired artifact.
ALLOWED_IP = lambda ip: ip == "192.168.70.134" or ip.startswith("10.0.0.")


def parse_run(folder):
    """-> {'ue1': (t,mbps) or None, 'total': (t,mbps) or None}."""
    paths = [os.path.join(BASE, folder, f)
             for f in ("iperf_ul_ue1.txt", "iperf_ul_ue2.txt")]
    conn_ip, conn_port, port_series = {}, {}, {}
    for path in paths:
        if not os.path.exists(path):
            continue
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
                if not (0.5 <= t1 - t0 <= 1.5):        # drop cumulative summary row
                    continue
                port = conn_port.get(cid)
                if port is None:
                    continue
                mbps = float(d.group(4)) * UNIT[d.group(5)]
                s = port_series.setdefault(port, {})
                s[t1] = max(s.get(t1, 0.0), mbps)      # de-dup NAT double-report
    ports = [p for p in port_series if any(ALLOWED_IP(ip) for ip in conn_ip.get(p, ()))]
    if not ports:
        return {"ue1": None, "total": None}

    def series(port):
        t = np.array(sorted(port_series[port]))
        return t, np.array([port_series[port][x] for x in t])

    # label by tunnel IP (10.0.0.2 = UE1 NeRF, 10.0.0.3 = UE2 const); complement
    # fills the second flow; a lone flow (UE2 never attached) is UE1.
    label = {}
    for p in ports:
        if "10.0.0.2" in conn_ip.get(p, ()):
            label[p] = "UE1"
        elif "10.0.0.3" in conn_ip.get(p, ()):
            label[p] = "UE2"
    unlabeled = [p for p in ports if p not in label]
    if len(ports) == 2 and len(label) == 1:
        label[unlabeled[0]] = ({"UE1", "UE2"} - set(label.values())).pop()
    elif len(ports) == 1 and not label:
        label[ports[0]] = "UE1"            # single radio flow = UE1 (UE2 no-attach)
    # (2 unlabeled flows would need variance; not hit by this dataset)

    ue1_ports = [p for p in ports if label.get(p) == "UE1"]
    ue1 = series(ue1_ports[0]) if ue1_ports else None

    grids = [series(p)[0] for p in ports]
    tg = np.union1d(*grids) if len(grids) > 1 else grids[0]
    tot = np.zeros_like(tg, dtype=float)
    for p in ports:
        t, y = series(p)
        tot += np.interp(tg, t, y, left=0, right=0)
    return {"ue1": ue1, "total": (tg, tot)}


def smooth(ser, win_s=SMOOTH_S):
    t, y = ser
    if len(y) < 2 or win_s <= 1:
        return t, y
    k = max(1, int(round(win_s)))
    ker = np.ones(k) / k
    return t, np.convolve(y, ker, mode="same")


def win_mean(ser):
    t, y = ser
    m = t >= WARMUP_S
    return float(np.mean(y[m])) if m.any() else (float(np.mean(y)) if len(y) else float("nan"))


parsed = [(db, color, parse_run(folder)) for folder, db, color in RUNS]
base_r = parse_run(BASELINE[0])
base_mean = win_mean(base_r["ue1"]) if base_r["ue1"] else float("nan")


def overlay(key, title, fname, ylabel):
    fig, ax = plt.subplots(figsize=(11, 6))
    # single-UE baseline (UE1 with the whole cell) - same series in both plots
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
        if db == 5 and key == "total":
            lab += " (UE2 no-attach)"
        if ser is None:
            rows.append((db, float("nan")))
            continue
        ts, ys = smooth(ser)
        m = win_mean(ser)
        ax.plot(ser[0], ser[1], color=color, lw=0.6, alpha=0.20)        # raw, faint
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


r1 = overlay("ue1", "UE1 NeRF (Path B @35 dB) UL goodput vs competitor strength",
             "multiue_nerf_ul_overlay.png", "UE1 NeRF UL goodput (Mbps)")
r2 = overlay("total", "Cell UL total (UE1 NeRF + UE2 const) vs competitor strength",
             "multiue_cell_ul_overlay.png", "cell UL total goodput (Mbps)")

print("\nmean UL goodput (Mbps, t >= %gs):" % WARMUP_S)
print("%-22s %14s %14s" % ("scenario", "UE1 NeRF", "cell total"))
print("%-22s %14.2f %14.2f" % ("single UE (baseline)", base_mean, base_mean))
m1, m2 = dict(r1), dict(r2)
for folder, db, _ in RUNS:
    print("%-22s %14.2f %14.2f" % (f"vs UE2 @ {db} dB", m1.get(db, float('nan')), m2.get(db, float('nan'))))
