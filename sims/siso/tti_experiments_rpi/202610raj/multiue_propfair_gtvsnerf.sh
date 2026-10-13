#!/bin/bash
# multiue_propfair_gtvsnerf.sh - 2-UE prop-fair, 35 dB anchor, GT vs NeRF.
#
# UE1 = Path B channel, run with BOTH the ground-truth CIR and the NeRF-predicted
# CIR, so we can compare how the predicted channel reproduces the true channel's
# multi-UE throughput. UE2 = constant-SNR competitor swept {35,20,10,5} dB (the
# same sweep as the original 35 dB example).
#
# Both channels are run back-to-back with IDENTICAL settings (fixed iperf port,
# duration, anchor) so the comparison is clean. No OAI code change (UE2 channel is
# a bind-mount; UE2 warmup auto-sized to ~15 dB for RACH).
#
#   ./multiue_propfair_gtvsnerf.sh [DURATION_s]
set -u
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate

EXP=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/202610raj
RPI=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi
cd "$EXP"

SNR=35                                       # UE1 anchor = shared noise anchor
DURATION="${1:-200}"
UE2_SNRS=(35 20 10 5)                        # same sweep as the 35 dB example
declare -A CHAN=( [gt]="20261009/channel_gt_B.txt" [nerf]="20261009/channel_nerf_B.txt" )

echo "=== [$(date +%H:%M:%S)] isolating CPU ==="
sudo "$RPI/setup_cpu_isolation.sh" | tail -1
echo "system.slice cpus = $(cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective) (expect 0-4,10-14)"

# keep nr-softmodem at FIFO:80 for the whole batch
sudo bash -c '
end=$((SECONDS+20000))
while [ $SECONDS -lt $end ]; do
  for p in $(pgrep -f nr-softmodem 2>/dev/null); do
    chrt -p "$p" 2>/dev/null | grep -q FIFO || chrt -f -p 80 "$p" 2>/dev/null && :
  done
  pgrep -f raj_tti_experiment_2ndUEConst >/dev/null || break
  sleep 2
done' >/dev/null 2>&1 &

for ch in gt nerf; do
  for u2 in "${UE2_SNRS[@]}"; do
    OUT="./plot/multiue_propfair_gtvsnerf/ue1${ch}35_ue2const${u2}db"
    echo "=== [$(date +%H:%M:%S)] UE1=${ch} @ ${SNR}dB  vs  UE2 const ${u2}dB  -> $OUT ==="
    python3 raj_tti_experiment_2ndUEConst.py 2 1 \
            --channel "${CHAN[$ch]}" --snr "$SNR" --ue2-snr "$u2" \
            --duration "$DURATION" --ul-rate 50M --ul-proto udp --dl-rate 1M \
            --out "$OUT"
  done
done

echo "=== [$(date +%H:%M:%S)] done. then: python3 plot/multiue_propfair_gtvsnerf/plot_gt_vs_nerf.py"
