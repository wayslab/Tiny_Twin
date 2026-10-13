#!/usr/bin/env python3
"""
tinytwin_vs_3gpp.py - overlay the TinyTwin measured UL throughput (iperf) against
a naive 3GPP analytical estimate computed per-TTI from the run's own PUSCH SNR.

3GPP naive model (attenuated Shannon, no simulation):
    SE(SNR)   = alpha * log2(1 + 10^(SNR_dB/10))          [bits/s/Hz]
    tput_UL   = SE * B * f_UL                              [bits/s]
with
    B    = N_PRB * SC_per_PRB * SCS          (occupied bandwidth)
    f_UL = ul_slots / total_slots            (TDD uplink duty cycle)

Defaults (per request):
    N_PRB=106, SC_per_PRB=12, SCS=30 kHz  -> B = 38.16 MHz
    alpha = 0.4 (uplink)
    TDD pattern 7D1S2U -> f_UL = 2/10 = 0.2

SNR comes from the run's `UL PUSCH_SNR:` entries in snr.txt (every TTI).
iperf UL goodput is the measured TinyTwin throughput.

Usage:  python3 tinytwin_vs_3gpp.py config.json
Config keys: run_dir, output_dir, label, alpha, N_PRB, SC_per_PRB, SCS_Hz,
             ul_slots, total_slots  (last several optional -> defaults above)
"""
import os
import re
import sys
import glob
import json
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_IPERF_RE = re.compile(r"([\d.]+)-\s*([\d.]+)\s+sec.*?([\d.]+)\s+[KMG]?bits/sec")


def read_series(path, tag):
    """snr.txt -> (ttis[], vals[]) for `UL <tag>` from the dominant RNTI."""
    per = {}
    cur = None
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
                rnti = w[-1]
                per.setdefault(rnti, [[], []])
                if cur is not None:
                    per[rnti][0].append(cur)
                    per[rnti][1].append(v)
    if not per:
        return [], []
    rnti = max(per, key=lambda r: len(per[r][0]))     # dominant RNTI
    return per[rnti]


def find_sync_tti(path, onset_tpt=500.0):
    """TTI where real UL data (hence iperf) begins: first TTI with UL TPT > onset.
    Before this the link is still attaching (MCS pinned, TPT ~ control-only)."""
    tt, vv = read_series(path, "TPT:")
    for t, v in zip(tt, vv):
        if v > onset_tpt:
            return t
    return tt[0] if tt else 0


def parse_iperf(path):
    """iperf -fk -> (end_time_s[], kbps[]); drops summary + out-of-order (2nd UDP stream)."""
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
            sc = {"K": 1, "M": 1e3, "G": 1e6}.get(u, 1)
            ts.append(t1); bw.append(rate * sc)
    return ts, bw


