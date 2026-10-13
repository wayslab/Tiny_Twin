#!/usr/bin/env python3
"""generate_20261009.py — rebuild the raj_chan CIR channel files from the raw
peak CSVs in Downloads/raj_cir (reconstructed after the originals were lost).

Two channel families are produced, both as SINGLE-COLUMN magnitude-only traces
(one tap value per row, one row per 0.5 ms slot; the experiment driver writes a
matching all-zeros channel_imag.txt at run time, so only the real tap is stored).

  1. raj LOS/NLOS  (consumed by raj/run_four_scenarios.sh)
       source : Downloads/raj_cir/{gt,pred}_only_peaks_{los,nlos}.csv   (col: peak_value)
       layout : [WARMUP_TAP] x RAJ_WARMUP_ROWS   (attach/RACH warmup)
                [sample]      x ROWS_PER_SAMPLE   for each CSV sample (motion)
       -> channel/raj_chan/channel_{gt,pred}_{los,nlos}.txt
       row counts: LOS 61*2000 + 23000 = 145000 ; NLOS 195*2000 + 23000 = 413000

  2. Path A/B      (consumed by 202610raj/run_six_scenarios.sh)
       source : AI53_017.zip -> AI53_017/path_{A,B}/path{A,B}_{gt,nerf,2nn}.csv
                (cols: spectrum_name,peak ; peak = per-frame dominant-tap magnitude)
       layout : [WARMUP_TAP] x PATH_WARMUP_ROWS   (60 s attach warmup)
                [sample]      x ROWS_PER_SAMPLE    for each CSV sample (motion)
                [last sample] x PATH_TAIL_ROWS     (60 s tail, holds last motion value)
       -> channel/raj_chan/20261009/channel_{gt,2nn,nerf}_{A,B}.txt

Spacing: ROWS_PER_SAMPLE = 2000 (hold each measured CIR sample for 1 s = 2000
slots at numerology-1, slot = 0.5 ms). This reproduces the documented path motion
(120000 warmup + 2000*N, reaching ~TTI 338001) and the raj README row counts.

  Run: python3 generate_20261009.py
"""

import csv
import os
import zipfile

# ---- paths ----------------------------------------------------------------
RAJ_CIR_DIR = "/home/ways_lab/Downloads/raj_cir"
AI53_ZIP    = os.path.join(RAJ_CIR_DIR, "AI53_017.zip")
CHANNEL_DIR = "/home/ways_lab/repos/Tiny_Twin/channel"
RAJ_CHAN    = os.path.join(CHANNEL_DIR, "raj_chan")          # raj LOS/NLOS land here
OUT_20261009 = os.path.join(RAJ_CHAN, "20261009")           # path A/B land here

# ---- spacing / warmup constants ------------------------------------------
ROWS_PER_SAMPLE  = 2000       # hold each CIR sample for 1 s (2000 slots)
WARMUP_TAP       = 0.103712   # ~15 dB attach SNR (shared across runs; == UE2_WARMUP_TAP)
RAJ_WARMUP_ROWS  = 23000      # raj LOS/NLOS attach prefix -> 61*2000+23000=145000, 195*2000+23000=413000
PATH_WARMUP_ROWS = 120000     # path A/B 60 s attach warmup
PATH_TAIL_ROWS   = 120000     # path A/B 60 s tail (holds the last motion sample)
FMT = "{:.6f}\n"              # house style (matches stage_channel / andrew channels)


def _write_rows(path, rows):
    with open(path, "w") as f:
        for v in rows:
            f.write(FMT.format(v))
    return len(rows)


def _read_single_col_csv(path, value_col):
    """Return the list of float sample values from a CSV, skipping the header."""
    out = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            out.append(float(r[value_col]))
    return out


def gen_raj_los_nlos():
    os.makedirs(RAJ_CHAN, exist_ok=True)
    jobs = [  # (csv basename, value column, output name)
        ("gt_only_peaks_los",    "peak_value", "channel_gt_los.txt"),
        ("pred_only_peaks_los",  "peak_value", "channel_pred_los.txt"),
        ("gt_only_peaks_nlos",   "peak_value", "channel_gt_nlos.txt"),
        ("pred_only_peaks_nlos", "peak_value", "channel_pred_nlos.txt"),
    ]
    for base, col, out_name in jobs:
        samples = _read_single_col_csv(os.path.join(RAJ_CIR_DIR, base + ".csv"), col)
        rows = [WARMUP_TAP] * RAJ_WARMUP_ROWS
        for s in samples:
            rows.extend([s] * ROWS_PER_SAMPLE)
        n = _write_rows(os.path.join(RAJ_CHAN, out_name), rows)
        print(f"[raj]  {out_name:24s} {len(samples):4d} samples -> {n} rows")


def gen_path_ab():
    os.makedirs(OUT_20261009, exist_ok=True)
    with zipfile.ZipFile(AI53_ZIP) as z:
        jobs = [  # (member in zip, output name)
            ("AI53_017/path_A/pathA_gt.csv",   "channel_gt_A.txt"),
            ("AI53_017/path_A/pathA_2nn.csv",  "channel_2nn_A.txt"),
            ("AI53_017/path_A/pathA_nerf.csv", "channel_nerf_A.txt"),
            ("AI53_017/path_B/pathB_gt.csv",   "channel_gt_B.txt"),
            ("AI53_017/path_B/pathB_2nn.csv",  "channel_2nn_B.txt"),
            ("AI53_017/path_B/pathB_nerf.csv", "channel_nerf_B.txt"),
        ]
        for member, out_name in jobs:
            with z.open(member) as fh:
                text = [ln.decode("utf-8") for ln in fh.readlines()]
            reader = csv.DictReader(text)
            samples = [float(r["peak"]) for r in reader]
            rows = [WARMUP_TAP] * PATH_WARMUP_ROWS
            for s in samples:
                rows.extend([s] * ROWS_PER_SAMPLE)
            rows.extend([samples[-1]] * PATH_TAIL_ROWS)
            n = _write_rows(os.path.join(OUT_20261009, out_name), rows)
            print(f"[path] {out_name:24s} {len(samples):4d} samples -> {n} rows")


if __name__ == "__main__":
    print("== regenerating raj LOS/NLOS channels ==")
    gen_raj_los_nlos()
    print("== regenerating Path A/B channels ==")
    gen_path_ab()
    print("done.")
