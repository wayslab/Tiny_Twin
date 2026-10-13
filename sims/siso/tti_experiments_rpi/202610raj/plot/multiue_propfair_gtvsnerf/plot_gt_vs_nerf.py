#!/usr/bin/env python3
"""
plot_gt_vs_nerf.py - compare UE1 UL goodput for the GROUND-TRUTH vs NeRF-predicted
Path-B channel, across the UE2 constant-SNR sweep (35/20/10/5 dB), 35 dB anchor.

For each UE2 case we ran UE1 twice: once on the true CIR (gt) and once on the
NeRF-predicted CIR (nerf), everything else identical. The question: does the
predicted channel reproduce the true channel's per-UE throughput under PF?

Outputs:
  gtvsnerf_ue1_timeseries.png - 2x2, one panel per UE2 case, GT vs NeRF UE1
                                UL goodput over time.
  gtvsnerf_summary.png        - bars: mean UE1 goodput (GT vs NeRF) and cell
                                total (GT vs NeRF) per UE2 case + NeRF error.

Fixed-port runs -> each UE in its own file; parsed independently (see parse_file).
Means over the ACTIVE window (WARMUP_S -> last non-zero second).
"""

import os
import re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
UE2_DB = [35, 20, 10, 5]
CHANS = [("gt", "Ground truth", "#1f77b4"), ("nerf", "NeRF", "#d62728")]
WARMUP_S = 10.0
SMOOTH_S = 3

HDR = re.compile(r"\[\s*(\d+)\]\s+local\s+\S+\s+port\s+\d+\s+connected with\s+([\d.]+)\s+port\s+(\d+)")
DAT = re.compile(r"\[\s*(\d+)\]\s+([\d.]+)-\s*([\d.]+)\s+sec.*?([\d.]+)\s+([KMGT]?)bits/sec")
UNIT = {"K": 1e-3, "M": 1.0, "G": 1e3, "T": 1e6, "": 1e-6}
ALLOWED_IP = lambda ip: ip == "192.168.70.134" or ip.startswith("10.0.0.")


def parse_file(path):
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
    best = max(ports, key=lambda p: len(port_series[p]))
    t = np.array(sorted(port_series[best]))
    return t, np.array([port_series[best][x] for x in t])


def parse_run(folder):
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


def smooth(ser):
    t, y = ser
    if len(y) < 2 or SMOOTH_S <= 1:
        return t, y
    k = max(1, int(round(SMOOTH_S)))
    return t, np.convolve(y, np.ones(k) / k, mode="same")


def win_mean(ser):
    if ser is None:
        return float("nan")
    t, y = ser
    nz = np.nonzero(y > 0.05)[0]
    if len(nz) == 0:
        return float("nan")
    m = (t >= WARMUP_S) & (t <= t[nz[-1]])
    return float(np.mean(y[m])) if m.any() else float("nan")


# parse everything: data[(ch, db)] = {'ue1':..., 'total':...}
data = {}
for ch, _, _ in CHANS:
    for db in UE2_DB:
        data[(ch, db)] = parse_run(f"ue1{ch}35_ue2const{db}db")

# ---- figure 1: 2x2 UE1 goodput time series, GT vs NeRF per UE2 case ----
fig, axes = plt.subplots(2, 2, figsize=(13, 8))
for ax, db in zip(axes.ravel(), UE2_DB):
    for ch, label, color in CHANS:
        ser = data[(ch, db)]["ue1"]
        if ser is None:
            ax.plot([], [], color=color, label=f"{label} (no data)")
            continue
        ts, ys = smooth(ser)
        ax.plot(ser[0], ser[1], color=color, lw=0.5, alpha=0.18)
        ax.plot(ts, ys, color=color, lw=1.8,
                label=f"{label}  (mean {win_mean(ser):.1f} Mbps)")
    ax.set_title(f"UE2 competitor @ {db} dB", fontsize=10)
    ax.set_xlabel("iperf time (s)"); ax.set_ylabel("UE1 UL goodput (Mbps)")
    ax.grid(alpha=0.3); ax.legend(fontsize=8, loc="upper left"); ax.set_ylim(bottom=0)
fig.suptitle("UE1 UL goodput: Ground-truth vs NeRF Path-B channel (35 dB anchor)",
             fontsize=13, fontweight="bold")
fig.tight_layout(rect=[0, 0, 1, 0.97])
out1 = os.path.join(BASE, "gtvsnerf_ue1_timeseries.png")
fig.savefig(out1, dpi=140); print("wrote", out1)

# ---- figure 2: summary bars ----
gt_ue1 = [win_mean(data[("gt", db)]["ue1"]) for db in UE2_DB]
nf_ue1 = [win_mean(data[("nerf", db)]["ue1"]) for db in UE2_DB]
gt_tot = [win_mean(data[("gt", db)]["total"]) for db in UE2_DB]
nf_tot = [win_mean(data[("nerf", db)]["total"]) for db in UE2_DB]

fig2, (axa, axb) = plt.subplots(1, 2, figsize=(14, 5.5))
x = np.arange(len(UE2_DB)); w = 0.38
for ax, gt, nf, ttl in [(axa, gt_ue1, nf_ue1, "UE1 UL goodput"),
                        (axb, gt_tot, nf_tot, "Cell UL total")]:
    ax.bar(x - w/2, gt, w, color="#1f77b4", label="Ground truth")
    ax.bar(x + w/2, nf, w, color="#d62728", label="NeRF")
    for i, (g, n) in enumerate(zip(gt, nf)):
        if g and not np.isnan(g) and not np.isnan(n):
            ax.annotate(f"{(n-g)/g*100:+.0f}%", (i, max(g, n)),
                        ha="center", va="bottom", fontsize=8, color="#555")
    ax.set_xticks(x); ax.set_xticklabels([f"UE2 {d}dB" for d in UE2_DB])
    ax.set_ylabel("mean UL goodput (Mbps)")
    ax.set_title(f"{ttl}: GT vs NeRF  (% = NeRF error)", fontsize=11)
    ax.grid(alpha=0.3, axis="y"); ax.legend(fontsize=9)
fig2.suptitle("GT vs NeRF Path-B, 35 dB anchor (active-window mean goodput)",
              fontsize=13, fontweight="bold")
fig2.tight_layout(rect=[0, 0, 1, 0.96])
out2 = os.path.join(BASE, "gtvsnerf_summary.png")
fig2.savefig(out2, dpi=140); print("wrote", out2)

# ---- text table ----
print("\nmean UL goodput (Mbps, active window):")
print("%-10s %10s %10s %8s   %10s %10s" % ("UE2", "UE1_GT", "UE1_NeRF", "err%", "cell_GT", "cell_NeRF"))
for i, db in enumerate(UE2_DB):
    e = (nf_ue1[i]-gt_ue1[i])/gt_ue1[i]*100 if gt_ue1[i] else float("nan")
    print("%-10s %10.2f %10.2f %7.0f%%   %10.2f %10.2f"
          % (f"{db}dB", gt_ue1[i], nf_ue1[i], e, gt_tot[i], nf_tot[i]))
