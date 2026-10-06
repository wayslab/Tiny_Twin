#!/usr/bin/env python3
"""Singular plotter for tti_experiments_rpi sub-experiments.

A run writes snr.txt (and tti.txt) to <exp>/plot/ue<#UE>_<#tap>/ under this
folder. This resolves that file by experiment + ue/tap (or an explicit path)
and renders a 6-panel UL plot over TTI: TPT, MCS, BLER, PUSCH_SNR, CQI, ACK.

  # RAJ experiment (default), ue=1 tap=1  -> raj/plot/ue1_1/snr_plot.png
  python3 plot.py

  # pick ue/tap (and experiment subfolder)
  python3 plot.py 1 1
  python3 plot.py 2 4 --exp raj

  # explicit snr.txt / output
  python3 plot.py --snr-file raj/plot/ue1_1/snr.txt --out /tmp/ul.png
"""
import os
import re
import sys
import glob
import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))   # .../tti_experiments_rpi

PANELS = [
    ("ul_tpt", "UL bytes per TB"),
    ("ul_mcs", "UL MCS"),
    ("ul_bler", "UL BLER"),
    ("ul_pusch_snr", "UL PUSCH SNR (dB)"),
    ("ul_cqi", "UL CQI"),
    ("ul_harq_round", "UL cumulative retransmissions"),
]
HARQ_KEY = "ul_harq_round"


def panel_xy(key, ttis, vals):
    """(x, y) for a panel. For the HARQ panel, convert per-TB round values
    (0=new, >=1=retx) into a running cumulative count of retransmissions — a
    CDF-like step curve that ends at the total number of retransmissions."""
    if key == HARQ_KEY:
        cum, c = [], 0
        for v in vals:
            if v >= 1:
                c += 1
            cum.append(c)
        return ttis, cum
    return ttis, vals
# "ACK:" is intentionally dropped — it was the Msg4 connected-flag (always 1),
# not per-TB HARQ. HARQ_ROUND is the real retransmission signal.
_TAGS = {"TPT:": "ul_tpt", "MCS:": "ul_mcs", "BLER:": "ul_bler",
         "PUSCH_SNR:": "ul_pusch_snr", "CQI:": "ul_cqi",
         "HARQ_ROUND:": "ul_harq_round"}
_KEYS = [k for k, _ in PANELS]


_IPERF_RE = re.compile(r"([\d.]+)-\s*([\d.]+)\s+sec.*?([\d.]+)\s+[KMG]?bits/sec")


def parse_iperf(path):
    """Parse an iperf (-fk) report into (end_times_s, bandwidth_kbps) for the
    per-second intervals (skips the cumulative summary line)."""
    ts, bw = [], []
    with open(path) as f:
        for line in f:
            if "bits/sec" not in line or "SUM" in line:
                continue
            m = _IPERF_RE.search(line)
            if not m:
                continue
            t0, t1, rate = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if t1 - t0 > 1.5:        # skip the 0.0-<total> summary row
                continue
            unit = line.split("bits/sec")[0].strip()[-1]  # K/M/G before "bits/sec"
            scale = {"K": 1, "M": 1e3, "G": 1e6}.get(unit, 1)
            ts.append(t1)
            bw.append(rate * scale)   # kbps
    return ts, bw


def read_more(path):
    """Parse an snr.txt into {rnti: {key_ttis:[], key_vals:[]}} for UL metrics."""
    all_vals = {}
    current_tti = None
    with open(path) as f:
        for line in f:
            w = line.split()
            if len(w) >= 3 and w[0] == "TTI":
                try:
                    current_tti = int(w[2])
                except ValueError:
                    pass
                continue
            if len(w) >= 3 and w[0] == "UL":
                try:
                    val = float(w[2])
                except ValueError:
                    continue
                rnti = w[-1]
                d = all_vals.setdefault(
                    rnti,
                    {k + "_ttis": [] for k in _KEYS} | {k + "_vals": [] for k in _KEYS})
                tag = _TAGS.get(w[1])
                if tag and current_tti is not None:
                    d[tag + "_ttis"].append(current_tti)
                    d[tag + "_vals"].append(val)
    return all_vals


