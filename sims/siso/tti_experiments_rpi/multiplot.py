#!/usr/bin/env python3
"""Overlay UL metrics from several runs onto one figure, driven by a JSON config.

The JSON lists each run's snr.txt and a legend label; every metric panel (TPT,
MCS, BLER, PUSCH_SNR, CQI, ACK) is drawn with one coloured line per run so the
channels can be compared directly.

Config format (paths are relative to the JSON file's own directory, or absolute):

  {
    "title": "raj UL metrics — channel comparison",   # optional
    "out":   "multiplot.png",                           # optional (rel. to JSON dir)
    "traces": [
      {"path": "gt_los/snr.txt",    "label": "GT LOS"},
      {"path": "pred_los/snr.txt",  "label": "Pred LOS"},
      {"path": "gt_nlos/snr.txt",   "label": "GT NLOS"},
      {"path": "pred_nlos/snr.txt", "label": "Pred NLOS"}
    ]
  }

Usage:
  python3 multiplot.py raj/plot/multiplot.json
  python3 multiplot.py raj/plot/multiplot.json --out /tmp/compare.png
"""
import os
import sys
import glob
import json
import argparse

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# reuse the single-run parser / panel definitions from plot.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot import read_more, PANELS, parse_iperf, panel_xy, HARQ_KEY


