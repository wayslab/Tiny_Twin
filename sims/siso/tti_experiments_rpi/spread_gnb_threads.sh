#!/bin/bash
# spread_gnb_threads.sh — WORKAROUND for boot isolcpus killing scheduler load
# balancing on the isolated cores. Docker pins tt-gnb to cpuset 5,6,7,8 but with
# isolcpus=5-9,15-19 the kernel never migrates threads, so ALL ~26 gNB threads
# pile onto core 5 (6,7,8 idle) -> core 5 saturates -> PUSCH decode misses its
# deadline -> ~15% BLER at clean SNR -> UL MCS locked at 6.
#
# This watcher round-robin-pins each gNB thread to a SINGLE core in {5,6,7,8}
# (single-core affinity needs no balancer), and re-applies as OAI spawns threads.
# The heavy L1 main thread effectively gets its own core -> deadlines met.
#
#   sudo ./spread_gnb_threads.sh         # run in a 2nd terminal during the experiment
#   sudo ./spread_gnb_threads.sh --once  # apply once and exit
#
# The real fix is to drop boot isolcpus (revert GRUB) and use the runtime
# isolate_oai_cores.sh instead; this is the keep-isolcpus stopgap.
set -u
CORES=(5 6 7 8)
MATCH='106PRB'          # the raj gNB nr-softmodem

spread(){
  local gpid i=0 tid core n=0
  gpid=$(pgrep -f "$MATCH" | head -1) || return 1
  [[ -z "$gpid" ]] && return 1
  for tid in $(ls /proc/$gpid/task 2>/dev/null); do
    core=${CORES[$((i % ${#CORES[@]}))]}
    taskset -pc "$core" "$tid" >/dev/null 2>&1 && { i=$((i+1)); n=$((n+1)); }
  done
  echo "  spread $n gNB threads (pid $gpid) across ${CORES[*]}"
}

[[ $EUID -ne 0 ]] && { echo "run with sudo"; exit 1; }

if [[ "${1:-}" == "--once" ]]; then spread; exit 0; fi

echo "== watching for gNB ($MATCH); re-spreading across ${CORES[*]} every 2s =="
while true; do spread || echo "  (no gNB yet)"; sleep 2; done
