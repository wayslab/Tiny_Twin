#!/usr/bin/env python3
"""Analyze the 4-antenna lambda/2 AoA capture: compare extracted per-antenna
channel vs ground truth, estimate AoA, plot angle error.

For each ground-truth angle <deg> there is <root>/aoa<deg>/chan_cap.txt holding
MIMO-RIC UL-channel snapshots (tagged F = filtered, L = raw LS), each reshaped to
a complex [nrx][nsc] CFR. For a lambda/2 ULA at AoA theta0 the ground truth is:
    |H_a(f)| = const (equal across antennas),
    angle H_a(f) = phi_a = pi * a * sin(theta0)   (constant per antenna, flat in f)
after the common per-subcarrier timing ramp (FFT-window slope, identical on every
antenna) is removed.

Outputs, per angle:  <root>/aoa<deg>/compare.png  (|H|, phase, CIR vs truth)
Summary:             <root>/aoa_angle_error.png    (theta_est vs theta_true + error)

  python3 aoa_analyze.py --root ./plot/aoa --angles -30 0 30
"""
import os
import argparse
from collections import Counter
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

D_ANT = 0.5 * np.arange(4)              # lambda/2 ULA element positions (wavelengths)
COLORS = ["tab:blue", "tab:orange", "tab:green", "tab:red"]


def parse(cap):
    """{'F':[H...], 'L':[H...]} of complex [nrx,nsc] snapshots."""
    out = {"F": [], "L": []}
    with open(cap) as f:
        for line in f:
            w = line.split()
            if not w:
                continue
            kind, rest = (w[0], w[1:]) if w[0] in ("F", "L") else ("F", w)
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


def pick(snaps):
    """Keep the dominant [nrx,nsc] shape, drop zero-padding subcarriers, return
    complex stack [N,nrx,nsc]."""
    if not snaps:
        return None
    shapes = Counter(s.shape for s in snaps)
    keep = max(shapes, key=lambda sh: (sh[0], sh[1], shapes[sh]))
    snaps = [s for s in snaps if s.shape == keep]
    stack = np.stack(snaps)
    occ = np.where(np.mean(np.abs(stack), axis=0).max(axis=0) > 1.0)[0]
    if len(occ):
        stack = stack[:, :, occ]
    return stack


def deramp(Hc):
    """Remove the common per-subcarrier timing ramp (slope of antenna 0) so the
    residual per-antenna phase is the AoA steering offset. Hc: [nrx,nsc]."""
    nsc = Hc.shape[1]
    kk = np.arange(nsc)
    slope = np.polyfit(kk, np.unwrap(np.angle(Hc[0])), 1)[0]
    return Hc * np.exp(-1j * slope * kk)[None, :]


def steer_vector(stack):
    """Coherently average snapshots -> [nrx,nsc], deramp, collapse to one complex
    value per antenna (the steering vector v[a]), normalized so v[0] is real>0."""
    Hc = np.mean(stack, axis=0)                 # [nrx,nsc] complex (coherent avg)
    Hd = deramp(Hc)
    v = np.mean(Hd, axis=1)                      # [nrx] per-antenna complex
    if np.abs(v[0]) > 0:
        v = v * np.conj(v[0]) / np.abs(v[0])     # rotate so antenna 0 phase = 0
    return v, Hc, Hd


def estimate_aoa(v, grid=None):
    """Classical beam-sweep AoA from the per-antenna steering vector v[nrx]."""
    nrx = len(v)
    d = 0.5 * np.arange(nrx)
    if grid is None:
        grid = np.deg2rad(np.arange(-80, 80.01, 0.1))
    P = np.array([np.abs(np.sum(np.exp(-1j * 2 * np.pi * d * np.sin(th)) * v)) ** 2
                  for th in grid])
    return grid[np.argmax(P)], grid, P


def per_snapshot_aoa(stack):
    """AoA estimate from each snapshot individually (for scatter / std)."""
    ests = []
    for s in stack:
        Hd = deramp(s)
        v = np.mean(Hd, axis=1)
        if np.abs(v[0]) > 0:
            v = v * np.conj(v[0]) / np.abs(v[0])
        th, _, _ = estimate_aoa(v)
        ests.append(th)
    return np.array(ests)


def read_muapp_aoa(outdir):
    """AoA values (deg) the muApp computed live, from aoa_muapp.txt.
    Line: <idx> <rnti> <nrx> <nsc> <theta_deg> <phi1..phiN_deg>."""
    path = os.path.join(outdir, "aoa_muapp.txt")
    if not os.path.isfile(path):
        return None
    th = []
    with open(path) as f:
        for line in f:
            w = line.split()
            if len(w) >= 5:
                try:
                    th.append(float(w[4]))
                except ValueError:
                    pass
    return np.array(th) if th else None


