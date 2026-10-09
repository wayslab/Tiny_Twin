#!/usr/bin/env python3
"""Compare the gNB's exported UL channel (MIMO-RIC) to the ground-truth CIR.

Reads a capture file produced by muApp_capture_channel.py. Two line formats are
supported:
  tagged (current):  <F|L> <rnti> <nrx> <nsc> <f0 f1 ...>
                       F = filtered estimate (ul_channel, post interpolation filter)
                       L = raw LS estimate   (ul_channel_ls, pre filter)
  legacy (untagged): <rnti> <nrx> <nsc> <f0 f1 ...>   (treated as F)
with floats = re,im,re,im per [rx][sc]. Each snapshot is reshaped to a complex
[nrx][nsc] frequency response; we plot, per rx antenna:
  (a) |H(f)| across subcarriers
  (b) the time-domain CIR |h(tau)| = |IFFT(H)|

The FILTERED estimate low-pass-filters the CFR and collapses the channel to a
single dominant delay, so a multi-tap ground truth reads flat. The RAW LS
estimate is un-smoothed, so a multi-tap ground-truth channel (e.g. 1.0 @ tap0 +
0.5 @ tap8) stays visible as CFR ripple and distinct IFFT taps.

  python3 compare_channel.py --cap ./plot/mimo2x2_2tap/chan_cap.txt --out ./plot/mimo2x2_2tap/channel_compare.png
"""
import argparse
from collections import Counter
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--cap", required=True, help="capture file from muApp_capture_channel.py")
ap.add_argument("--out", default="channel_compare.png")
ap.add_argument("--nfft", type=int, default=1536,
                help="OFDM FFT size; the occupied CFR is zero-padded to this length before the "
                     "IFFT so taps land on exact integer SAMPLE delays (0 => plain nsc-point IFFT, "
                     "where the x-axis is IFFT bins, not samples). Default 1536 — the gNB's actual "
                     "ofdm_symbol_size for 106 PRB @ 30 kHz (46.08 Msps); using 2048 stretches the "
                     "delay axis by 2048/1536=1.333x.")
ap.add_argument("--only", default="", choices=["", "F", "L"],
                help="plot only one kind: F = filtered, L = raw LS (default: both)")
ap.add_argument("--truth-taps", default="",
                help="ground-truth CIR taps to overlay as 'delay:amp,delay:amp' "
                     "(e.g. '0:1.0,8:0.5'). Plotted as stems, aligned to the extracted "
                     "dominant tap so synthetic-vs-extracted lines up despite timing offset.")
a = ap.parse_args()

truth = []
if a.truth_taps:
    for part in a.truth_taps.split(","):
        d, v = part.split(":")
        truth.append((float(d), float(v)))


def parse(cap):
    """Return {'F': [H,...], 'L': [H,...]} of complex [nrx,nsc] snapshots per kind."""
    out = {"F": [], "L": []}
    with open(cap) as f:
        for line in f:
            w = line.split()
            if not w:
                continue
            if w[0] in ("F", "L"):
                kind, rest = w[0], w[1:]
            else:
                kind, rest = "F", w          # legacy untagged line
            if len(rest) < 4:
                continue
            nrx, nsc = int(rest[1]), int(rest[2])
            vals = np.array([float(x) for x in rest[3:]], dtype=np.float64)
            need = nrx * nsc * 2
            if len(vals) < need or nrx == 0 or nsc == 0:
                continue
            c = vals[:need].reshape(nrx, nsc, 2)
            out[kind].append(c[:, :, 0] + 1j * c[:, :, 1])
    return out


def reduce_kind(snaps, nfft=0):
    """Keep the widest/dominant shape, drop zero-padding, return (H_mean_abs, cir_norm).

    If nfft > 0, the occupied CFR is zero-padded to length nfft before the IFFT. A tap at
    sample delay d gives CFR phase exp(-j2*pi*k*d/Nfft); padding to N = Nfft makes its IFFT
    peak land at bin == d exactly, so the x-axis reads in SAMPLE delays (taps 0 and 8 are
    distinct integer bins instead of a single fractional ~2.5 bin).
    """
    if not snaps:
        return None
    shapes = Counter(s.shape for s in snaps)
    keep = max(shapes, key=lambda sh: (sh[1], shapes[sh]))
    snaps = [s for s in snaps if s.shape == keep]
    stack = np.stack(snaps)                                  # [N, nrx, nsc]
    nrx, nsc = keep
    alloc = np.where(np.mean(np.abs(stack), axis=0).max(axis=0) > 1.0)[0]
    if len(alloc):
        stack = stack[:, :, alloc]
    H = np.mean(np.abs(stack), axis=0)                       # [nrx, nsc]
    return H, stack, len(snaps)                              # keep complex stack; CIR computed later


kinds = parse(a.cap)
F = reduce_kind(kinds["F"], a.nfft)
L = reduce_kind(kinds["L"], a.nfft)
if F is None and L is None:
    raise SystemExit(f"no usable channel snapshots in {a.cap}")

# The filtered estimate (ul_ch_estimates) is exported at the DMRS COMB density (6 REs/RB),
# while raw LS (ul_ls_est) is held to the FULL RE density (12/RB). Both span the same band,
# so the comb one has half the samples. To put them on a common physical axis we scale by
# full_nsc / nsc: the comb-2 CFR plots at 2x subcarrier spacing, and its IFFT uses Nfft/2 so
# taps land on the same integer sample delays as the full-density one.
full_nsc = max((s[1].shape[2] for s in (F, L) if s is not None), default=1)

