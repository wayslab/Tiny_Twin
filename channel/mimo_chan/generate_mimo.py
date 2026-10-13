#!/usr/bin/env python3
"""generate_mimo.py — build a KNOWN per-rx-antenna complex UL channel for the
uplink-MIMO validation (dev/mimo).

The patched rfsim gNB-rx path (radio/rfsimulator/simulator.c + apply_channelmod.c)
reads a dedicated UL channel file, one line PER gNB rx antenna PER TTI, each line
= nb_tx*taplen complex taps (nb_tx=1 here), real taps in channel_mimo_real.txt and
imag taps in channel_mimo_imag.txt. So the file is "multirow": nant rows per TTI.

We inject, on gNB rx antenna a, a single complex tap at sample-delay D with a
per-antenna phase offset:

    h_a[l] = 0                         for l != D
    h_a[D] = mag_a * exp(j * phi_a),   phi_a = pi * a * sin(theta)   (lambda/2 ULA)

That is a plane wave from angle `theta` (the per-antenna phase ramp phi_a) arriving
with propagation delay D samples (the tap position). In the frequency domain the
gNB should therefore estimate, per antenna a and subcarrier k:

    H_a(k) = mag_a * exp(j*phi_a) * exp(-j*2*pi*k*D / Nfft)
             [per-antenna phase offset phi_a] x [common across-subcarrier delay ramp]

The validator checks the EXTRACTED channel reproduces both: the inter-antenna
phase offsets phi_a and the common across-subcarrier delay ramp.

  python3 generate_mimo.py --theta 30 --delay 3            # writes channel_mimo_*.txt + truth json
"""
import argparse
import json
import math
import os

CHANNEL_DIR = "/home/ways_lab/repos/Tiny_Twin/channel"
REAL = os.path.join(CHANNEL_DIR, "channel_mimo_real.txt")
IMAG = os.path.join(CHANNEL_DIR, "channel_mimo_imag.txt")
TRUTH = os.path.join(CHANNEL_DIR, "mimo_chan", "input_truth.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theta", type=float, default=30.0, help="plane-wave AoA in degrees (sets phi_a = pi*a*sin(theta))")
    ap.add_argument("--delay", type=int, default=3, help="propagation delay D in samples (tap at index D; taplen=D+1)")
    ap.add_argument("--nant", type=int, default=4, help="number of gNB rx antennas (rows per TTI)")
    ap.add_argument("--nrows", type=int, default=20000, help="number of TTIs (file loops on EOF, so this just bounds file size)")
    ap.add_argument("--mag", type=float, nargs="*", default=None, help="per-antenna magnitudes (default all 1.0)")
    args = ap.parse_args()

    theta = math.radians(args.theta)
    D = args.delay
    taplen = D + 1
    nant = args.nant
    mags = args.mag if args.mag else [1.0] * nant
    assert len(mags) == nant, "need one magnitude per antenna"

    # per-antenna phase offset of a lambda/2 uniform linear array
    phi = [math.pi * a * math.sin(theta) for a in range(nant)]

    # one tap line per antenna: zeros except index D
    real_lines, imag_lines = [], []
    for a in range(nant):
        r = ["0.000000"] * taplen
        im = ["0.000000"] * taplen
        r[D] = f"{mags[a]*math.cos(phi[a]):.6f}"
        im[D] = f"{mags[a]*math.sin(phi[a]):.6f}"
        real_lines.append(" ".join(r) + "\n")
        imag_lines.append(" ".join(im) + "\n")

    os.makedirs(os.path.dirname(TRUTH), exist_ok=True)
    with open(REAL, "w") as fr, open(IMAG, "w") as fi:
        for _ in range(args.nrows):          # nant rows per TTI, repeated (static channel)
            fr.writelines(real_lines)
            fi.writelines(imag_lines)

    truth = {
        "theta_deg": args.theta, "theta_rad": theta, "delay_samples": D, "taplen": taplen,
        "nant": nant, "mag": mags, "phi_rad": phi,
        "phi_deg": [math.degrees(p) for p in phi],
        # inter-antenna offset relative to antenna 0 (what the validator compares)
        "dphi_deg": [math.degrees(phi[a] - phi[0]) for a in range(nant)],
        "note": "H_a(k) = mag_a*exp(j*phi_a)*exp(-j*2*pi*k*D/Nfft)",
    }
    with open(TRUTH, "w") as f:
        json.dump(truth, f, indent=2)

    print(f"wrote {REAL} and {IMAG}: {nant} rows/TTI x {args.nrows} TTIs, taplen={taplen}")
    print(f"  theta={args.theta} deg, delay D={D} samples")
    print(f"  per-antenna phi (deg): {[round(math.degrees(p),2) for p in phi]}")
    print(f"  truth -> {TRUTH}")


if __name__ == "__main__":
    main()
