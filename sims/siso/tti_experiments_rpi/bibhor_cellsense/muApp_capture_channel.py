#!/usr/bin/env python3
"""Capture the MIMO-RIC per-antenna UL channel export from the gNB.

Runs INSIDE the edgeric container (which has zmq + metrics_pb2 and sits on the
oai-cn5g-public-net bridge, so it can reach the gNB report socket at
tcp://192.168.70.140:5555). Subscribes to the RT-E2 Metrics stream, pulls the
`ul_channel` field (flattened [rx][sc] interleaved re,im), and writes the first
N non-empty snapshots to a capture file for host-side comparison.

  docker exec edgeric_v2_2 python3 /home/EdgeRIC/muApp_capture_channel.py --n 20 --out /tmp/ethan_chan_cap.txt

Capture-file format, one snapshot per line:
  <rnti> <nrx> <nsc> <f0> <f1> ...        (floats = re,im,re,im,... per [rx][sc])
"""
import sys
import os
import time
import argparse

sys.path.insert(0, "/home/EdgeRIC")          # metrics_pb2 lives in the edgeric mount
import zmq
import metrics_pb2

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=20, help="how many non-empty snapshots to capture")
ap.add_argument("--out", default="/tmp/ethan_chan_cap.txt")
ap.add_argument("--addr", default="tcp://192.168.70.140:5555")
ap.add_argument("--timeout", type=int, default=120, help="seconds to wait before giving up")
a = ap.parse_args()

ctx = zmq.Context()
sub = ctx.socket(zmq.SUB)
sub.setsockopt_string(zmq.SUBSCRIBE, "")
sub.setsockopt(zmq.CONFLATE, 1)              # latest-only (matches the publisher's intent)
sub.setsockopt(zmq.RCVTIMEO, 2000)
sub.connect(a.addr)

got, seen, t0 = [], 0, time.time()
while len(got) < a.n and (time.time() - t0) < a.timeout:
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
        if ch and ue.ul_channel_nsc > 0:
            got.append((ue.rnti, ue.ul_channel_nrx, ue.ul_channel_nsc, ch))

with open(a.out, "w") as f:
    for rnti, nrx, nsc, ch in got:
        f.write(f"{rnti} {nrx} {nsc} " + " ".join(f"{v:.6f}" for v in ch) + "\n")

print(f"[capture] messages seen={seen}, non-empty channel snapshots captured={len(got)} "
      f"-> {a.out}")
if got:
    r, nrx, nsc, ch = got[-1]
    print(f"[capture] last: rnti={r} nrx={nrx} nsc={nsc} floats={len(ch)} "
          f"(expect nrx*nsc*2={nrx*nsc*2})")
