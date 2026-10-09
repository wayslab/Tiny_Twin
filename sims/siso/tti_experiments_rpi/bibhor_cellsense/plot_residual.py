#!/usr/bin/env python3
"""plot_residual.py -- residual (position) error vs time for one CellSense trial.

Reads <out>/trial.npz written by cellsense_position.py and plots the per-frame
residual error ||pos_est - pos_gt|| against time (frame x Te). Frames in the
static-learning phase and frames with no position estimate (miss / not visible)
are shaded/marked so the plot is honest about where an estimate exists.

  python3 plot_residual.py --in ./plot/trial1/trial.npz --out ./plot/trial1/residual_vs_time.png
"""
import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="inp", default="./plot/trial1/trial.npz")
ap.add_argument("--out", default=None)
a = ap.parse_args()

d = np.load(a.inp, allow_pickle=True)
r = d["residual"]
vis = d["visible"]
sp = int(d["static_point"])
Te = float(d["Te"])
scen = str(d["scenario"])
traj = str(d["trajectory"])

K = len(r)
t = np.arange(K) * Te
have = ~np.isnan(r)

fig, ax = plt.subplots(figsize=(11, 5))

# static-learning phase shading
ax.axvspan(0, sp * Te, color="0.85", alpha=0.6, label=f"static-learning phase (frames 0-{sp-1})")

# residual where an estimate exists
ax.plot(t[have], r[have], "-o", color="tab:blue", lw=1.6, ms=5, label="residual error")

# mark visible-object frames with no estimate (misses) along the bottom
miss = (~have) & (np.arange(K) >= sp) & (vis == 1)
if np.any(miss):
    ax.plot(t[miss], np.zeros(np.sum(miss)), "x", color="tab:red", ms=7,
            label="visible but no estimate (miss)")

# 5 m acceptance threshold used by the ISAC detector
ax.axhline(5.0, color="tab:orange", ls="--", lw=1.2, label="5 m detection threshold")

if np.any(have):
    rmse = np.sqrt(np.mean(r[have] ** 2))
    ax.axhline(rmse, color="tab:green", ls=":", lw=1.2, label=f"RMSE = {rmse:.2f} m")

ax.set_xlabel("time (s)   [frame index x Te = 0.08 s]")
ax.set_ylabel("residual position error  ||est - ground truth||  (m)")
ax.set_title(f"CellSense per-TTI residual error vs time -- {scen}/{traj} (one trial)")
ax.set_xlim(0, (K - 1) * Te)
ax.set_ylim(bottom=0)
ax.grid(True, ls="--", lw=0.4, alpha=0.6)
ax.legend(fontsize=9, loc="upper right")
fig.tight_layout()

out = a.out or a.inp.replace("trial.npz", "residual_vs_time.png")
fig.savefig(out, dpi=140)
print(f"saved {out}")
