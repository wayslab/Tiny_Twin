#!/usr/bin/env python3
"""
post_process.py - standalone post-processor for 202610raj TTI-experiment runs.

Single self-contained file (stdlib only; matplotlib OPTIONAL). Given a JSON
config listing scenario folders (each with snr.txt and iperf_ul_ue*.txt /
iperf_dl_ue*.txt), it writes to the config's output_dir:

  summary.txt                  UL/DL throughput over a chosen window [start,end] s
  multiplot.png                6-panel UL metrics vs TTI (TPT,MCS,BLER,PUSCH-SNR,CQI,retx)
  multiplot_iperf.png          UL + DL iperf goodput vs time (window shaded)
  multiplot_tpt_smoothed.png   UL bytes/TB smoothed over `tpt_smooth` TTIs

summary.txt is ALWAYS written. The three PNGs are written only if matplotlib is
installed; without it the script still runs and just prints a notice.

-----------------------------------------------------------------------------
Config JSON (see example_config.json):

{
  "base_dir": "/abs/path/.../plot/snr-45dB_lw",   # optional; prepended to each folder
  "output_dir": "/abs/path/.../post_processing/snr_45dB",
  "title": "202610raj UL - 45 dB",                # optional
  "tpt_smooth": 100,                               # smoothing window in TTIs (default 500)
  "throughput_window_s": [60, 200],                # [start,end] s for summary (default whole run)
  "scenarios": [
    {"folder": "pathA_gt",  "label": "Path A GT"},
    {"folder": "pathA_2nn", "label": "Path A 2NN"},
    "pathB_gt"                                      # bare string allowed (folder == label)
  ]
}
A "folder" may be absolute; otherwise it is joined to base_dir.

Usage:  python3 post_process.py config.json
-----------------------------------------------------------------------------
"""
import os
import re
import sys
import glob
import json

# matplotlib is optional - summary.txt is produced regardless.
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except Exception:
    HAVE_MPL = False

# fallback color cycle (so we don't need matplotlib just to assign colors)
_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
           "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

# ----- UL metric panels (key in snr.txt -> axis label) -----
PANELS = [
    ("ul_tpt",        "UL bytes per TB"),
    ("ul_mcs",        "UL MCS"),
    ("ul_bler",       "UL BLER"),
    ("ul_pusch_snr",  "UL PUSCH SNR (dB)"),
    ("ul_cqi",        "UL CQI"),
    ("ul_harq_round", "UL cumulative retransmissions"),
]
HARQ_KEY = "ul_harq_round"
_KEYS = [k for k, _ in PANELS]
_TAGS = {"TPT:": "ul_tpt", "MCS:": "ul_mcs", "BLER:": "ul_bler",
         "PUSCH_SNR:": "ul_pusch_snr", "CQI:": "ul_cqi",
         "HARQ_ROUND:": "ul_harq_round"}
_IPERF_RE = re.compile(r"([\d.]+)-\s*([\d.]+)\s+sec.*?([\d.]+)\s+[KMG]?bits/sec")


# ======================= parsing =======================
def read_snr(path):
    """snr.txt -> {rnti: {<key>_ttis:[], <key>_vals:[]}} for the UL metrics."""
    out = {}
    cur_tti = None
    with open(path, errors="ignore") as f:
        for line in f:
            w = line.split()
            if len(w) >= 3 and w[0] == "TTI":           # "TTI Index: N" / "TTI Count: N"
                try:
                    cur_tti = int(w[2])
                except ValueError:
                    pass
                continue
            if len(w) >= 3 and w[0] == "UL":            # "UL MCS: 23 RNTI: 1720"
                tag = _TAGS.get(w[1])
                if not tag or cur_tti is None:
                    continue
                try:
                    val = float(w[2])
                except ValueError:
                    continue
                rnti = w[-1]
                d = out.setdefault(rnti, {k + "_ttis": [] for k in _KEYS}
                                         | {k + "_vals": [] for k in _KEYS})
                d[tag + "_ttis"].append(cur_tti)
                d[tag + "_vals"].append(val)
    return out


def dominant_rnti(data):
    """RNTI with the most samples (ignores stray 1-sample RNTIs from reattach)."""
    if not data:
        return None
    return max(data, key=lambda r: sum(len(v) for k, v in data[r].items()
                                       if k.endswith("_vals")))


