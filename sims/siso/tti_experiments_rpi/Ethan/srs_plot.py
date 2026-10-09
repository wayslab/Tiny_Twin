#!/usr/bin/env python3
"""Plot the SRS-extracted per-antenna channel captured by muApp_srs_aoa.py.

Reads <dir>/srs_cap.txt:
  S <rnti> <nrx> <nsc> ...   SRS codebook channel  [gnb_rx][prg] re,im
  F <rnti> <nrx> <nsc> ...   DMRS ul_channel       [rx][sc]    re,im  (for comparison)
Plots, per gNB rx antenna: |H| and phase vs PRG (SRS), and the DMRS |H| alongside.

  python3 srs_plot.py --dir ./plot/mimo2x2_2tap_phase
"""
import os
import argparse
from collections import Counter
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLORS = ["tab:blue", "tab:orange", "tab:green", "tab:red"]


def load(cap, tag):
    snaps = []
    with open(cap) as f:
        for line in f:
            w = line.split()
            if not w or w[0] != tag:
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
    return np.stack(snaps)                       # [N, nrx, nsc]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "plot", "mimo2x2_2tap_phase"))
    a = ap.parse_args()
    cap = os.path.join(a.dir, "srs_cap.txt")
    S = load(cap, "S")
    F = load(cap, "F")
    if S is None:
        raise SystemExit(f"no SRS (S) snapshots in {cap}")

    nrx, nsc = S.shape[1], S.shape[2]
    Smag = np.mean(np.abs(S), axis=0)            # [nrx, nsc]
    Sc = np.mean(S, axis=0)                       # coherent mean (phase)
    # remove the common per-PRG ramp (slope of antenna 0) to expose per-antenna offset
    kk = np.arange(nsc)
    slope = np.polyfit(kk, np.unwrap(np.angle(Sc[0])), 1)[0]
    Sd = Sc * np.exp(-1j * slope * kk)[None, :]
    dphi = np.angle(np.mean(S * np.conj(S[:, 0:1, :]), axis=(0, 2)))   # per-antenna Δφ vs rx0

    fig, ax = plt.subplots(1, 3, figsize=(18, 5))

    for r in range(nrx):
        ax[0].plot(np.arange(nsc), Smag[r], color=COLORS[r % 4],
                   label=f"SRS rx{r}: |H|={Smag[r].mean():.0f} (±{100*Smag[r].std()/max(Smag[r].mean(),1e-9):.1f}%)")
    ax[0].set_title(f"SRS CFR magnitude per antenna  (nrx={nrx}, {nsc} PRGs, {S.shape[0]} snaps)")
    ax[0].set_xlabel("PRG index"); ax[0].set_ylabel("|H| (SRS, normalized IQ)")
    ax[0].legend(fontsize=8); ax[0].grid(True, ls="--", lw=0.4, alpha=0.6)

    for r in range(nrx):
        ph = np.unwrap(np.angle(Sd[r])) - np.median(np.unwrap(np.angle(Sd[0])))
        ax[1].plot(np.arange(nsc), ph, color=COLORS[r % 4],
                   label=f"SRS rx{r}  (Δφ vs rx0 = {dphi[r]:+.2f} rad)")
    ax[1].set_title("SRS CFR phase per antenna (common ramp removed)")
    ax[1].set_xlabel("PRG index"); ax[1].set_ylabel("∠H [rad]")
    ax[1].legend(fontsize=8); ax[1].grid(True, ls="--", lw=0.4, alpha=0.6)

    # comparison: SRS vs DMRS per-antenna mean |H| and Δφ
    ax[2].plot(np.arange(nrx), dphi, "o-", color="tab:blue", label="SRS Δφ vs rx0")
    if F is not None and F.shape[1] == nrx:
        dphiF = np.angle(np.mean(F * np.conj(F[:, 0:1, :]), axis=(0, 2)))
        ax[2].plot(np.arange(nrx), dphiF, "s--", color="tab:red", label="DMRS Δφ vs rx0")
    ax[2].set_title("Per-antenna phase offset: SRS vs DMRS")
    ax[2].set_xlabel("gNB rx antenna"); ax[2].set_ylabel("Δφ vs rx0 [rad]")
    ax[2].set_xticks(range(nrx)); ax[2].legend(fontsize=9)
    ax[2].grid(True, ls="--", lw=0.4, alpha=0.6)

    fig.suptitle(f"SRS reference-signal per-antenna channel extracted by muApp_srs_aoa.py  "
                 f"(2x2 2-tap phase)  —  {cap}", fontsize=12)
    fig.tight_layout()
    out = os.path.join(a.dir, "srs_channel.png")
    fig.savefig(out, dpi=130)
    print(f"[srs-plot] SRS nrx={nrx} nsc(PRG)={nsc}, {S.shape[0]} snaps; "
          f"per-antenna Δφ = {np.round(dphi, 3)} rad")
    if F is not None:
        print(f"[srs-plot] DMRS nrx={F.shape[1]} nsc={F.shape[2]}, {F.shape[0]} snaps")
    print(f"[srs-plot] saved {out}")


if __name__ == "__main__":
    main()