def analyze_angle(capfile, theta0_rad, outdir, kind_pref="F", keep_ramp=False):
    kinds = parse(capfile)
    stack = pick(kinds.get(kind_pref))
    used = kind_pref
    if stack is None:                            # fall back to the other kind
        other = "L" if kind_pref == "F" else "F"
        stack = pick(kinds.get(other)); used = other
    if stack is None:
        print(f"[warn] no usable snapshots in {capfile}")
        return None

    nrx, nsc = stack.shape[1], stack.shape[2]
    v, Hc, Hd = steer_vector(stack)
    _, grid, P = estimate_aoa(v)                  # spectrum (for the display panel only)

    # AoA ESTIMATE comes from the muApp (computed live in-container), not offline.
    muapp = read_muapp_aoa(outdir)
    if muapp is not None and len(muapp):
        theta_est = np.radians(np.median(muapp))
        snap_est = np.radians(muapp)
        aoa_src = f"muApp ({len(muapp)} live snaps)"
    else:
        print(f"[warn] no muApp AoA log in {outdir}; falling back to offline estimate")
        theta_est, _, _ = estimate_aoa(v)
        snap_est = per_snapshot_aoa(stack)
        aoa_src = "offline fallback"

    # ground-truth per-antenna phase (relative to antenna 0)
    phi_true = np.pi * np.arange(nrx) * np.sin(theta0_rad)       # = 2*pi*d*sin, d=0.5
    phi_true = np.angle(np.exp(1j * phi_true))                   # wrap to (-pi,pi]
    phi_meas = np.angle(v)

    # ---- 4-panel figure: |H|, phase vs sc, per-antenna phase vs truth, beam ----
    fig, ax = plt.subplots(1, 4, figsize=(23, 5))
    Hmag = np.mean(np.abs(stack), axis=0)        # [nrx,nsc]
    for r in range(nrx):
        ax[0].plot(np.arange(nsc), Hmag[r], color=COLORS[r % 4],
                   label=f"rx{r}: |H|={Hmag[r].mean():.0f} (±{100*Hmag[r].std()/max(Hmag[r].mean(),1e-9):.1f}%)")
    ax[0].set_title("CFR magnitude per antenna (extracted)")
    ax[0].set_xlabel("allocated subcarrier"); ax[0].set_ylabel("|H(f)|")
    ax[0].legend(fontsize=8); ax[0].grid(True, ls="--", lw=0.4, alpha=0.6)
    ax[0].set_ylim(0, Hmag.max() * 1.25)

    # phase vs subcarrier: either with the common timing ramp (raw ∠H, from Hc)
    # or with it removed (Hd). With the ramp, all antennas share the same slope
    # (FFT-window timing) and sit at their per-antenna offsets phi_a on top of it.
    Hphase = Hc if keep_ramp else Hd
    ref0 = np.angle(Hphase[0, 0])                 # center rx0 near 0 at sc 0
    for r in range(nrx):
        ph = np.unwrap(np.angle(Hphase[r])) - ref0
        ax[1].plot(np.arange(nsc), ph, color=COLORS[r % 4],
                   label=f"rx{r} (meas Δφ={phi_meas[r]:+.2f}, truth={phi_true[r]:+.2f} rad)")
    ax[1].set_title("CFR phase per antenna (with common timing ramp)" if keep_ramp
                    else "CFR phase per antenna (common ramp removed)")
    ax[1].set_xlabel("allocated subcarrier"); ax[1].set_ylabel("∠H(f) [rad]")
    ax[1].legend(fontsize=8); ax[1].grid(True, ls="--", lw=0.4, alpha=0.6)

    ax[2].plot(np.arange(nrx), phi_meas, "o-", color="tab:blue", label="extracted Δφ")
    ax[2].plot(np.arange(nrx), phi_true, "s--", color="k", label="ground truth")
    ax[2].set_title(f"per-antenna phase offset  (θ₀={np.degrees(theta0_rad):+.1f}°)")
    ax[2].set_xlabel("antenna index a"); ax[2].set_ylabel("Δφ vs rx0 [rad]")
    ax[2].set_xticks(range(nrx)); ax[2].legend(fontsize=9)
    ax[2].grid(True, ls="--", lw=0.4, alpha=0.6)

    ax[3].plot(np.degrees(grid), 10 * np.log10(P / P.max() + 1e-12), color="tab:purple")
    ax[3].axvline(np.degrees(theta0_rad), color="k", ls="--", label=f"truth {np.degrees(theta0_rad):+.1f}°")
    ax[3].axvline(np.degrees(theta_est), color="tab:red", ls="-",
                  label=f"muApp est {np.degrees(theta_est):+.1f}° (err {np.degrees(theta_est-theta0_rad):+.2f}°)")
    ax[3].set_title("AoA beam-sweep spectrum (est from muApp)")
    ax[3].set_xlabel("angle [deg]"); ax[3].set_ylabel("norm. power [dB]")
    ax[3].set_ylim(-30, 1); ax[3].legend(fontsize=9)
    ax[3].grid(True, ls="--", lw=0.4, alpha=0.6)

    fig.suptitle(f"4-antenna λ/2 AoA  —  ground truth {np.degrees(theta0_rad):+.1f}°  "
                 f"[{used}-estimate, nrx={nrx}, nsc={nsc}, {stack.shape[0]} snaps]  —  {capfile}",
                 fontsize=12)
    fig.tight_layout()
    png = os.path.join(outdir, "compare_with_ramp.png" if keep_ramp else "compare.png")
    fig.savefig(png, dpi=130)
    plt.close(fig)

    print(f"[aoa] θ₀={np.degrees(theta0_rad):+6.1f}°  "
          f"θ_est={np.degrees(theta_est):+6.2f}° [{aoa_src}]  "
          f"err={np.degrees(theta_est-theta0_rad):+5.2f}°  "
          f"(per-snap std {np.degrees(snap_est.std()):.2f}°, nrx={nrx}) -> {png}")
    return {"theta0": theta0_rad, "theta_est": theta_est, "nrx": nrx,
            "snap_est": snap_est, "phi_true": phi_true, "phi_meas": phi_meas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "plot", "aoa"))
    ap.add_argument("--angles", type=float, nargs="+", required=True)
    ap.add_argument("--kind", default="F", choices=["F", "L"], help="F=filtered, L=raw LS")
    ap.add_argument("--keep-ramp", action="store_true",
                    help="plot the CFR phase WITH the common timing ramp (raw ∠H), "
                         "writing compare_with_ramp.png instead of compare.png")
    a = ap.parse_args()

    res = []
    for deg in a.angles:
        tag = f"aoa{int(deg):+03d}".replace("+", "p").replace("-", "m")
        outdir = os.path.join(a.root, tag)
        cap = os.path.join(outdir, "chan_cap.txt")
        if not os.path.isfile(cap):
            print(f"[warn] missing {cap}"); continue
        r = analyze_angle(cap, np.radians(deg), outdir, a.kind, a.keep_ramp)
        if r:
            res.append(r)

    if not res:
        raise SystemExit("no results to summarize")

    # ---- summary: estimated vs true, and angle error ----
    t0 = np.degrees([r["theta0"] for r in res])
    te = np.degrees([r["theta_est"] for r in res])
    err = te - t0
    stds = np.degrees([r["snap_est"].std() for r in res])

    fig, (axA, axB) = plt.subplots(1, 2, figsize=(13, 5))
    lo, hi = min(t0.min(), te.min()) - 10, max(t0.max(), te.max()) + 10
    axA.plot([lo, hi], [lo, hi], "k--", alpha=0.6, label="ideal (est=truth)")
    axA.errorbar(t0, te, yerr=stds, fmt="o", color="tab:red", capsize=4, label="estimated")
    axA.set_xlabel("ground-truth AoA [deg]"); axA.set_ylabel("estimated AoA [deg]")
    axA.set_title("AoA: estimated vs ground truth"); axA.legend(); axA.grid(True, ls="--", lw=0.4, alpha=0.6)

    axB.axhline(0, color="k", lw=0.8)
    axB.bar(t0, err, width=6, color="tab:blue", alpha=0.8)
    axB.errorbar(t0, err, yerr=stds, fmt="none", ecolor="k", capsize=4)
    axB.set_xlabel("ground-truth AoA [deg]"); axB.set_ylabel("AoA error (est − truth) [deg]")
    axB.set_title(f"Angle error  (mean |err| = {np.mean(np.abs(err)):.2f}°)")
    axB.grid(True, ls="--", lw=0.4, alpha=0.6)
    for x, e in zip(t0, err):
        axB.annotate(f"{e:+.2f}°", (x, e), textcoords="offset points",
                     xytext=(0, 8 if e >= 0 else -14), ha="center", fontsize=9)

    fig.suptitle("4-antenna λ/2 ULA — AoA accuracy (AoA computed live by muApp_aoa.py)", fontsize=13)
    fig.tight_layout()
    out = os.path.join(a.root, "aoa_angle_error.png")
    fig.savefig(out, dpi=130)
    print(f"\n[aoa] summary -> {out}")
    print(f"[aoa] mean |angle error| = {np.mean(np.abs(err)):.3f}°  "
          f"(max {np.max(np.abs(err)):.3f}°)")


if __name__ == "__main__":
    main()
