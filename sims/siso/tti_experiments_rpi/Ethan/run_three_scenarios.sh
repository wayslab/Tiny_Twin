#!/bin/bash
# run_three_scenarios.sh — Ethan's three channel scenarios, back to back.
#
#   ./run_three_scenarios.sh [SNR] [OUT_DIR] [DURATION]
#
# PREREQ: CPU isolation this boot (cpuset.cpus.effective == 0-4,10-14), else UL
# MCS pins at 6. The Ethan merge needs the gNB/UE rebuilt once:
#   docker run --rm --entrypoint /bin/bash \
#     -v /home/ways_lab/repos/tinytwin-oai:/opt/tt-ran/tt:rw tt-gnb:v2 \
#     -c "cd /opt/tt-ran/tt/cmake_targets && ./build_oai --gNB --nrUE"
#
# Each run is ~3-4 min wall-clock (core/gNB/UE up, traffic window, teardown).
set -u
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate

ETHAN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="/home/ways_lab/repos/Tiny_Twin/targets/PROJECTS/GENERIC-NR-5GC/CONF"
MIMO_CONF="$CONF_DIR/gnb.sa.band78.fr1.106PRB.usrpb210.2x2.ethan.conf"

SNR="${1:-30}"
OUT_DIR="${2:-./plot}"
DURATION="${3:-80}"

eff=$(cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective 2>/dev/null || echo "")
[[ "$eff" == "0-4,10-14" ]] || echo "WARNING: CPU isolation may be off (cpus='${eff:-?}'); UL MCS may pin at 6."

cd "$ETHAN"
echo "=== three scenarios: snr=$SNR out=$OUT_DIR dur=${DURATION}s ==="

# 1. BASELINE — seesaw SISO channel, no jammer, no MIMO.
echo "=== [$(date +%H:%M:%S)] 1/3 seesaw (SISO baseline) ==="
python3 ethan_tti_experiment.py 1 1 --channel channel_seesaw_real.txt \
        --snr "$SNR" --duration "$DURATION" --out "$OUT_DIR/seesaw/"

# 2. Full 2x2 MIMO — gNB nb_tx=2/nb_rx=2 ($MIMO_CONF) + UE 2 antennas
#    (--ue-ant 2, both ends of the rfsim socket must match). Captures the
#    per-antenna UL channel the gNB exports (nrx=2) and compares to the staged CIR.
echo "=== [$(date +%H:%M:%S)] 2/3 full 2x2 MIMO + channel extraction ==="
python3 ethan_tti_experiment.py 1 1 --channel channel_mimo_real.txt \
        --snr "$SNR" --duration "$DURATION" --gnb-conf "$MIMO_CONF" \
        --ue-ant 2 --capture-channel --out "$OUT_DIR/mimo2x2/"
python3 compare_channel.py --cap "$OUT_DIR/mimo2x2/chan_cap.txt" \
        --out "$OUT_DIR/mimo2x2/channel_compare.png" || true

# 3. JAMMER — flat channel, jammer switches ON at the trace midpoint.
echo "=== [$(date +%H:%M:%S)] 3/3 jammer (switches on at T) ==="
python3 ethan_tti_experiment.py 1 1 --channel channel_jammer_real.txt \
        --snr "$SNR" --duration "$DURATION" --jam --jgain 200 --jtap 1 \
        --out "$OUT_DIR/jammer/"

# plots (6-panel UL metrics + iperf) for each, via the shared plotter one level up.
for s in seesaw mimo jammer; do
  python3 ../plot.py --snr-file "$OUT_DIR/$s/snr.txt" --out "$OUT_DIR/$s/snr_plot.png" || true
done
echo "=== done. outputs under $OUT_DIR/{seesaw,mimo,jammer}/ ==="
