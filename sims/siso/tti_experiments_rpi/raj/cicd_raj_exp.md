# cicd_raj_exp — recreate the raj 4-scenario SNR experiment from scratch

Produces the `snr-30dB`-style UL MCS/throughput plots. Without CPU isolation
UL MCS stays pinned at 6; with it, MCS reaches 28.

## 0. Paths
```
RPI=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi
RAJ=$RPI/raj
VENV=/home/ways_lab/repos/venv/sub6isac_env
```

## 1. Isolate CPU cores (ONCE per boot, as root)
Runtime-only, resets every reboot. Must run before launching the experiment.
```bash
sudo $RPI/setup_cpu_isolation.sh
```
Verify — must print `0-4,10-14`:
```bash
cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective
```
If it prints `0-19`, isolation is NOT active → MCS will pin at 6. Re-run step 1.

## 2. Launch the keep-RT helper (as root, background)
Pins `nr-softmodem` to SCHED_FIFO:80 as each scenario's gNB starts. Leave it
running for the whole batch; it self-exits when the run finishes.
```bash
sudo bash -c '
end=$((SECONDS+1200))
while [ $SECONDS -lt $end ]; do
  for p in $(pgrep -f nr-softmodem 2>/dev/null); do
    chrt -p "$p" 2>/dev/null | grep -q FIFO || { chrt -f -p 80 "$p" && echo "FIFO:80 $p"; }
  done
  pgrep -f run_four_scenarios >/dev/null || break
  sleep 2
done' &
```

## 3. Run the 4 scenarios
Self-manages the OAI stack (CN + gNB pinned to cores 5-8, UE to 9,15-19 + iperf).
No containers need to be started manually; none should be running beforehand.
```bash
source $VENV/bin/activate
cd $RAJ
./run_four_scenarios.sh 30 ./plot/snr_30dB_cpuvalidation 80
#                         ^SNR ^out dir                     ^dur(s)/scenario
```
Runs gt_los, pred_los, gt_nlos, pred_nlos (~3-4 min each) then the overlay.
Takes ~15 min. Output goes to `$RAJ/plot/snr_30dB_cpuvalidation/`.

## 4. Verify it worked (MCS above 9, not pinned at 6)
```bash
for s in gt_los pred_los gt_nlos pred_nlos; do
  awk '/^UL MCS:/{n++;s+=$3;if($3>m)m=$3} END{printf "%-10s mean=%.1f max=%d\n","'"$s"'",s/n,m}' \
    plot/snr_30dB_cpuvalidation/$s/snr.txt
done
```
PASS: each `mean` ~19-26 and `max=28`.  FAIL: `mean≈6`, `max≤9` → isolation
was not active; redo steps 1-2 and re-run.

## 5. Outputs
In `$RAJ/plot/snr_30dB_cpuvalidation/`:
- `multiplot.png`, `multiplot_iperf.png`, `multiplot_tpt_smoothed.png` — overlays
- `multiplot_results.txt` — per-channel UL/DL goodput JSON
- `<scenario>/snr.txt`, `tti.txt`, `iperf_*` — raw per-scenario data

## Notes
- GRUB already has `isolcpus=5-9,15-19 nohz_full=... rcu_nocbs=...`, but that
  alone is NOT enough — step 1's systemd-slice eviction + IRQ move and step 2's
  FIFO scheduling are what clear the BLER floor.
- To change SNR/output/duration: `./run_four_scenarios.sh <SNR> <outdir> <dur>`.
- First scenario's mean MCS may trail the rest if FIFO lands a second after the
  gNB starts; re-run that scenario alone if you need a tighter mean.
