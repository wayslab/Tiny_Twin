#!/usr/bin/env python3
"""muApp_aoa.py -- live AoA muApp for a 4-antenna lambda/2 gNB ULA.

Runs INSIDE the edgeric container. Subscribes to the RT-E2 Metrics stream, pulls
the MIMO-RIC per-antenna uplink channel export (`ul_channel`, flattened [rx][sc]
re,im, with ul_channel_nrx rx antennas), and for every snapshot:

  1. extracts the per-antenna CIR/CFR  H[rx][sc]  (nrx = gNB rx antennas),
  2. forms the per-antenna steering vector  v[a] = mean_sc( H_a(sc) * conj(H_0(sc)) )
     -- the conj(H_0) cancels the common per-subcarrier timing ramp (identical on
     every antenna), leaving the inter-antenna phase exp(j*phi_a),
  3. computes AoA by a classical beam-sweep over a half-wavelength ULA:
        P(theta) = | sum_a exp(-j*2*pi*d_a*sin(theta)) * v[a] |^2 ,  d_a = 0.5*a
        theta_est = argmax_theta P(theta)

and prints it live. This is the RIC application that *uses the extracted channels
to compute AoA* -- the AoA is produced here, in the muApp, not offline.

  docker exec edgeric_v2_2 python3 /home/EdgeRIC/muApp_aoa.py \
      --n 300 --out /tmp/aoa_muapp.txt --chan-out /tmp/aoa_chan_cap.txt

Outputs:
  --out       AoA log, one line per snapshot:
                 <idx> <rnti> <nrx> <nsc> <theta_deg> <phi1_deg..phiN_deg>
  --chan-out  raw per-antenna channel (same tagged format as muApp_capture_channel.py)
                 F <rnti> <nrx> <nsc> <re,im per [rx][sc]>   (filtered ul_channel)
                 L <rnti> <nrx> <nsc> ...                    (raw LS, if present)
              so the magnitude/phase-vs-ground-truth comparison uses the SAME
              muApp-extracted channel.
"""
import sys
import time
import argparse
import numpy as np

sys.path.insert(0, "/home/EdgeRIC")          # metrics_pb2 lives in the edgeric mount
import zmq
import metrics_pb2

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=300, help="non-empty snapshots to process (0 = until timeout)")
ap.add_argument("--out", default="/tmp/aoa_muapp.txt", help="AoA log")
ap.add_argument("--chan-out", default="/tmp/aoa_chan_cap.txt", help="raw per-antenna channel capture")
ap.add_argument("--addr", default="tcp://192.168.70.140:5555")
ap.add_argument("--timeout", type=int, default=60)
ap.add_argument("--spacing", type=float, default=0.5, help="ULA element spacing in wavelengths (lambda/2 = 0.5)")
ap.add_argument("--grid", type=float, default=0.1, help="beam-sweep grid step in degrees")
ap.add_argument("--fov", type=float, default=80.0, help="beam-sweep half field-of-view in degrees")
a = ap.parse_args()

THETA = np.deg2rad(np.arange(-a.fov, a.fov + 1e-9, a.grid))


def extract_channel(flat, nrx, nsc):
    """flat [re,im per [rx][sc]] -> complex [nrx, nsc], zero-padding columns dropped."""
    v = np.asarray(flat, dtype=np.float64)
    need = nrx * nsc * 2
    if len(v) < need or nrx <= 0 or nsc <= 0:
        return None
    c = v[:need].reshape(nrx, nsc, 2)
    H = c[:, :, 0] + 1j * c[:, :, 1]
    occ = np.where(np.abs(H).max(axis=0) > 1.0)[0]      # keep only allocated subcarriers
    if len(occ):
        H = H[:, occ]
    return H


def steering_vector(H):
    """v[a] = mean_sc H_a * conj(H_0); common timing ramp cancels. v[0] real>0."""
    v = np.mean(H * np.conj(H[0:1, :]), axis=1)         # [nrx]
    if np.abs(v[0]) > 0:
        v = v / np.abs(v[0])
    return v


def aoa_beamsweep(v, spacing):
    d = spacing * np.arange(len(v))
    P = np.array([np.abs(np.sum(np.exp(-1j * 2 * np.pi * d * np.sin(th)) * v)) ** 2
                  for th in THETA])
    return THETA[np.argmax(P)]


def main():
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    sub.setsockopt(zmq.CONFLATE, 1)
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    sub.connect(a.addr)

    flog = open(a.out, "w")
    fchan = open(a.chan_out, "w") if a.chan_out else None
    idx, seen, t0 = 0, 0, time.time()
    last_theta = None

    while (a.n == 0 or idx < a.n) and (time.time() - t0) < a.timeout:
        try:
            raw = sub.recv()
        except zmq.Again:
            continue
        seen += 1
        m = metrics_pb2.Metrics()
        try:
            m.ParseFromString(raw)
        except Exception:
            continue
        for ue in m.ue_metrics:
            ch = list(ue.ul_channel)
            nrx, nsc = ue.ul_channel_nrx, ue.ul_channel_nsc
            if not ch or nrx <= 0 or nsc <= 0:
                continue
            H = extract_channel(ch, nrx, nsc)
            if H is None or H.shape[0] < 2:
                continue
            v = steering_vector(H)
            theta = aoa_beamsweep(v, a.spacing)
            phi = np.degrees(np.angle(v[1:]))           # per-antenna phase vs ant0
            last_theta = np.degrees(theta)
            flog.write(f"{idx} {ue.rnti} {nrx} {nsc} {np.degrees(theta):.4f} "
                       + " ".join(f"{p:.3f}" for p in phi) + "\n")
            flog.flush()
            if fchan is not None:
                fchan.write(f"F {ue.rnti} {nrx} {nsc} " + " ".join(f"{x:.6f}" for x in ch) + "\n")
                ls = list(ue.ul_channel_ls)
                if ls and ue.ul_channel_ls_nsc > 0:
                    fchan.write(f"L {ue.rnti} {ue.ul_channel_ls_nrx} {ue.ul_channel_ls_nsc} "
                                + " ".join(f"{x:.6f}" for x in ls) + "\n")
                fchan.flush()
            if idx % 25 == 0:
                print(f"[muApp-aoa] snap {idx:4d}  rnti={ue.rnti}  nrx={nrx} nsc={nsc}  "
                      f"AoA = {np.degrees(theta):+7.2f} deg  "
                      f"(Δφ vs ant0 = {', '.join(f'{p:+.1f}°' for p in phi)})")
            idx += 1

    flog.close()
    if fchan is not None:
        fchan.close()
    print(f"[muApp-aoa] done: messages seen={seen}, AoA snapshots computed={idx} -> {a.out}"
          + (f"  (last AoA = {last_theta:+.2f} deg)" if last_theta is not None else ""))


if __name__ == "__main__":
    main()