def main():
    p = argparse.ArgumentParser(description="plot UL metrics from an rpi run")
    p.add_argument("ue", type=int, nargs="?", default=1, help="number of UEs (default 1)")
    p.add_argument("tap", type=int, nargs="?", default=1, help="number of taps (default 1)")
    p.add_argument("--exp", default="raj", help="experiment subfolder (default raj)")
    p.add_argument("--snr-file", help="explicit snr.txt path (overrides exp/ue/tap)")
    p.add_argument("--out", help="output PNG (default <snr dir>/snr_plot.png)")
    a = p.parse_args()

    src = a.snr_file or os.path.join(HERE, a.exp, "plot", f"ue{a.ue}_{a.tap}", "snr.txt")
    out = a.out or os.path.join(os.path.dirname(src), "snr_plot.png")

    if not os.path.isfile(src):
        sys.exit(f"no snr.txt at {src}\n"
                 f"run the experiment first: "
                 f"cd {a.exp} && python3 raj_tti_experiment.py {a.ue} {a.tap}")

    data = read_more(src)
    fig, axes = plt.subplots(len(PANELS), 1, figsize=(14, 16), sharex=True)
    for ax, (key, label) in zip(axes, PANELS):
        for rnti, d in data.items():
            t, v = panel_xy(key, d[key + "_ttis"], d[key + "_vals"])
            if t:
                lab = (f"RNTI {rnti} (total {v[-1]})" if key == HARQ_KEY
                       else f"RNTI {rnti} (n={len(v)})")
                ax.plot(t, v, lw=0.5, label=lab)
        ax.set_ylabel(label)
        ax.grid(True, ls="--", lw=0.4, alpha=0.6)
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("TTI Index")
    fig.suptitle(f"UL metrics over TTI — {src}", fontsize=14)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"saved {out}")
    for rnti, d in data.items():
        for key, label in PANELS:
            v = d[key + "_vals"]
            if v:
                print(f"  {rnti} {key}: n={len(v)} min={min(v):.2f} "
                      f"max={max(v):.2f} mean={sum(v)/len(v):.2f}")

    # --- iperf DL + UL throughput vs time (own figure) ---
    src_dir = os.path.dirname(src)
    iperf_files = (sorted(glob.glob(os.path.join(src_dir, "iperf_ul_ue*.txt")))
                   + sorted(glob.glob(os.path.join(src_dir, "iperf_dl_ue*.txt"))))
    series = []
    for p in iperf_files:
        base = os.path.basename(p)
        direction = "UL" if "_ul_" in base else "DL"
        ue = base.split("ue")[-1].split(".")[0]
        ts, bw = parse_iperf(p)
        if ts:
            series.append((f"{direction} ue{ue}", ts, bw))
    if series:
        iperf_out = os.path.join(os.path.dirname(out), "iperf_throughput.png")
        fig2, ax2 = plt.subplots(figsize=(14, 5))
        for label, ts, bw in series:
            ax2.plot(ts, bw, lw=1.2, label=f"{label} (mean {sum(bw)/len(bw):.0f} kbps)")
        ax2.set_xlabel("Time (s)")
        ax2.set_ylabel("iperf throughput (kbps)")
        ax2.grid(True, ls="--", lw=0.4, alpha=0.6)
        ax2.legend(loc="upper right", fontsize=9)
        fig2.suptitle(f"iperf application throughput — {src_dir}", fontsize=13)
        fig2.tight_layout()
        fig2.savefig(iperf_out, dpi=130)
        print(f"saved {iperf_out}")
    else:
        print("no iperf_ul_ue*/iperf_dl_ue* data found — skipping iperf_throughput.png")


if __name__ == "__main__":
    main()