def panel_xy(key, ttis, vals):
    """HARQ panel -> running cumulative retransmission count; others pass through."""
    if key == HARQ_KEY:
        cum, c = [], 0
        for v in vals:
            if v >= 1:
                c += 1
            cum.append(c)
        return ttis, cum
    return ttis, vals


def parse_iperf(path):
    """iperf (-fk) report -> (end_time_s[], bandwidth_kbps[]) per 1 s interval.

    UDP server logs can contain a spurious 2nd stream ('[  2]', ~100% loss)
    whose intervals restart at 1,2,3..., interleaved near the end. Keeping only
    monotonically increasing end-times drops it (and any out-of-order lines)."""
    ts, bw = [], []
    last_t = -1.0
    with open(path, errors="ignore") as f:
        for line in f:
            if "bits/sec" not in line or "SUM" in line:
                continue
            m = _IPERF_RE.search(line)
            if not m:
                continue
            t0, t1, rate = float(m.group(1)), float(m.group(2)), float(m.group(3))
            if t1 - t0 > 1.5:                           # skip the 0.0-<total> summary row
                continue
            if t1 <= last_t:                            # drop out-of-order (2nd UDP stream)
                continue
            last_t = t1
            unit = line.split("bits/sec")[0].strip()[-1]  # K/M/G before "bits/sec"
            scale = {"K": 1, "M": 1e3, "G": 1e6}.get(unit, 1)
            ts.append(t1)
            bw.append(rate * scale)                     # kbps
    return ts, bw


