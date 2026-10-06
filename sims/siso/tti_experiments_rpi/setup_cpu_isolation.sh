#!/bin/bash
# setup_cpu_isolation.sh — shield the OAI gNB/UE cores from OS jitter.
#
# Run ONCE per boot, BEFORE launching an experiment:
#     sudo ./setup_cpu_isolation.sh
#
# Why: docker `cpuset` only pins OAI *to* cores 5-9,15-19 (the X925 perf cores);
# it does not evict the kernel/other tasks *from* them. Leftover scheduler
# preemption + device IRQs + reschedule IPIs jitter the L1 thread, producing a
# ~10% first-transmission BLER floor that pins UL MCS at 6 regardless of SNR.
# This moves IRQs and all host tasks onto the housekeeping cores, leaving the
# OAI cores clean. Effects are runtime-only (reset on reboot) — re-run each boot.
#
# For *permanent* isolation (also stops the timer tick / RCU), add to
# GRUB_CMDLINE_LINUX and reboot:
#   isolcpus=5-9,15-19 nohz_full=5-9,15-19 rcu_nocbs=5-9,15-19 irqaffinity=0-4,10-14

set -u

# Cores used by the experiment (gNB 5-8, UE 9,15-19) and the rest (housekeeping).
ISOLATED="5-9,15-19"
HOUSEKEEPING="0-4,10-14"

if [[ $EUID -ne 0 ]]; then
  echo "error: must run as root — try:  sudo $0" >&2
  exit 1
fi

echo "== 1. moving device IRQs off isolated cores ($ISOLATED) =="
moved=0 failed=0
for f in /proc/irq/*/smp_affinity_list; do
  if echo "$HOUSEKEEPING" > "$f" 2>/dev/null; then
    moved=$((moved+1))
  else
    failed=$((failed+1))   # per-CPU / managed IRQs can't be moved — expected
  fi
done
echo "   moved $moved IRQ(s); $failed pinned/managed (left as-is, normal)"

echo "== 2. evicting host tasks to housekeeping cores ($HOUSEKEEPING) =="
for slice in system.slice user.slice init.scope; do
  if systemctl set-property --runtime "$slice" "AllowedCPUs=$HOUSEKEEPING" 2>/dev/null; then
    echo "   $slice -> $HOUSEKEEPING"
  else
    echo "   WARN: could not set AllowedCPUs on $slice" >&2
  fi
done

echo "== 3. raising priority of any running nr-softmodem (best effort) =="
pids=$(pgrep -f nr-softmodem || true)
if [[ -n "$pids" ]]; then
  for p in $pids; do chrt -f -p 80 "$p" 2>/dev/null && echo "   chrt FIFO:80 pid $p"; done
else
  echo "   (nr-softmodem not running yet — skip; re-run after the gNB starts if desired)"
fi

echo "== verify =="
eff=$(cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective 2>/dev/null || echo "n/a")
echo "   system.slice effective cpus: $eff   (expect $HOUSEKEEPING)"
echo "   tip: during a run, 'grep IPI1 /proc/interrupts' — cpu5-8 columns should stay ~flat"
echo "done. OAI cores $ISOLATED are shielded for this boot."
