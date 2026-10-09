#!/usr/bin/env python3
"""muApp_srs_aoa.py -- SRS-based per-antenna channel muApp.

Runs INSIDE the edgeric container. Subscribes to the RT-E2 Metrics stream and
pulls the NEW SRS-RIC export `srs_channel` (per-antenna UL channel from the SRS
codebook IQ matrix, flattened [gnb_rx_antenna][prg] interleaved re,im;
srs_channel_nrx = num gNB rx antennas, srs_channel_nsc = num PRGs). It also pulls
the DMRS `ul_channel` so the SRS-extracted channel can be compared to the DMRS one.

SRS is wideband + periodic (it sounds the whole band regardless of the PUSCH
grant), so it gives the per-antenna channel even when the UE has no UL data.

  docker exec edgeric_v2_2 python3 /home/EdgeRIC/muApp_srs_aoa.py \
      --n 200 --out /tmp/srs_cap.txt

Capture file (one or two tagged lines per snapshot):
  S <rnti> <nrx> <nsc> <re,im per [gnb_rx][prg]>     SRS codebook channel
  F <rnti> <nrx> <nsc> <re,im per [rx][sc]>          DMRS filtered ul_channel (if present)
"""
import sys
import time
import argparse

sys.path.insert(0, "/home/EdgeRIC")
import zmq
import metrics_pb2

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=200, help="SRS snapshots to capture (0 = until timeout)")
ap.add_argument("--out", default="/tmp/srs_cap.txt")
ap.add_argument("--addr", default="tcp://192.168.70.140:5555")
ap.add_argument("--timeout", type=int, default=60)
a = ap.parse_args()


def main():
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    sub.setsockopt(zmq.CONFLATE, 1)
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    sub.connect(a.addr)

    f = open(a.out, "w")
    got_srs, seen, t0 = 0, 0, time.time()
    srs_ever = False
    while (a.n == 0 or got_srs < a.n) and (time.time() - t0) < a.timeout:
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
            srs = list(ue.srs_channel)
            snrx, snsc = ue.srs_channel_nrx, ue.srs_channel_nsc
            if srs and snrx > 0 and snsc > 0:
                f.write(f"S {ue.rnti} {snrx} {snsc} " + " ".join(f"{v:.6f}" for v in srs) + "\n")
                # DMRS alongside, for comparison
                ch = list(ue.ul_channel)
                if ch and ue.ul_channel_nrx > 0:
                    f.write(f"F {ue.rnti} {ue.ul_channel_nrx} {ue.ul_channel_nsc} "
                            + " ".join(f"{v:.6f}" for v in ch) + "\n")
                f.flush()
                if got_srs % 10 == 0:
                    print(f"[muApp-srs] snap {got_srs:4d}  rnti={ue.rnti}  "
                          f"SRS nrx(gnb)={snrx} nsc(prg)={snsc} floats={len(srs)}")
                got_srs += 1
                srs_ever = True

    f.close()
    print(f"[muApp-srs] done: messages seen={seen}, SRS snapshots captured={got_srs} -> {a.out}")
    if not srs_ever:
        print("[muApp-srs] WARNING: no srs_channel field ever populated — "
              "the gNB PHY did not emit a codebook SRS channel matrix in this run.")


if __name__ == "__main__":
    main()
