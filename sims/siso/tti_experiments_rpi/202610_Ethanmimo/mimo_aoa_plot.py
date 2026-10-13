#!/usr/bin/env python3
"""mimo_aoa_plot.py — AoA validation over time.

Plots the muApp's per-snapshot AoA estimate (beam-swept from the gNB's EXTRACTED
per-antenna channel, logged in muapp.txt) against the GROUND-TRUTH AoA computed by
running the identical beamsweep on the INPUT channel (input_truth.json). If the
uplink MIMO channel is received correctly, the extracted-AoA track sits on the
input-AoA line.

  python3 mimo_aoa_plot.py --dir ./plot/mimo
"""
import argparse
import json
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FOV, GRID, SPACING = 80.0, 0.1, 0.5
THETA = np.deg2rad(np.arange(-FOV, FOV + 1e-9, GRID))


def beamsweep(v, spacing=SPACING):
    d = spacing * np.arange(len(v))
    P = np.array([np.abs(np.sum(np.exp(-1j * 2 * np.pi * d * np.sin(th)) * v)) ** 2 for th in THETA])
    return np.degrees(THETA[np.argmax(P)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="./plot/mimo")
    args = ap.parse_args()

    truth = json.load(open(os.path.join(args.dir, "input_truth.json")))
    nant = truth["nant"]
    phi_in = np.array(truth["phi_rad"][:nant])
    # ground-truth AoA = beamsweep on the INPUT channel's steering vector
    v_in = np.exp(1j * phi_in)
    aoa_truth = beamsweep(v_in)

    # muApp log: idx rnti nrx nsc AoA_deg  dphi1 dphi2 dphi3
    idx, aoa_est, dphi = [], [], []
    for ln in open(os.path.join(args.dir, "muapp.txt")):
        p = ln.split()
        if len(p) < 5:
            continue
        idx.append(int(p[0])); aoa_est.append(float(p[4]))
        dphi.append([float(x) for x in p[5:5 + (nant - 1)]])
    idx = np.array(idx); aoa_est = np.array(aoa_est); dphi = np.array(dphi)
    dphi_in = np.degrees(phi_in[1:] - phi_in[0]) % 360

    err = ((aoa_est - aoa_truth + 180) % 360) - 180
    print(f"input theta={truth['theta_deg']} deg | ground-truth AoA (beamsweep on input) = {aoa_truth:.2f} deg")
    print(f"extracted AoA over {len(idx)} snaps: mean={aoa_est.mean():.3f} deg, std={aoa_est.std():.3f}, "
          f"max|err|={np.abs(err).max():.3f} deg")

    fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    ax[0].plot(idx, aoa_est, ".", ms=4, color="tab:orange", label="extracted (muApp, per snapshot)")
    ax[0].axhline(aoa_truth, color="tab:blue", lw=2, label=f"ground truth (input channel) = {aoa_truth:.1f}°")
    ax[0].axhline(truth["theta_deg"], color="k", ls=":", alpha=0.6, label=f"injected θ = {truth['theta_deg']}°")
    ax[0].set_ylabel("AoA (deg)")
    ax[0].set_ylim(aoa_truth - 5, aoa_truth + 5)
    ax[0].set_title(f"AoA over time — extracted vs ground truth  "
                    f"(mean {aoa_est.mean():.2f}°, std {aoa_est.std():.2f}°, max err {np.abs(err).max():.2f}°)")
    ax[0].legend(loc="upper right"); ax[0].grid(True, alpha=0.3)

    colors = plt.cm.viridis(np.linspace(0, 0.85, nant - 1))
    for a in range(nant - 1):
        ax[1].plot(idx, dphi[:, a] % 360, ".", ms=3, color=colors[a], label=f"ant{a+1} extracted")
        ax[1].axhline(dphi_in[a], color=colors[a], lw=1.5, ls="--")
    ax[1].set_xlabel("muApp snapshot"); ax[1].set_ylabel("Δφ vs ant0 (deg)")
    ax[1].set_title("per-antenna phase over time (dots = extracted, dashed = input)")
    ax[1].legend(loc="center right", fontsize=8); ax[1].grid(True, alpha=0.3)

    fig.tight_layout()
    png = os.path.join(args.dir, "mimo_aoa_over_time.png")
    fig.savefig(png, dpi=130); plt.close(fig)
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
