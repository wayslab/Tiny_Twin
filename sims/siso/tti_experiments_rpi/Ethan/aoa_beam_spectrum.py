#!/usr/bin/env python3
"""Plot the AoA beam-sweep (grid-search) spectrum for one or more angle dirs,
marking the ground-truth angle and the muApp's live estimate.

For each <root>/aoa<deg>/ it reads the extracted channel (chan_cap.txt, filtered
F lines) and the muApp AoA log (aoa_muapp.txt), builds the per-antenna steering
vector v[a] = mean_sc( H_a · conj(H_0) ) (the common timing ramp / delay cancels),
and sweeps  P(θ) = |Σ_a exp(-j·2π·(0.5a)·sinθ)·v[a]|²  over a θ grid.

  python3 aoa_beam_spectrum.py --root ./plot/aoa_delay --angles 30
  python3 aoa_beam_spectrum.py --root ./plot/aoa --angles -30 0 30 --out ./plot/aoa/beam_spectra.png
"""
import os
import argparse
from collections import Counter
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLORS = ["tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple", "tab:brown"]


def load_channel(cap):
    """Full complex stack [N, nrx, nsc] from the filtered (F) snapshots."""
    snaps = []
    with open(cap) as f:
        for line in f:
            w = line.split()
            if not w or w[0] != "F":
                continue
            nrx, nsc = int(w[2]), int(w[3])
            vals = np.array([float(x) for x in w[4:]], dtype=np.float64)
            need = nrx * nsc * 2
            if len(vals) < need:
                continue
            c = vals[:need].reshape(nrx, nsc, 2)
            snaps.append(c[:, :, 0] + 1j * c[:, :, 1])
    if not snaps:
        return None
    keep = Counter(s.shape for s in snaps).most_common(1)[0][0]
    snaps = [s for s in snaps if s.shape == keep]
    stack = np.stack(snaps)                                  # [N, nrx, nsc]
    occ = np.where(np.mean(np.abs(stack), axis=0).max(axis=0) > 1.0)[0]
    return stack[:, :, occ] if len(occ) else stack


def steer_vec(stack):
    """Per-antenna steering vector from the channel estimates (the muApp's method):
    v[a] = mean_sc( H_a · conj(H_0) ), averaged over snapshots. The conj(H_0)
    cancels the common per-subcarrier phase (the delay ramp)."""
    H = np.mean(stack, axis=0)                       # mean channel estimate [nrx,nsc]
    return np.mean(H * np.conj(H[0:1, :]), axis=1)   # [nrx]


def beam_spectrum(v, grid, spacing=0.5):
    """Grid-search AoA objective evaluated at each θ in `grid`:
        P(θ) = | Σ_a exp(-j·2π·d_a·sinθ) · v[a] |²,  d_a = 0.5·a."""
    d = spacing * np.arange(len(v))
    P = np.array([np.abs(np.sum(np.exp(-1j * 2 * np.pi * d * np.sin(th)) * v)) ** 2
                  for th in grid])
    return P


def muapp_est(outdir):
    path = os.path.join(outdir, "aoa_muapp.txt")
    if not os.path.isfile(path):
        return None
    th = [float(l.split()[4]) for l in open(path) if len(l.split()) >= 5]
    return float(np.median(th)) if th else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--angles", type=float, nargs="+", required=True)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--grid", type=float, default=0.1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--linear", action="store_true",
                    help="plot P(θ) in linear scale (default is 10·log10 P in dB)")
    ap.add_argument("--sample-step", type=float, default=5.0,
                    help="spacing (deg) of the discrete grid-search SAMPLE points "
                         "drawn as markers on the curve (0 = don't draw samples)")
    a = ap.parse_args()

    grid = np.deg2rad(np.arange(-a.fov, a.fov + 1e-9, a.grid))
    gdeg = np.degrees(grid)
    sdeg = np.arange(-a.fov, a.fov + 1e-9, a.sample_step) if a.sample_step > 0 else None
    sgrid = np.deg2rad(sdeg) if sdeg is not None else None

    fig, ax = plt.subplots(figsize=(11, 6))
    for i, deg in enumerate(a.angles):
        tag = f"aoa{int(deg):+03d}".replace("+", "p").replace("-", "m")
        outdir = os.path.join(a.root, tag)
        cap = os.path.join(outdir, "chan_cap.txt")
        if not os.path.isfile(cap):
            print(f"[warn] missing {cap}"); continue
        stack = load_channel(cap)
        if stack is None:
            print(f"[warn] no channel in {cap}"); continue
        v = steer_vec(stack)
        P = beam_spectrum(v, grid)
        Pmax = P.max()
        est = muapp_est(outdir)
        c = COLORS[i % len(COLORS)]
        yfun = (lambda z: z / Pmax) if a.linear else (lambda z: 10 * np.log10(z / Pmax + 1e-12))
        ax.plot(gdeg, yfun(P), color=c, lw=1.4, alpha=0.8,
                label=f"P(θ) from extracted channel  (θ₀={deg:+.0f}° run)")
        # discrete grid-search SAMPLES (the θ the search actually evaluates), as markers
        if sgrid is not None:
            Ps = beam_spectrum(v, sgrid)
            ax.plot(sdeg, yfun(Ps), "o", color=c, ms=5, mfc="white", mew=1.3,
                    label=f"grid-search samples (Δ={a.sample_step:g}°)")
        ax.axvline(deg, color=c, ls="--", lw=1.2, alpha=0.7)                 # ground truth
        if est is not None:
            ax.axvline(est, color=c, ls=":", lw=2.0,
                       label=f"muApp est {est:+.2f}° (err {est-deg:+.2f}°)")
        pk = gdeg[np.argmax(P)]
        print(f"[beam] truth {deg:+6.1f}°  grid-search peak {pk:+6.2f}°  "
              f"muApp {est if est is None else f'{est:+.2f}'}°  nrx={stack.shape[1]}")

    # legend proxies for the line styles
    ax.plot([], [], color="k", ls="--", lw=1.2, label="ground-truth θ₀ (dashed, reference only)")
    ax.plot([], [], color="k", ls=":", lw=2.0, label="muApp estimate (dotted)")
    ax.set_xlabel("angle θ [deg]")
    if a.linear:
        ax.set_ylabel("normalized beam power  P(θ)  (linear)")
        ax.set_ylim(0, 1.05)
    else:
        ax.set_ylabel("normalized beam power  10·log₁₀ P(θ)  [dB]")
        ax.set_ylim(-35, 2)
    ax.set_xlim(-a.fov, a.fov)
    ax.set_title("P(θ) from EXTRACTED channel estimates (grid search) — truth & muApp marked")
    ax.grid(True, ls="--", lw=0.4, alpha=0.6)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    out = a.out or os.path.join(a.root, "beam_spectra.png")
    fig.savefig(out, dpi=140)
    print(f"[beam] saved {out}")


if __name__ == "__main__":
    main()
