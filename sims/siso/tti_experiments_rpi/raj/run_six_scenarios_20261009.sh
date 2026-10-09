#!/bin/bash
# run_six_scenarios_20261009.sh — run the 6 AI53_017 channel scenarios
# (gt/2nn/nerf x path A/B) at 30 dB, then overlay them with multiplot.
#
#   ./run_six_scenarios_20261009.sh [SNR] [OUT_DIR] [DURATION]
#
# This is the 20261009 analogue of run_four_scenarios.sh. A/B are two robot
# paths (like LOS/NLOS); gt/2nn/nerf are the three CIR methods compared on each
# path. All six runs are SISO 1 Tx / 1 Rx (ue=1 tap=1) and land under
# raj/plot/gt_2nn_nerf/.
#
# PREREQ: CPU isolation must be set this boot, or UL MCS stays pinned at 6:
#   sudo ../setup_cpu_isolation.sh
# Verify (should print 0-4,10-14):
#   cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective

set -u

source /home/ways_lab/repos/venv/sub6isac_env/bin/activate

RAJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../raj
RPI="$(dirname "$RAJ")"                               # .../tti_experiments_rpi

# soft reminder if the isolated cores still have host tasks on them
eff=$(cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective 2>/dev/null || echo "")
if [[ "$eff" != "0-4,10-14" ]]; then
  echo "WARNING: CPU isolation may not be active (system.slice cpus = '${eff:-?}')."
  echo "         Run 'sudo ../setup_cpu_isolation.sh' first or UL MCS may stay at 6."
fi

cd "$RAJ"

# Args:  ./run_six_scenarios_20261009.sh [SNR] [OUT_DIR] [DURATION]
#   SNR      - dB assigned to a unit tap (default 30)
#   OUT_DIR  - output folder under raj/ (default ./plot/gt_2nn_nerf)
#   DURATION - traffic-window seconds per run (default 150; A/B traces are
#              ~243k/237k rows, so ~150 s plays most of the trajectory)
SNR="${1:-30}"
OUT_DIR="${2:-./plot/gt_2nn_nerf}"
DURATION="${3:-150}"

# Measure the clean UL maximum: saturate the uplink (TCP, no -b) while keeping
# the downlink quiet so the DL flood doesn't starve the UL TCP ACK path.
UL_RATE="sat"
DL_RATE="1M"

echo "=== six scenarios: snr=$SNR out=$OUT_DIR dur=${DURATION}s (ul=$UL_RATE dl=$DL_RATE) ==="

run() {  # <channel> <out-subdir>
  echo "=== [$(date +%H:%M:%S)] $2 : $1  (snr=$SNR dur=${DURATION}s ul=$UL_RATE dl=$DL_RATE) ==="
  python3 20261009_raj_tti_experiment.py 1 1 --channel "$1" --snr "$SNR" --duration "$DURATION" \
          --ul-rate "$UL_RATE" --dl-rate "$DL_RATE" --out "$OUT_DIR/$2/"
}

run channel_gt_A.txt    gt_A
run channel_2nn_A.txt   2nn_A
run channel_nerf_A.txt  nerf_A
run channel_gt_B.txt    gt_B
run channel_2nn_B.txt   2nn_B
run channel_nerf_B.txt  nerf_B

# multiplot reads paths relative to its JSON's own folder, so drop a copy of the
# config into OUT_DIR and point multiplot at it (works for any OUT_DIR).
echo "=== [$(date +%H:%M:%S)] overlay: multiplot ==="
mkdir -p "$OUT_DIR"
MP_JSON="$OUT_DIR/multiplot.json"
[ "$(readlink -f "$MP_JSON")" = "$(readlink -f "$RAJ/plot/gt_2nn_nerf/multiplot.json")" ] || cp "$RAJ/plot/gt_2nn_nerf/multiplot.json" "$MP_JSON"
cd "$RPI"
python3 multiplot.py "raj/${OUT_DIR#./}/multiplot.json"

echo "=== done. outputs in $OUT_DIR/ (multiplot*.png, multiplot_results.txt) ==="
