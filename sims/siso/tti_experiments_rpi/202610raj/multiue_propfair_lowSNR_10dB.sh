#!/bin/bash
# multiue_propfair_lowSNR_10dB.sh - 2-UE prop-fair, 30 dB anchor variant.
#
# Same as multiue_propfair_lowSNR.sh but with a 30 dB SNR anchor (vs 28 dB), to
# compare the anchor's effect. UE1 = Path B NeRF @ 30 dB; UE2 constant-SNR sweep
# {8, 10, 20, 25} dB (same as the 28 dB set).
#
# No OAI/gNB code change: UE2 gets its own constant channel via a bind-mount of
# channel_real_2.txt; its warmup is sized to ~15 dB at THIS anchor so weak targets
# still RACH. Run with CPU isolation (done here). DURATION >=200 recommended.
#
#   ./multiue_propfair_lowSNR_10dB.sh [DURATION_s]
set -u
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate

EXP=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/202610raj
RPI=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi
cd "$EXP"

UE1_CHANNEL="20261009/channel_nerf_B.txt"   # UE1 = Path B NeRF
SNR=30                                       # UE1 anchor = shared noise anchor
DURATION="${1:-200}"   # >=200 so UE2's constant region plays after the 60s warmup
UE2_SNRS=(8 10 20 25)                        # UE2 constant SNR sweep (same as 28 dB set)

echo "=== [$(date +%H:%M:%S)] isolating CPU ==="
sudo "$RPI/setup_cpu_isolation.sh" | tail -1
echo "system.slice cpus = $(cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective) (expect 0-4,10-14)"

# keep nr-softmodem at FIFO:80 for the whole batch
sudo bash -c '
end=$((SECONDS+12000))
while [ $SECONDS -lt $end ]; do
  for p in $(pgrep -f nr-softmodem 2>/dev/null); do
    chrt -p "$p" 2>/dev/null | grep -q FIFO || chrt -f -p 80 "$p" 2>/dev/null && :
  done
  pgrep -f raj_tti_experiment_2ndUEConst >/dev/null || break
  sleep 2
done' >/dev/null 2>&1 &

for u2 in "${UE2_SNRS[@]}"; do
  OUT="./plot/multiue_propfair_lowSNR_10dB/ue1nerf30_ue2const${u2}db"
  echo "=== [$(date +%H:%M:%S)] UE2 const SNR = ${u2} dB  (UE1 NeRF @ ${SNR} dB)  -> $OUT ==="
  python3 raj_tti_experiment_2ndUEConst.py 2 1 \
          --channel "$UE1_CHANNEL" --snr "$SNR" --ue2-snr "$u2" \
          --duration "$DURATION" --ul-rate 50M --ul-proto udp --dl-rate 1M \
          --out "$OUT"
done

echo "=== [$(date +%H:%M:%S)] done. then: python3 plot/multiue_propfair_lowSNR_10dB/plot_ul_iperf.py"