# ======================= helpers (pure python, no numpy) =======================
def smooth_by_tti(ttis, vals, n):
    """Mean of the series within consecutive N-TTI bins -> (bin_center, mean)."""
    if not ttis:
        return [], []
    acc = {}
    for t, v in zip(ttis, vals):
        b = int(t // n)
        s = acc.setdefault(b, [0.0, 0])
        s[0] += v
        s[1] += 1
    cx, cy = [], []
    for b in sorted(acc):
        cx.append((b + 0.5) * n)
        cy.append(acc[b][0] / acc[b][1])
    return cx, cy


def iperf_window_stats(files, t0, t1):
    """Aggregate iperf stats over [t0,t1] s across the UE file(s) for one direction."""
    mean = peak = mb = 0.0
    nsec = 0
    for p in files:
        ts, bw = parse_iperf(p)
        wb = [b for t, b in zip(ts, bw) if t0 <= t <= t1]
        if not wb:
            continue
        mean += sum(wb) / len(wb)        # sum means across UEs (aggregate rate, incl 0s)
        peak = max(peak, max(wb))
        mb += sum(wb) / 8000.0           # kbps*1s summed -> MB (kbps/8=KB/s -> /1000=MB)
        nsec = max(nsec, sum(1 for b in wb if b > 0))   # non-zero seconds (reveals collapse)
    return {"mean_kbps": mean, "peak_kbps": peak, "total_MB": mb, "n_sec": nsec}


# ======================= main =======================
def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 post_process.py <config.json>")
    with open(os.path.abspath(sys.argv[1])) as f:
        cfg = json.load(f)

    base = cfg.get("base_dir", "")
    out_dir = cfg["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    title = cfg.get("title", "UL metrics")
    smooth_n = int(cfg.get("tpt_smooth", 500))
    win = cfg.get("throughput_window_s", [0, 1e12])
    t0, t1 = float(win[0]), float(win[1])

    # normalize scenarios -> [{folder,label,dir,color,data}]
    scen = []
    for i, s in enumerate(cfg["scenarios"]):
        if isinstance(s, str):
            folder, label = s, s
        else:
            folder, label = s["folder"], s.get("label", s["folder"])
        d = folder if os.path.isabs(folder) else os.path.join(base, folder)
        scen.append({"folder": folder, "label": label, "dir": d,
                     "color": _COLORS[i % len(_COLORS)], "data": None})

    # load UL metrics (dominant RNTI) for each scenario
    for s in scen:
        snr = os.path.join(s["dir"], "snr.txt")
        if not os.path.isfile(snr):
            print(f"WARN: missing {snr}")
            continue
        data = read_snr(snr)
        r = dominant_rnti(data)
        if r is None:
            print(f"WARN: no UL data in {snr}")
            continue
        s["data"] = data[r]
        print(f"loaded {s['label']}: RNTI {r}")

    # ---------------- summary.txt (always) ----------------
    L = [f"# {title}",
         f"# throughput window: {t0:.0f}-{t1:.0f} s (iperf time)",
         f"# smoothing (tpt_smooth): {smooth_n} TTIs",
         ""]
    hdr = (f"{'scenario':<16}{'UL mean':>11}{'UL peak':>11}{'UL MB':>9}"
           f"{'DL mean':>11}{'DL peak':>11}{'DL MB':>9}{'MCS(full)':>11}{'UL secs':>9}")
    L += [hdr, "-" * len(hdr)]
    for s in scen:
        ul = iperf_window_stats(sorted(glob.glob(os.path.join(s["dir"], "iperf_ul_ue*.txt"))), t0, t1)
        dl = iperf_window_stats(sorted(glob.glob(os.path.join(s["dir"], "iperf_dl_ue*.txt"))), t0, t1)
        mcs = "-"
        if s["data"] and s["data"]["ul_mcs_vals"]:
            v = s["data"]["ul_mcs_vals"]
            mcs = f"{sum(v) / len(v):.1f}"
        L.append(f"{s['label']:<16}"
                 f"{ul['mean_kbps']:>10.0f}k{ul['peak_kbps']:>10.0f}k{ul['total_MB']:>9.2f}"
                 f"{dl['mean_kbps']:>10.0f}k{dl['peak_kbps']:>10.0f}k{dl['total_MB']:>9.2f}"
                 f"{mcs:>11}{ul['n_sec']:>9}")
    L += ["",
          "UL/DL mean,peak in kbps (of per-second iperf goodput INSIDE the window).",
          "MB      = integral of per-second rate over the window.",
          "UL secs = # in-window seconds with UL goodput; if << window length the UL",
          "          stalled/ended early (e.g. TCP collapse on a weak link).",
          "MCS(full) = mean UL MCS over the WHOLE run (not just the window)."]
    summ = os.path.join(out_dir, "summary.txt")
    with open(summ, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"saved {summ}")

    if not HAVE_MPL:
        print("matplotlib not installed - skipping the 3 PNGs (summary.txt still written). "
              "To enable plots: pip install matplotlib")
        return

    have = [s for s in scen if s["data"]]

    # ---------------- multiplot.png (6-panel UL metrics vs TTI) ----------------
    if have:
        fig, axes = plt.subplots(len(PANELS), 1, figsize=(15, 17), sharex=True)
        for ax, (key, ylabel) in zip(axes, PANELS):
            for s in have:
                d = s["data"]
                tt, vv = panel_xy(key, d[key + "_ttis"], d[key + "_vals"])
                if tt:
                    lbl = f"{s['label']} (total {vv[-1]})" if key == HARQ_KEY else s["label"]
                    ax.plot(tt, vv, lw=0.6, color=s["color"], label=lbl, alpha=0.85)
            ax.set_ylabel(ylabel)
            ax.grid(True, ls="--", lw=0.4, alpha=0.6)
        axes[0].legend(loc="upper right", fontsize=9, ncol=len(have))
        axes[-1].set_xlabel("TTI Index")
        fig.suptitle(title, fontsize=14)
        fig.tight_layout()
        p = os.path.join(out_dir, "multiplot.png")
        fig.savefig(p, dpi=130); plt.close(fig)
        print(f"saved {p}")

        # ---------------- multiplot_tpt_smoothed.png ----------------
        fig, ax = plt.subplots(figsize=(15, 5))
        for s in have:
            cx, cy = smooth_by_tti(s["data"]["ul_tpt_ttis"], s["data"]["ul_tpt_vals"], smooth_n)
            if cx:
                ax.plot(cx, cy, lw=1.4, color=s["color"], label=s["label"])
        ax.set_xlabel("TTI Index")
        ax.set_ylabel(f"UL bytes per TB (mean per {smooth_n} TTIs)")
        ax.grid(True, ls="--", lw=0.4, alpha=0.6)
        ax.legend(loc="upper right", fontsize=9)
        fig.suptitle(f"{title} - smoothed (N={smooth_n} TTIs)", fontsize=14)
        fig.tight_layout()
        p = os.path.join(out_dir, "multiplot_tpt_smoothed.png")
        fig.savefig(p, dpi=130); plt.close(fig)
        print(f"saved {p}")

    # ---------------- multiplot_iperf.png (UL + DL goodput vs time) ----------------
    fig, (axu, axd) = plt.subplots(2, 1, figsize=(15, 9), sharex=True)
    any_iperf = False
    for s in scen:
        for ax, pat in ((axu, "iperf_ul_ue*.txt"), (axd, "iperf_dl_ue*.txt")):
            for p in sorted(glob.glob(os.path.join(s["dir"], pat))):
                ts, bw = parse_iperf(p)
                if not ts:
                    continue
                any_iperf = True
                ax.plot(ts, bw, lw=1.3, color=s["color"],
                        label=f"{s['label']} ({sum(bw) / len(bw):.0f}k)")
    if any_iperf:
        for ax in (axu, axd):
            ax.axvspan(t0, min(t1, ax.get_xlim()[1]), color="gray", alpha=0.08)
            ax.axvline(t0, color="k", ls=":", lw=0.8)
            ax.axvline(t1, color="k", ls=":", lw=0.8)
            ax.grid(True, ls="--", lw=0.4, alpha=0.6)
            ax.legend(loc="upper right", fontsize=8)
        axu.set_ylabel("UL iperf goodput (kbps)")
        axd.set_ylabel("DL iperf goodput (kbps)")
        axd.set_xlabel("Time (s)   [shaded = throughput window]")
        fig.suptitle(f"{title} - iperf goodput (received)", fontsize=14)
        fig.tight_layout()
        p = os.path.join(out_dir, "multiplot_iperf.png")
        fig.savefig(p, dpi=130); plt.close(fig)
        print(f"saved {p}")
    else:
        plt.close(fig)
        print("no iperf_ul_ue*/iperf_dl_ue* files found - skipping multiplot_iperf.png")

    if not cfg.get("residuals", True):
        return   # residual + SNR-residual CDFs disabled via config

    # ---------------- residual vs GT: (method - GT) UL goodput ----------------
    # reference = the ground-truth scenario (config "reference", else the one
    # whose label/folder contains "gt"). Residual = method_UL - GT_UL per second.
    ref_key = cfg.get("reference")
    def is_ref(s):
        if ref_key:
            return s["label"] == ref_key or s["folder"] == ref_key
        return "gt" in s["label"].lower() or "gt" in os.path.basename(s["dir"]).lower()
    ref = next((s for s in scen if is_ref(s)), scen[0] if scen else None)
    others = [s for s in scen if s is not ref]

    def ul_per_sec(s):
        f = sorted(glob.glob(os.path.join(s["dir"], "iperf_ul_ue*.txt")))
        if not f:
            return {}
        ts, bw = parse_iperf(f[0])
        return {int(round(t)): b / 1e3 for t, b in zip(ts, bw)}   # sec -> Mbps

    if ref and others:
        ref_ser = ul_per_sec(ref)
        resid = {}
        for s in others:
            ms = ul_per_sec(s)
            secs = sorted(set(ms) & set(ref_ser))
            resid[s["label"]] = [(sec, ms[sec] - ref_ser[sec]) for sec in secs]

        # (a) residual vs time
        figR, axR = plt.subplots(figsize=(14, 5))
        for s in others:
            r = resid[s["label"]]
            if r:
                xs, ys = zip(*r)
                axR.plot(xs, ys, lw=1.3, color=s["color"],
                         label=f"{s['label']} - {ref['label']}")
        axR.axhline(0, color="k", lw=0.8)
        axR.axvspan(t0, t1, color="gray", alpha=0.08)
        axR.set_xlabel("Time (s)   [shaded = residual window]")
        axR.set_ylabel(f"UL goodput residual vs {ref['label']} (Mbps)")
        axR.set_title(f"{title} - UL residual vs {ref['label']}")
        axR.grid(True, ls="--", lw=0.4, alpha=0.6)
        axR.legend(loc="upper right", fontsize=9)
        figR.tight_layout()
        p = os.path.join(out_dir, "residual_iperf.png"); figR.savefig(p, dpi=130); plt.close(figR)
        print(f"saved {p}")

        # (b) CDF of residual within [t0, t1]
        figC, axC = plt.subplots(figsize=(8, 6))
        stats = []
        for s in others:
            vals = sorted(v for sec, v in resid[s["label"]] if t0 <= sec <= t1)
            if not vals:
                continue
            n = len(vals)
            axC.plot(vals, [(i + 1) / n for i in range(n)], lw=1.8, color=s["color"],
                     label=f"{s['label']} - {ref['label']}")
            mean = sum(vals) / n
            med = vals[n // 2]
            p10, p90 = vals[int(0.1 * n)], vals[min(n - 1, int(0.9 * n))]
            stats.append(f"  {s['label']+' - '+ref['label']:<22} mean={mean:+.2f}  median={med:+.2f}  "
                         f"P10={p10:+.2f}  P90={p90:+.2f}  Mbps (n={n})")
        axC.axvline(0, color="k", lw=0.8)
        axC.set_xlabel(f"UL goodput residual vs {ref['label']} (Mbps)")
        axC.set_ylabel("CDF"); axC.set_ylim(0, 1)
        axC.set_title(f"{title} - residual CDF ({t0:.0f}-{t1:.0f} s)")
        axC.grid(True, ls="--", lw=0.4, alpha=0.6)
        axC.legend(loc="upper left", fontsize=9)
        figC.tight_layout()
        p = os.path.join(out_dir, "residual_cdf.png"); figC.savefig(p, dpi=130); plt.close(figC)
        print(f"saved {p}")

        with open(summ, "a") as f:
            f.write(f"\nUL goodput residual vs {ref['label']} ({t0:.0f}-{t1:.0f} s):\n")
            f.write("\n".join(stats) + "\n")
    else:
        print("residual: need a reference (GT) + other scenario(s) - skipping")

    # ---------------- PUSCH SNR residual vs GT CDF (within the window) ----------------
    # SNR is per TTI; bin each run into per-second mean SNR (mapping its post-sync
    # TTI span onto its iperf duration), then residual = SNR_method - SNR_GT per s.
    def sync_tti(d, onset=500.0):
        for t, v in zip(d["ul_tpt_ttis"], d["ul_tpt_vals"]):
            if v > onset:
                return t
        return d["ul_tpt_ttis"][0] if d["ul_tpt_ttis"] else 0
    def snr_per_sec(s):
        d = s["data"]
        if not d or not d["ul_pusch_snr_ttis"]:
            return {}
        tt, vv = d["ul_pusch_snr_ttis"], d["ul_pusch_snr_vals"]
        X, tN = sync_tti(d), tt[-1]
        ser = ul_per_sec(s)
        Tip = max(ser) if ser else (tN - X) * 0.0005
        acc = {}
        for t, v in zip(tt, vv):
            tm = (t - X) / (tN - X) * Tip if tN > X else 0.0
            a = acc.setdefault(int(tm), [0.0, 0]); a[0] += v; a[1] += 1
        return {sec: a[0] / a[1] for sec, a in acc.items()}

    if ref and others:
        gt_snr = snr_per_sec(ref)
        figS, axS = plt.subplots(figsize=(8, 6))
        snr_lines = []
        for s in others:
            ms = snr_per_sec(s)
            vals = sorted(ms[sec] - gt_snr[sec] for sec in (set(ms) & set(gt_snr))
                          if t0 <= sec <= t1)
            if not vals:
                continue
            n = len(vals)
            axS.plot(vals, [(i + 1) / n for i in range(n)], lw=1.8, color=s["color"],
                     label=f"{s['label']} - {ref['label']}")
            snr_lines.append(f"  {s['label']+' - '+ref['label']:<22} mean={sum(vals)/n:+.2f}  "
                             f"median={vals[n//2]:+.2f}  P10={vals[int(0.1*n)]:+.2f}  "
                             f"P90={vals[min(n-1,int(0.9*n))]:+.2f}  dB (n={n})")
        if snr_lines:
            axS.axvline(0, color="k", lw=0.8)
            axS.set_xlabel(f"UL PUSCH SNR residual vs {ref['label']} (dB)")
            axS.set_ylabel("CDF"); axS.set_ylim(0, 1)
            axS.set_title(f"{title} - PUSCH SNR residual CDF ({t0:.0f}-{t1:.0f} s)")
            axS.grid(True, ls="--", lw=0.4, alpha=0.6)
            axS.legend(loc="upper left", fontsize=9)
            figS.tight_layout()
            p = os.path.join(out_dir, "snr_cdf.png"); figS.savefig(p, dpi=130); plt.close(figS)
            print(f"saved {p}")
            with open(summ, "a") as f:
                f.write(f"\nUL PUSCH SNR residual vs {ref['label']} ({t0:.0f}-{t1:.0f} s):\n")
                f.write("\n".join(snr_lines) + "\n")
        else:
            plt.close(figS)
    else:
        print("SNR residual: need a reference (GT) + other scenario(s) - skipping")


if __name__ == "__main__":
    main()
