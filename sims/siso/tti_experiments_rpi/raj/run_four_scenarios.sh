#!/bin/bash
# run_four_scenarios.sh — run the 4 raj channel scenarios, then overlay with multiplot.
#
#   ./run_four_scenarios.sh
#
# PREREQ: CPU isolation must be set this boot, or UL MCS stays pinned at 6:
#   sudo ../setup_cpu_isolation.sh
#
# Each run is ~3-4 min wall-clock, so the four take ~15 min; multiplot runs at the end.

set -u

source /home/ways_lab/repos/venv/sub6isac_env/bin/activate

RAJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../raj
RPI="$(dirname "$RAJ")"                               # .../tti_experiments_rpi

# soft reminder if the isolated cores still have host tasks on them
eff=$(cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective 2>/dev/null || echo "")
if [[ "$eff" != "0-4,10-14" ]]; then
  echo "WARNING: CPU isolation may not be active (system.slice cpus = '${eff:-?}')."
  echo "         Run 'sudo ../setup_cpu_isolation.sh' first or MCS may stay at 6."
fi

cd "$RAJ"

# Args:  ./run_four_scenarios.sh [SNR] [OUT_DIR] [DURATION]
#   SNR      - dB assigned to a unit tap (default 30),  e.g. 20
#   OUT_DIR  - output folder under raj/ for this batch (default ./plot),
#              e.g. ./plot/snr20  — keeps runs at different SNRs side by side
#   DURATION - traffic-window seconds per run (default 80)
SNR="${1:-30}"
OUT_DIR="${2:-./plot}"
DURATION="${3:-80}"

# Measure the clean UL maximum: saturate the uplink (TCP, no -b) while keeping
# the downlink quiet so the DL flood doesn't starve the UL TCP ACK path.
UL_RATE="sat"
DL_RATE="1M"

echo "=== four scenarios: snr=$SNR out=$OUT_DIR dur=${DURATION}s (ul=$UL_RATE dl=$DL_RATE) ==="

run() {  # <channel> <out-subdir>
  echo "=== [$(date +%H:%M:%S)] $2 : $1  (snr=$SNR dur=${DURATION}s ul=$UL_RATE dl=$DL_RATE) ==="
  python3 raj_tti_experiment.py 1 1 --channel "$1" --snr "$SNR" --duration "$DURATION" \
          --ul-rate "$UL_RATE" --dl-rate "$DL_RATE" --out "$OUT_DIR/$2/"
}

run channel_gt_los.txt    gt_los
run channel_pred_los.txt  pred_los
run channel_gt_nlos.txt   gt_nlos
run channel_pred_nlos.txt pred_nlos

# multiplot reads paths relative to its JSON's own folder, so drop a copy of the
# config into OUT_DIR and point multiplot at it (works for any OUT_DIR).
echo "=== [$(date +%H:%M:%S)] overlay: multiplot ==="
mkdir -p "$OUT_DIR"
MP_JSON="$OUT_DIR/multiplot.json"
[ "$(readlink -f "$MP_JSON")" = "$(readlink -f "$RAJ/plot/multiplot.json")" ] || cp "$RAJ/plot/multiplot.json" "$MP_JSON"
cd "$RPI"
python3 multiplot.py "raj/${OUT_DIR#./}/multiplot.json"

echo "=== done. outputs in $OUT_DIR/ (multiplot*.png, multiplot_results.txt) ==="
