#!/bin/bash
# multiue_propfair_lowSNR.sh - 2-UE proportional-fair experiment, LOW anchor.
#
# Same as multiue_propfair_exp.sh but with a 28 dB SNR anchor (vs 35 dB) and a
# UE2 constant-SNR sweep of {8, 10, 20, 25} dB (spanning well below to near the
# anchor). UE1 = Path B NeRF @ 28 dB.
#
# No OAI/gNB code change: UE2 gets its own constant channel by bind-mounting
# channel_real_2.txt into its container (handled by the python script). UE2's
# channel has a strong attach-warmup sized to ~15 dB at THIS anchor, then drops
# to its constant target, so weak anchors (8/10 dB) can still RACH. Run with CPU
# isolation (done here) or MCS pins at 6. DURATION should play past the 60s
# warmup (>=200 recommended).
#
#   ./multiue_propfair_lowSNR.sh [DURATION_s]
set -u
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate

EXP=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/202610raj
RPI=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi
cd "$EXP"

UE1_CHANNEL="20261009/channel_nerf_B.txt"   # UE1 = Path B NeRF
SNR=28                                       # UE1 anchor = shared noise anchor
DURATION="${1:-200}"   # >=200 so UE2's constant region plays after the 60s warmup
UE2_SNRS=(8 10 20 25)                        # UE2 constant SNR sweep (low anchor)

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
  OUT="./plot/multiue_propfair_lowSNR/ue1nerf28_ue2const${u2}db"
  echo "=== [$(date +%H:%M:%S)] UE2 const SNR = ${u2} dB  (UE1 NeRF @ ${SNR} dB)  -> $OUT ==="
  python3 raj_tti_experiment_2ndUEConst.py 2 1 \
          --channel "$UE1_CHANNEL" --snr "$SNR" --ue2-snr "$u2" \
          --duration "$DURATION" --ul-rate 50M --ul-proto udp --dl-rate 1M \
          --out "$OUT"
done

echo "=== [$(date +%H:%M:%S)] done. per-run outputs in plot/multiue_propfair_lowSNR/*/ :"
echo "    snr.txt (both UEs by RNTI), iperf_ul_ue1.txt (UE1), iperf_ul_ue2.txt (UE2),"
echo "    two_ue_metrics.png (both UEs' UL SNR/MCS/TPT/BLER overlaid)"
echo "    then: python3 plot/multiue_propfair_lowSNR/plot_ul_iperf.py"