def cir_of(stack, nfft):
    nsc = stack.shape[2]
    neff = int(round(nfft * nsc / full_nsc)) if nfft else None
    if neff and neff < nsc:
        neff = nsc
    return np.mean(np.abs(np.fft.ifft(stack, n=neff, axis=2)), axis=0)

nrx = (F or L)[0].shape[0]
colors = ["tab:blue", "tab:orange", "tab:green", "tab:red"]
# filtered = solid, raw LS = dashed with markers
series = []
if F is not None and a.only in ("", "F"):
    series.append(("filtered", F, "-", 1.4))
if L is not None and a.only in ("", "L"):
    series.append(("raw LS", L, "--", 1.4))

fig, (ax1, ax3, ax2) = plt.subplots(1, 3, figsize=(19, 5))
for name, (H, stack, n), ls, lw in series:
    nsc = H.shape[1]
    xscale = full_nsc / nsc                 # 1 for full-density, 2 for comb-2 filtered
    cir = cir_of(stack, a.nfft)
    # complex mean per antenna (for phase); remove the COMMON timing ramp (from rx0) so the
    # per-antenna PHASE OFFSET is visible instead of being buried under the FFT-window slope.
    Hc = np.mean(stack, axis=0)                              # [nrx, nsc] complex
    kk = np.arange(nsc)
    slope = np.polyfit(kk, np.unwrap(np.angle(Hc[0])), 1)[0]
    deramp = np.exp(-1j * slope * kk)
    ph0 = None
    for r in range(H.shape[0]):
        c = colors[r % len(colors)]
        m = H[r].mean()
        ax1.plot(np.arange(nsc) * xscale, H[r], ls=ls, lw=lw, color=c,
                 label=f"rx{r} {name}: |H|={m:.0f} (flat ±{100*H[r].std()/max(m,1e-9):.1f}%)"
                       + (f", comb x{int(xscale)}" if xscale != 1 else ""))
        # unwrapped phase (common ramp removed)
        ph = np.unwrap(np.angle(Hc[r] * deramp))
        ph -= np.median(ph) if r == 0 else 0                # center rx0 near 0
        if r == 0: ph0 = ph
        lbl = f"rx{r} {name}"
        if r > 0 and ph0 is not None:
            lbl += f"  (Δφ vs rx0 ≈ {np.median(ph - ph0):+.2f} rad)"
        ax3.plot(np.arange(nsc) * xscale, ph, ls=ls, lw=lw, color=c, label=lbl)
        # Align each CIR to its own dominant (main) tap at delay 0.
        cn = cir[r] / cir[r].max()
        main = int(np.argmax(cn))
        cn = np.roll(cn, -main)
        ax2.plot(np.arange(cn.shape[0]), cn, ls=ls, lw=lw, color=c,
                 marker="." if ls == "--" else None,
                 label=f"rx{r} {name}")

ymax = max((s[1][0].max() for s in series), default=1.0)
ax1.set_ylim(0, ymax * 1.25)
ax1.set_xlabel("allocated subcarrier index  (full PUSCH grant, no downsample)")
ax1.set_ylabel("|H(f)|  (extracted, linear)")
ax1.set_title("CFR magnitude per antenna")
ax1.grid(True, ls="--", lw=0.4, alpha=0.6); ax1.legend(fontsize=8)

ax3.set_xlabel("allocated subcarrier index  (common timing ramp removed)")
ax3.set_ylabel("unwrapped ∠H(f)  [rad]")
ax3.set_title("CFR phase per antenna")
ax3.grid(True, ls="--", lw=0.4, alpha=0.6); ax3.legend(fontsize=8)

# Ground-truth taps overlay: main is now at delay 0, so stems sit at their nominal delays.
if truth:
    tmax = max(v for _, v in truth)
    for j, (d, v) in enumerate(truth):
        ax2.plot([d, d], [0, v / tmax], color="k", lw=1.6, alpha=0.8, zorder=5)
        ax2.plot([d], [v / tmax], marker="o", color="k", ms=6, zorder=6,
                 label="ground-truth taps" if j == 0 else None)

# Zoom so the main tap (at 0) and the echo are both legible.
cir_len = a.nfft if a.nfft else full_nsc
hi = (max(d for d, _ in truth) + 6) if truth else (24 if a.nfft else 40)
ax2.set_xlim(0, min(hi, cir_len))
if a.nfft:
    ax2.set_xlabel(f"delay (samples), main tap aligned to 0  [CFR zero-padded to Nfft={a.nfft}]")
else:
    ax2.set_xlabel("delay tap index (IFFT bin), main tap aligned to 0")
ax2.set_ylabel("|h(tau)|  (normalized)")
ax2.set_title("CIR per antenna  (filtered vs raw LS)")
ax2.grid(True, ls="--", lw=0.4, alpha=0.6); ax2.legend(fontsize=8)

n_f = F[2] if F else 0
n_l = L[2] if L else 0
fig.suptitle(f"PUSCH DMRS Chan Est  (layer-0 → {nrx} gNB rx antennas)  "
             f"[{n_f} filtered, {n_l} raw-LS snaps]  —  {a.cap}", fontsize=12)
fig.tight_layout()
fig.savefig(a.out, dpi=130)

for name, (H, cir, n), _, _ in series:
    for r in range(H.shape[0]):
        print(f"{name:8s} rx{r}: |H| mean={H[r].mean():.1f} std={H[r].std():.2f} "
              f"flatness={H[r].std()/max(H[r].mean(),1e-9):.4f}")
print(f"saved {a.out}")
