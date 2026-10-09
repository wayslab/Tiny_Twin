#!/usr/bin/env bash
# One-shot: run a single CellSense sensing trial and plot residual-vs-time.
#   ./run_cellsense.sh [scenario] [trajectory] [seed] [outdir]
set -e
SCEN=${1:-Open_scenario}
TRAJ=${2:-Trajectory_1}
SEED=${3:-10}
OUT=${4:-./plot/trial1}
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

python3 cellsense_position.py --scenario "$SCEN" --traj "$TRAJ" --seed "$SEED" --out "$OUT"
python3 plot_residual.py --in "$OUT/trial.npz" --out "$OUT/residual_vs_time.png"
echo "done -> $OUT/residual_vs_time.png"