def bin_by(xs, ys, width):
    """Average ys into consecutive bins of `width` along xs -> (center, mean)."""
    acc = {}
    for x, y in zip(xs, ys):
        b = int(x // width)
        s = acc.setdefault(b, [0.0, 0])
        s[0] += y; s[1] += 1
    cx = sorted(acc)
    return [(b + 0.5) * width for b in cx], [acc[b][0] / acc[b][1] for b in cx]


def main():
    cfg = json.load(open(os.path.abspath(sys.argv[1])))
    run_dir = cfg["run_dir"]
    out_dir = cfg["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    label = cfg.get("label", os.path.basename(run_dir.rstrip("/")))

    # ---- 3GPP model params ----
    alpha       = float(cfg.get("alpha", 0.4))
    N_PRB       = int(cfg.get("N_PRB", 106))
    SC_per_PRB  = int(cfg.get("SC_per_PRB", 12))
    SCS_Hz      = float(cfg.get("SCS_Hz", 30e3))
    ul_slots    = int(cfg.get("ul_slots", 2))      # 7D1S2U -> 2 UL slots
    total_slots = int(cfg.get("total_slots", 10))  #          per 10-slot period
    B   = N_PRB * SC_per_PRB * SCS_Hz               # occupied bandwidth [Hz]
    fUL = ul_slots / total_slots                    # TDD uplink duty cycle
    print(f"B = {B/1e6:.2f} MHz, alpha = {alpha}, f_UL = {fUL:.3f} "
          f"({ul_slots}/{total_slots}, 7D1S2U)")

    # ---- 3GPP naive estimate per TTI (from measured PUSCH SNR) ----
    snr_path = os.path.join(run_dir, "snr.txt")
    ttis_all, snr_all = read_series(snr_path, "PUSCH_SNR:")
    if not ttis_all:
        sys.exit(f"no UL PUSCH_SNR in {snr_path}")

    # Sync/attach TTI X: before this the UE is still attaching (MCS pinned,
    # control-only UL) and iperf has NOT started, so iperf-time 0 lines up with
    # X, not TTI 0. Align by mapping [X, tN] -> [0, T_iperf] and dropping pre-X.
    X = (int(cfg["sync_tti"]) if "sync_tti" in cfg
         else find_sync_tti(snr_path, float(cfg.get("tpt_onset", 500.0))))
    ttis = [t for t in ttis_all if t >= X]
    snr  = [s for t, s in zip(ttis_all, snr_all) if t >= X]
    print(f"sync/attach TTI X = {X}  (dropped {len(ttis_all) - len(ttis)} pre-sync samples)")
    naive_mbps = [alpha * B * fUL * math.log2(1 + 10 ** (s / 10)) / 1e6 for s in snr]

    # map TTI -> time(s) by stretching the POST-sync TTI span onto iperf duration
    ul_files = sorted(glob.glob(os.path.join(run_dir, "iperf_ul_ue*.txt")))
    its, ibw = parse_iperf(ul_files[0]) if ul_files else ([], [])
    T_iperf = max(its) if its else (ttis[-1] - X) * 0.0005
    tN = ttis[-1]
    def tti2t(t): return (t - X) / (tN - X) * T_iperf if tN > X else 0.0
    naive_t = [tti2t(t) for t in ttis]
    ibw_mbps = [b / 1e3 for b in ibw]                # kbps -> Mbps

    # per-second-binned 3GPP for a clean overlay line
    nb_t, nb_v = bin_by(naive_t, naive_mbps, 1.0)

    # ---- plot ----
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(naive_t, naive_mbps, lw=0.4, color="#9ecae1", alpha=0.7,
            label="3GPP naive (every TTI)")
    ax.plot(nb_t, nb_v, lw=1.8, color="#08519c",
            label="3GPP naive (per-s mean)")
    if its:
        ax.plot(its, ibw_mbps, lw=1.6, color="#cb181d",
                label="TinyTwin measured (iperf UL)")
    ax.set_xlabel("Time (s)   [TTI linearly mapped onto iperf duration]")
    ax.set_ylabel("UL throughput (Mbps)")
    ax.set_title(f"TinyTwin vs 3GPP naive - {label}\n"
                 f"B={B/1e6:.1f} MHz, alpha={alpha}, f_UL={fUL:.2f} (7D1S2U)")
    ax.grid(True, ls="--", lw=0.4, alpha=0.6)
    ax.legend(loc="upper left", fontsize=10)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    out_png = os.path.join(out_dir, "tinytwin_vs_3gpp_pathB_gt.png")
    fig.savefig(out_png, dpi=140); plt.close(fig)
    print(f"saved {out_png}")

    # ---- summary ----
    def mean(x): return sum(x) / len(x) if x else 0.0
    meas_win = [b for t, b in zip(its, ibw_mbps) if 60 <= t <= 200]
    naive_win = [v for t, v in zip(naive_t, naive_mbps) if 60 <= t <= 200]
    L = [f"TinyTwin vs 3GPP naive - {label}",
         f"B = {B/1e6:.2f} MHz  ({N_PRB} PRB x {SC_per_PRB} x {SCS_Hz/1e3:.0f} kHz)",
         f"alpha = {alpha}   f_UL = {fUL:.3f} (7D1S2U = {ul_slots}/{total_slots})",
         f"3GPP model: tput = alpha * B * f_UL * log2(1 + 10^(SNR/10))",
         f"sync/attach TTI X = {X}  (pre-X attach phase excluded; iperf-time 0 <-> TTI X)",
         "",
         f"mean PUSCH SNR            : {mean(snr):.1f} dB",
         f"3GPP naive  mean (all)    : {mean(naive_mbps):.2f} Mbps",
         f"TinyTwin    mean (all)    : {mean(ibw_mbps):.2f} Mbps",
         f"3GPP naive  mean (60-200s): {mean(naive_win):.2f} Mbps",
         f"TinyTwin    mean (60-200s): {mean(meas_win):.2f} Mbps"]
    out_txt = os.path.join(out_dir, "tinytwin_vs_3gpp_summary.txt")
    open(out_txt, "w").write("\n".join(L) + "\n")
    print(f"saved {out_txt}")
    print("\n".join(L))


if __name__ == "__main__":
    main()
