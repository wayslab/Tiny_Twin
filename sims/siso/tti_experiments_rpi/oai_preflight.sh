#!/bin/bash
# oai_preflight.sh — find (and optionally stop) OAI RAN runs that would conflict
# with a fresh raj experiment: competing softmodems, duplicate-IMSI UEs, and
# stale tt-* containers. KEEPS the shared 5G core (oai-*), mysql/ims, monitoring,
# and anything unrelated. Safe to run any time.
#
#   oai_preflight.sh            # DRY RUN — just report conflicts
#   oai_preflight.sh --stop     # stop/kill the conflicts (alias: -y)
#
# Why: the raj gNB/UE run as the docker containers tt-gnb / tt-ue*. Anything ELSE
# driving the air interface — a bare-metal nr-softmodem (e.g. the andrewgate gNB),
# a non-core container running nr-uesoftmodem (e.g. ue-gate, which reuses raj's
# IMSI 001010000000011) — collides on CPU AND on the 5G core (same subscriber /
# same CN container names), so the new UE never gets a clean PDU session, iperf
# falls back to the docker bridge, and UL MCS stays pinned at 6.

set -u
STOP=0
case "${1:-}" in --stop|-y|stop) STOP=1 ;; "" ) ;; *) echo "usage: $0 [--stop]"; exit 1 ;; esac

# containers that must stay up (5G core + infra + unrelated); extend as needed
KEEP_RE='^(oai-amf|oai-smf|oai-upf|oai-nrf|oai-ausf|oai-udm|oai-udr|oai-ext-dn|mysql|ims|prometheus|grafana|wayslab-jekyll)$'

found=0
echo "== OAI preflight: scanning for conflicting RAN =="

# 1) bare-metal / host-side softmodems and their docker-exec wrappers.
#    raj runs the gNB in a container, so any HOST softmodem is a foreign run.
for pid in $(pgrep -f 'nr-softmodem|nr-uesoftmodem' 2>/dev/null | sort -un); do
  [[ -r /proc/$pid/cgroup ]] || continue
  grep -q '/docker-' /proc/$pid/cgroup 2>/dev/null && continue   # in-container: handled below
  cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null); [[ -z "$cmd" ]] && continue
  echo "   [host] pid $pid  ${cmd:0:90}"
  found=1
  if [[ $STOP == 1 ]]; then kill "$pid" 2>/dev/null && echo "         -> killed pid $pid"; fi
done

# 2) containers running a softmodem that are NOT part of the 5G core keep-list.
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  echo "$c" | grep -qE "$KEEP_RE" && continue
  if docker top "$c" 2>/dev/null | grep -qE 'nr-softmodem|nr-uesoftmodem'; then
    imsi=$(docker top "$c" 2>/dev/null | grep -oE 'imsi [0-9]+' | head -1)
    echo "   [container] $c  ${imsi:-}"
    found=1
    if [[ $STOP == 1 ]]; then docker stop "$c" >/dev/null 2>&1 && echo "         -> stopped $c"; fi
  fi
done

# 3) stale raj containers left over from an aborted run (even if exited).
for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^tt-gnb$|^tt-ue|^edgeric'); do
  echo "   [stale] $c"
  found=1
  if [[ $STOP == 1 ]]; then docker rm -f "$c" >/dev/null 2>&1 && echo "         -> removed $c"; fi
done

echo "== result =="
if [[ $found == 0 ]]; then
  echo "   clear — no conflicting RAN; safe to start a run."
  exit 0
elif [[ $STOP == 1 ]]; then
  echo "   cleared. (kept 5G core + infra: see KEEP list)"
  exit 0
else
  echo "   conflicts found (dry run). Re-run with --stop to clear them:"
  echo "     $0 --stop"
  exit 1
fi
