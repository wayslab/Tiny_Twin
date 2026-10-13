#!/usr/bin/env python3
"""
fusion.py - residual-corrected ensemble regression that fuses the 2NN and NeRF
predictions to reconstruct Ground Truth:

    GT_hat(t) = w1 * 2NN(t) + w2 * NeRF(t) + b          (b = residual correction)

The coefficients (w1, w2, b) are fit by least squares on the TRAIN window, then
the model is used for inference on the TEST window. Both inputs and target are
per-second series: metric = "snr" (mean UL PUSCH SNR, dB) or "throughput"
(iperf UL goodput, Mbps). SNR is sync-aligned to iperf time (iperf-0 <-> the TTI
where real UL data begins).

Usage:  python3 fusion.py config.json
Config: gt_dir, nn2_dir, nerf_dir, output_dir, metric,
        train_window_s [lo,hi], test_window_s [lo,hi]
"""
import os
import re
import sys
import glob
import json

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_IPERF_RE = re.compile(r"([\d.]+)-\s*([\d.]+)\s+sec.*?([\d.]+)\s+[KMG]?bits/sec")


def read_series(path, tag):
    """snr.txt -> (ttis[], vals[]) for `UL <tag>` from the dominant RNTI."""
    per, cur = {}, None
    with open(path, errors="ignore") as f:
        for line in f:
            w = line.split()
            if len(w) >= 3 and w[0] == "TTI":
                try: cur = int(w[2])
                except ValueError: pass
                continue
            if len(w) >= 3 and w[0] == "UL" and w[1] == tag:
                try: v = float(w[2])
                except ValueError: continue
                per.setdefault(w[-1], [[], []])
                if cur is not None:
                    per[w[-1]][0].append(cur); per[w[-1]][1].append(v)
    if not per:
        return [], []
    r = max(per, key=lambda k: len(per[k][0]))
    return per[r]


def parse_iperf(path):
    ts, bw, last = [], [], -1.0
    with open(path, errors="ignore") as f:
        for line in f:
            if "bits/sec" not in line or "SUM" in line:
                continue
            m = _IPERF_RE.search(line)
            if not m:
                continue
            t0, t1, rate = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if t1 - t0 > 1.5 or t1 <= last:
                continue
            last = t1
            u = line.split("bits/sec")[0].strip()[-1]
            ts.append(t1); bw.append(rate * {"K": 1, "M": 1e3, "G": 1e6}.get(u, 1))
    return ts, bw


TTI_S = 0.0005   # one TTI = one channel row = 0.5 ms of sim-time


def per_second(run_dir, metric):
    """{sim_second: value}, aligned across runs by TTI (= channel row).

    The 2NN/NeRF/GT channel files share the SAME row timeline (same warmup +
    motion), so TTI R is the same motion point in every run. Binning the measured
    PUSCH SNR by sim-second = TTI*0.5ms therefore aligns the three runs feature-
    for-feature (unlike iperf wall-seconds, which drift with the ~0.6x rate).
    For metric='throughput' the iperf log is only in wall-seconds (NOT TTI-
    aligned), so cross-run fusion there is unreliable - prefer metric='snr'."""
    if metric == "throughput":
        ul = sorted(glob.glob(os.path.join(run_dir, "iperf_ul_ue*.txt")))
        its, ibw = parse_iperf(ul[0]) if ul else ([], [])
        return {int(round(t)): b / 1e3 for t, b in zip(its, ibw)}
    tt, vv = read_series(os.path.join(run_dir, "snr.txt"), "PUSCH_SNR:")
    acc = {}
    for t, v in zip(tt, vv):
        a = acc.setdefault(int(t * TTI_S), [0.0, 0]); a[0] += v; a[1] += 1
    return {sec: a[0] / a[1] for sec, a in acc.items()}