def smooth_by_tti(ttis, vals, n):
    """Average the series into consecutive N-TTI windows → (bin_center, mean)."""
    if not ttis:
        return [], []
    t = np.asarray(ttis, dtype=float)
    v = np.asarray(vals, dtype=float)
    bins = (t // n).astype(int)
    centers, means = [], []
    for b in np.unique(bins):
        m = bins == b
        centers.append((b + 0.5) * n)
        means.append(float(v[m].mean()))
    return centers, means


def dominant_rnti(data):
    """Pick the RNTI with the most samples in a run (ignores stray 1-sample RNTIs)."""
    if not data:
        return None
    return max(data, key=lambda r: sum(len(v) for k, v in data[r].items()
                                       if k.endswith("_vals")))


def main():
    p = argparse.ArgumentParser(description="overlay UL metrics from multiple runs")
    p.add_argument("config", nargs="?", default="raj/plot/multiplot.json",
                   help="JSON config (default: raj/plot/multiplot.json)")
    p.add_argument("--out", help="full output PNG path (overrides --out-dir and JSON)")
    p.add_argument("--out-dir", help="output directory (PNG named from JSON 'out', "
                                     "else multiplot.png)")
    p.add_argument("--smooth", type=int, default=None,
                   help="window (in TTIs) for the smoothed-throughput plot "
                        "(JSON 'smooth', else 500)")
    a = p.parse_args()

    cfg_path = os.path.abspath(a.config)
    if not os.path.isfile(cfg_path):
        sys.exit(f"no config at {cfg_path}")
    cfg_dir = os.path.dirname(cfg_path)
    with open(cfg_path) as f:
        cfg = json.load(f)

    traces = cfg.get("traces", [])
    if not traces:
        sys.exit("config has no 'traces'")

    def resolve(path):
        return path if os.path.isabs(path) else os.path.join(cfg_dir, path)

    # Resolve output. Precedence: --out (full path) > --out-dir > JSON out_dir/out.
    # CLI paths resolve relative to the current dir; JSON paths relative to the JSON.
    fname = os.path.basename(a.out or cfg.get("out") or "multiplot.png")
    out_dir_cfg = cfg.get("out_dir")
    if a.out:
        out = os.path.abspath(a.out)
    elif a.out_dir:
        out = os.path.abspath(os.path.join(a.out_dir, fname))
    elif out_dir_cfg:
        base = out_dir_cfg if os.path.isabs(out_dir_cfg) else os.path.join(cfg_dir, out_dir_cfg)
        out = os.path.join(base, fname)
    else:
        rel = cfg.get("out") or (os.path.splitext(os.path.basename(cfg_path))[0] + ".png")
        out = rel if os.path.isabs(rel) else os.path.join(cfg_dir, rel)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    # load each run, keep its dominant RNTI's series
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    loaded = []
    for i, t in enumerate(traces):
        src = resolve(t["path"])
        label = t.get("label", os.path.basename(os.path.dirname(src)))
        if not os.path.isfile(src):
            print(f"WARN: skipping missing {src}")
            continue
        data = read_more(src)
        rnti = dominant_rnti(data)
        if rnti is None:
            print(f"WARN: no UL data in {src}")
            continue
        loaded.append((label, data[rnti], t.get("color", colors[i % len(colors)])))
        print(f"loaded {label}: RNTI {rnti} from {src}")

    if not loaded:
        sys.exit("no usable traces found — run the experiments first")

    fig, axes = plt.subplots(len(PANELS), 1, figsize=(15, 17), sharex=True)
    for ax, (key, ylabel) in zip(axes, PANELS):
        for label, d, color in loaded:
            tt, vv = panel_xy(key, d[key + "_ttis"], d[key + "_vals"])
            if tt:
                lbl = f"{label} (total {vv[-1]})" if key == HARQ_KEY else label
                ax.plot(tt, vv, lw=0.6, color=color, label=lbl, alpha=0.85)
        ax.set_ylabel(ylabel)
        ax.grid(True, ls="--", lw=0.4, alpha=0.6)
    axes[0].legend(loc="upper right", fontsize=9, ncol=len(loaded))
    axes[-1].set_xlabel("TTI Index")
    fig.suptitle(cfg.get("title", "UL metrics — overlay"), fontsize=14)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"saved {out}")

    # --- second figure: throughput smoothed over N TTIs (own file) ---
    smooth_n = a.smooth or cfg.get("smooth") or 500
    root, ext = os.path.splitext(out)
    out_tpt = f"{root}_tpt_smoothed{ext}"
    fig2, ax2 = plt.subplots(figsize=(15, 5))
    for label, d, color in loaded:
        cx, cy = smooth_by_tti(d["ul_tpt_ttis"], d["ul_tpt_vals"], smooth_n)
        if cx:
            ax2.plot(cx, cy, lw=1.4, color=color, label=label)
    ax2.set_xlabel("TTI Index")
    ax2.set_ylabel(f"UL bytes per TB (mean per {smooth_n} TTIs)")
    ax2.grid(True, ls="--", lw=0.4, alpha=0.6)
    ax2.legend(loc="upper right", fontsize=9)
    fig2.suptitle(f"{cfg.get('title', 'UL throughput')} — smoothed (N={smooth_n} TTIs)",
                  fontsize=14)
    fig2.tight_layout()
    fig2.savefig(out_tpt, dpi=130)
    print(f"saved {out_tpt}")

    # --- third figure: iperf goodput comparison across runs (UL + DL) ---
    # also accumulates a per-channel throughput summary written to <root>.txt
    out_iperf = f"{root}_iperf{ext}"
    fig3, (axu, axd) = plt.subplots(2, 1, figsize=(15, 9), sharex=True)
    any_iperf = False
    summary = {}
    for i, t in enumerate(traces):
        d = os.path.dirname(resolve(t["path"]))
        label = t.get("label", os.path.basename(d))
        color = t.get("color", colors[i % len(colors)])
        summary[label] = {}
        for ax, pat, dirn in ((axu, "iperf_ul_ue*.txt", "ul"),
                              (axd, "iperf_dl_ue*.txt", "dl")):
            files = sorted(glob.glob(os.path.join(d, pat)))
            mean_sum = total_mb = peak = dur = 0.0
            for p in files:
                ts, bw = parse_iperf(p)
                if not ts:
                    continue
                ue = os.path.basename(p).split("ue")[-1].split(".")[0]
                lbl = label if len(files) == 1 else f"{label} ue{ue}"
                ax.plot(ts, bw, color=color, lw=1.3, label=f"{lbl} ({sum(bw)/len(bw):.0f}k)")
                any_iperf = True
                mean_sum += sum(bw) / len(bw)            # aggregate rate across UEs
                total_mb += sum(bw) / 8000.0             # kbps*1s summed -> MB
                peak = max(peak, max(bw))
                dur = max(dur, max(ts))
            summary[label][dirn] = {
                "mean_kbps": round(mean_sum, 1),
                "peak_kbps": round(peak, 1),
                "total_MB": round(total_mb, 2),
                "duration_s": round(dur, 1),
            }
    if any_iperf:
        axu.set_ylabel("UL iperf goodput (kbps)")
        axd.set_ylabel("DL iperf goodput (kbps)")
        axd.set_xlabel("Time (s)")
        for ax in (axu, axd):
            ax.grid(True, ls="--", lw=0.4, alpha=0.6)
            ax.legend(loc="upper right", fontsize=8)
        fig3.suptitle(f"{cfg.get('title', 'iperf goodput')} — iperf (received)", fontsize=14)
        fig3.tight_layout()
        fig3.savefig(out_iperf, dpi=130)
        print(f"saved {out_iperf}")

        out_txt = f"{root}_results.txt"
        doc = {"title": cfg.get("title", ""), "note": "iperf received goodput; "
               "total_MB = integral of per-second rate", "channels": summary}
        with open(out_txt, "w") as f:
            json.dump(doc, f, indent=2)
        print(f"saved {out_txt}")
    else:
        plt.close(fig3)
        print("no iperf_ul_ue*/iperf_dl_ue* files in any trace dir — skipping iperf comparison")


if __name__ == "__main__":
    main()
