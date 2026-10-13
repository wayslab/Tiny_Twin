#!/usr/bin/env python3
"""mimo_validate.py — show the EXTRACTED per-antenna UL channel == the INPUT.

Parses chan_cap.txt (muApp capture; `F <rnti> <nrx> <nsc> <re,im per [rx][sc]>`
filtered ul_channel, `L ...` raw LS), reconstructs H[rx][sc] per snapshot, and
compares to input_truth.json:

  * inter-antenna phase offset  dphi_a = angle( mean_sc( H_a * conj(H_0) ) )
       vs input  phi_a - phi_0 = pi*a*sin(theta)
  * common propagation-delay ramp: slope of unwrap(angle(H_a)) across subcarriers
       -> D_est = -slope * Nfft / (2*pi)   vs input delay D
  * relative per-antenna magnitude |H_a|/|H_0|  vs input mag_a/mag_0

  python3 mimo_validate.py --dir ./plot/mimo [--tag F] [--nfft 1536]
"""
import argparse
import json
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_chan(path, tag):
    """Return list of complex H arrays [nrx, nsc], one per matching snapshot line."""
    snaps = []
    with open(path) as f:
        for ln in f:
            p = ln.split()
            if len(p) < 4 or p[0] != tag:
                continue
            nrx, nsc = int(p[2]), int(p[3])
            vals = np.array(p[4:], dtype=float)
            need = nrx * nsc * 2
            if nrx <= 0 or nsc <= 0 or len(vals) < need:
                continue
            c = vals[:need].reshape(nrx, nsc, 2)
            snaps.append(c[:, :, 0] + 1j * c[:, :, 1])
    return snaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="./plot/mimo")
    ap.add_argument("--tag", default="F", help="F = filtered ul_channel, L = raw LS")
    ap.add_argument("--nfft", type=int, default=1536, help="OFDM FFT size (106 PRB/30kHz = 1536) for delay-from-slope")
    args = ap.parse_args()

    truth = json.load(open(os.path.join(args.dir, "input_truth.json")))
    nant = truth["nant"]
    snaps = parse_chan(os.path.join(args.dir, "chan_cap.txt"), args.tag)
    if not snaps:
        print(f"no '{args.tag}' snapshots in {args.dir}/chan_cap.txt"); return
    nrx = min(s.shape[0] for s in snaps)
    nsc = min(s.shape[1] for s in snaps)
    H = np.stack([s[:nrx, :nsc] for s in snaps])          # [Nsnap, nrx, nsc]
    print(f"parsed {len(snaps)} snapshots, nrx={nrx}, nsc={nsc} (tag {args.tag})")

    # --- inter-antenna phase offset (conj(H0) cancels the common delay ramp) ---
    rel = H * np.conj(H[:, 0:1, :])                        # [Nsnap, nrx, nsc]
    v = np.mean(rel, axis=(0, 2))                          # [nrx] averaged over snaps+sc
    dphi_ext = np.degrees(np.angle(v))
    dphi_in = np.array(truth["dphi_deg"][:nrx])
    mag_ext = np.abs(np.mean(H, axis=(0, 2)))
    mag_ext_rel = mag_ext / (mag_ext[0] + 1e-12)
    mag_in = np.array(truth["mag"][:nrx]); mag_in_rel = mag_in / mag_in[0]

    # --- propagation delay: IFFT the antenna-0 CFR to a CIR; the peak tap IS the
    # delay (robust to PUSCH allocation, unlike a phase-slope fit). The sign is
    # OAI's freq->time convention (consistent across runs); a zero-delay channel
    # peaks at tap 0, so |peak| = the injected sample delay. ---
    Havg0 = np.mean(H[:, 0, :], axis=0)
    cir = np.abs(np.fft.ifft(Havg0))
    pk = int(np.argmax(cir))
    pk_signed = pk - nsc if pk > nsc // 2 else pk
    D_est = abs(pk_signed)

    # --- report ---
    lines = []
    lines.append(f"INPUT : theta={truth['theta_deg']} deg, delay D={truth['delay_samples']} samples, nant={nant}")
    lines.append(f"CAPTURE: {len(snaps)} snapshots, nrx={nrx}, nsc={nsc}, tag={args.tag}")
    lines.append("")
    lines.append("per-antenna INTER-ANTENNA PHASE offset (deg), relative to antenna 0:")
    lines.append(f"  {'ant':>3} {'input':>10} {'extracted':>12} {'error':>9} {'|H|in':>7} {'|H|ext':>8}")
    for a in range(nrx):
        err = ((dphi_ext[a] - dphi_in[a] + 180) % 360) - 180
        lines.append(f"  {a:>3} {dphi_in[a]:>10.2f} {dphi_ext[a]:>12.2f} {err:>9.2f} "
                     f"{mag_in_rel[a]:>7.3f} {mag_ext_rel[a]:>8.3f}")
    max_err = max(abs(((dphi_ext[a]-dphi_in[a]+180)%360)-180) for a in range(nrx))
    lines.append("")
    lines.append(f"DELAY (antenna-0 CIR peak): tap={pk_signed} -> D_est={D_est} samples "
                 f"(input D={truth['delay_samples']})")
    lines.append("")
    verdict = "PASS" if max_err < 10.0 and abs(D_est - truth['delay_samples']) < 1.0 else "CHECK"
    lines.append(f"VERDICT: {verdict}  (max phase err {max_err:.2f} deg, delay err {abs(D_est-truth['delay_samples']):.2f} samples)")
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(args.dir, "mimo_validation.txt"), "w") as f:
        f.write(report + "\n")

    # --- plot ---
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
    a_idx = np.arange(nrx)
    ax[0].plot(a_idx, dphi_in % 360, "o-", label="input  pi*a*sin(theta)")
    ax[0].plot(a_idx, dphi_ext % 360, "s--", label="extracted")
    ax[0].set_xlabel("gNB rx antenna a"); ax[0].set_ylabel("inter-antenna phase offset (deg)")
    ax[0].set_title(f"per-antenna phase  (theta={truth['theta_deg']} deg)"); ax[0].legend(); ax[0].grid(True, alpha=0.3)

    for a in range(nrx):
        c = np.abs(np.fft.ifft(np.mean(H[:, a, :], axis=0)))
        taps = np.arange(nsc); taps = np.where(taps > nsc // 2, taps - nsc, taps)
        order = np.argsort(taps)
        ax[1].plot(taps[order], (c / c.max())[order], label=f"ant {a}")
    ax[1].axvline(-truth['delay_samples'], color="k", ls=":", label=f"input -D={-truth['delay_samples']}")
    ax[1].set_xlim(-16, 16)
    ax[1].set_xlabel("CIR tap (samples)"); ax[1].set_ylabel("|CIR| (norm)")
    ax[1].set_title(f"delay: CIR peak  D_est={D_est} (input {truth['delay_samples']})")
    ax[1].legend(fontsize=8); ax[1].grid(True, alpha=0.3)

    ax[2].bar(a_idx - 0.17, mag_in_rel, 0.34, label="input")
    ax[2].bar(a_idx + 0.17, mag_ext_rel, 0.34, label="extracted")
    ax[2].set_xlabel("gNB rx antenna a"); ax[2].set_ylabel("|H_a| / |H_0|")
    ax[2].set_title("relative per-antenna magnitude"); ax[2].legend(); ax[2].grid(True, alpha=0.3)

    fig.suptitle(f"UL-MIMO complex-channel validation — {verdict}", fontweight="bold")
    fig.tight_layout()
    png = os.path.join(args.dir, "mimo_validation.png")
    fig.savefig(png, dpi=130); plt.close(fig)
    print(f"\nwrote {png}\nwrote {os.path.join(args.dir, 'mimo_validation.txt')}")


if __name__ == "__main__":
    main()
