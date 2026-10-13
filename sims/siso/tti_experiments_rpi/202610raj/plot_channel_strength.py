#!/usr/bin/env python3
"""Overlay the channel *input* signal strength for all 6 AI53_017 traces.

Reads channel/raj_chan/20261009/channel_{gt,2nn,nerf}_{A,B}.txt (one tap
magnitude per 0.5 ms slot) and plots, on a common time axis:
  (1) tap magnitude |h|
  (2) anchored SNR = 30 + 20*log10(|h|) dB  (the --snr 30 anchor: |h|=1 -> 30 dB)
The 12.5 s warmup hold is shaded; a nominal UL decode floor is marked.
"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

CHAN = "/home/ways_lab/repos/Tiny_Twin/channel/raj_chan/20261009"
TTI_S = 0.0005
WARMUP_TTI = 120000  # 60 s hold (matches generate_20261009.py)
# usage: plot_channel_strength.py [anchor_dB] [out_png]
SNR_ANCHOR_DB = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0  # |h|=1 -> anchor dB
DECODE_FLOOR_DB = -5.0    # nominal lowest UL-decodable SNR (guide line)
STRIDE = 40               # downsample for plotting

TRACES = [
    ("channel_gt_A.txt",   "Path A GT",   "#1f77b4"),
    ("channel_2nn_A.txt",  "Path A 2NN",  "#ff7f0e"),
    ("channel_nerf_A.txt", "Path A NeRF", "#d62728"),
    ("channel_gt_B.txt",   "Path B GT",   "#2ca02c"),
    ("channel_2nn_B.txt",  "Path B 2NN",  "#9467bd"),
    ("channel_nerf_B.txt", "Path B NeRF", "#8c564b"),
]

fig, ax = plt.subplots(2, 1, figsize=(13, 9), sharex=True)
warm_s = WARMUP_TTI * TTI_S

for fname, label, color in TRACES:
    h = np.loadtxt(f"{CHAN}/{fname}")
    t = np.arange(len(h)) * TTI_S
    snr = SNR_ANCHOR_DB + 20.0 * np.log10(np.maximum(h, 1e-9))
    ls = "--" if label == "Path A NeRF" else "-"   # dashed = failed (RLF) scenario
    lab = label + "  (RLF)" if label == "Path A NeRF" else label
    ax[0].plot(t[::STRIDE], h[::STRIDE], ls, color=color, lw=1.1, label=lab)
    ax[1].plot(t[::STRIDE], snr[::STRIDE], ls, color=color, lw=1.1, label=lab)

for a in ax:
    a.axvspan(0, warm_s, color="gray", alpha=0.12)
    a.grid(alpha=0.3)
ax[0].text(warm_s/2, ax[0].get_ylim()[1]*0.95, "warmup\n(attach)",
           ha="center", va="top", fontsize=8, color="gray")
ax[0].set_ylabel("channel tap |h|")
ax[0].set_title(f"202610raj — channel input signal strength (AI53_017, all 6 traces, {SNR_ANCHOR_DB:.0f} dB anchor)")
ax[0].legend(ncol=3, fontsize=9, loc="upper right")

ax[1].axhline(DECODE_FLOOR_DB, color="k", ls=":", lw=1)
ax[1].text(ax[1].get_xlim()[1], DECODE_FLOOR_DB, f" ~UL decode floor ({DECODE_FLOOR_DB:.0f} dB)",
           va="bottom", ha="right", fontsize=8)
ax[1].set_ylabel(f"anchored SNR (dB) = {SNR_ANCHOR_DB:.0f} + 20·log10|h|")
ax[1].set_xlabel("time (s)   [1 row = 0.5 ms slot]")

plt.tight_layout()
_default_out = "/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/202610raj/plot/channel_signal_strength.png"
out = sys.argv[2] if len(sys.argv) > 2 else _default_out
plt.savefig(out, dpi=130)
print("wrote", out)

# quick numeric summary
print(f"{'trace':<14}{'mean|h|':>9}{'minSNR':>9}{'meanSNR':>9}{'maxSNR':>9}  (dB)")
for fname, label, _ in TRACES:
    h = np.loadtxt(f"{CHAN}/{fname}")
    snr = SNR_ANCHOR_DB + 20.0 * np.log10(np.maximum(h, 1e-9))
    print(f"{label:<14}{h.mean():>9.4f}{snr.min():>9.1f}{snr.mean():>9.1f}{snr.max():>9.1f}")