def main():
    cfg = json.load(open(os.path.abspath(sys.argv[1])))
    metric = cfg.get("metric", "snr")
    unit = "Mbps" if metric == "throughput" else "dB"
    out_dir = cfg["output_dir"]; os.makedirs(out_dir, exist_ok=True)
    tr0, tr1 = cfg.get("train_window_s", [90, 115])
    te0, te1 = cfg.get("test_window_s", [115, 200])

    gt = per_second(cfg["gt_dir"], metric)
    nn = per_second(cfg["nn2_dir"], metric)
    nf = per_second(cfg["nerf_dir"], metric)
    secs = sorted(set(gt) & set(nn) & set(nf))

    def Xy(lo, hi):
        s = [t for t in secs if lo <= t <= hi]
        A = np.array([[nn[t], nf[t], 1.0] for t in s])
        y = np.array([gt[t] for t in s])
        return s, A, y

    s_tr, A_tr, y_tr = Xy(tr0, tr1)
    coef, *_ = np.linalg.lstsq(A_tr, y_tr, rcond=None)
    w1, w2, b = coef
    s_te, A_te, y_te = Xy(te0, te1)
    yhat = A_te @ coef

    def rmse(a, c): return float(np.sqrt(np.mean((a - c) ** 2)))
    def mae(a, c): return float(np.mean(np.abs(a - c)))
    r_fus, r_2nn, r_nerf = rmse(yhat, y_te), rmse(A_te[:, 0], y_te), rmse(A_te[:, 1], y_te)

    # ---- plot ----
    fig, ax = plt.subplots(figsize=(14, 6))
    xs = [t for t in secs if tr0 <= t <= te1]
    ax.plot(xs, [gt[t] for t in xs], color="k", lw=2.0, label="Ground Truth (actual)")
    ax.plot(xs, [nn[t] for t in xs], color="#ff7f0e", lw=1.0, alpha=0.6, label="2NN")
    ax.plot(xs, [nf[t] for t in xs], color="#2ca02c", lw=1.0, alpha=0.6, label="NeRF")
    ax.plot(s_tr, A_tr @ coef, color="#1f77b4", lw=2.0, ls="--", label="fused (fit, train)")
    ax.plot(s_te, yhat, color="#d62728", lw=2.2, label="fused (predicted, test)")
    ax.axvspan(tr0, tr1, color="#1f77b4", alpha=0.08)
    ax.axvspan(te0, te1, color="gray", alpha=0.08)
    ax.text((tr0 + tr1) / 2, ax.get_ylim()[1], "train", ha="center", va="top", fontsize=9, color="#1f77b4")
    ax.text((te0 + te1) / 2, ax.get_ylim()[1], "test", ha="center", va="top", fontsize=9, color="gray")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(f"UL {'throughput' if metric=='throughput' else 'PUSCH SNR'} ({unit})")
    ax.set_title(f"Fusion: GT_hat = {w1:.2f}*2NN + {w2:.2f}*NeRF + {b:+.2f}  "
                 f"({metric})\nTest RMSE: fused={r_fus:.2f}  2NN={r_2nn:.2f}  NeRF={r_nerf:.2f} {unit}")
    ax.grid(True, ls="--", lw=0.4, alpha=0.6)
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    fig.tight_layout()
    p = os.path.join(out_dir, "fusion_test.png"); fig.savefig(p, dpi=140); plt.close(fig)
    print(f"saved {p}")

    L = [f"Fusion (residual-corrected ensemble) - metric = {metric} ({unit})",
         f"model: GT_hat = w1*2NN + w2*NeRF + b",
         f"train window = {tr0}-{tr1} s  (n={len(s_tr)})",
         f"test  window = {te0}-{te1} s  (n={len(s_te)})",
         "",
         f"  w1 (2NN)  = {w1:+.4f}",
         f"  w2 (NeRF) = {w2:+.4f}",
         f"  b         = {b:+.4f}",
         "",
         f"TEST errors ({unit}):",
         f"  fused : RMSE={r_fus:.3f}  MAE={mae(yhat, y_te):.3f}",
         f"  2NN   : RMSE={r_2nn:.3f}  MAE={mae(A_te[:,0], y_te):.3f}",
         f"  NeRF  : RMSE={r_nerf:.3f}  MAE={mae(A_te[:,1], y_te):.3f}",
         f"  fused vs best single: {100*(1 - r_fus/min(r_2nn, r_nerf)):+.1f}% RMSE"]
    out_txt = os.path.join(out_dir, "fusion_summary.txt")
    open(out_txt, "w").write("\n".join(L) + "\n")
    print(f"saved {out_txt}")
    print("\n".join(L))


if __name__ == "__main__":
    main()
