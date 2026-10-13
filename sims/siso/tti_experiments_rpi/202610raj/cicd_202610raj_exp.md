# cicd_202610raj_exp — AI53_017 path A/B (gt/2nn/nerf) 6-scenario experiment

Same experiment as `raj/` but driven by the AI53_017 CIR data: two robot paths
(A, B) × three channel methods (gt, 2nn, nerf) = 6 scenarios. Without CPU
isolation UL MCS pins at 6; with it, MCS reaches 28.

## 0. Paths
```
RPI=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi
EXP=$RPI/202610raj
VENV=/home/ways_lab/repos/venv/sub6isac_env
GEN=/home/ways_lab/repos/Tiny_Twin/channel/raj_chan/20261009/generate_20261009.py
ZIP=/home/ways_lab/Downloads/raj_cir/AI53_017.zip
```

## 1. Build the channels from the zip (ONCE; re-run if the zip changes)
Extracts AI53_017.zip and writes 6 channel traces into
`channel/raj_chan/20261009/channel_{gt,2nn,nerf}_{A,B}.txt`.
```bash
source $VENV/bin/activate
python3 $GEN
```
Trace lengths: path A = 121.5 s (12.5 s warmup + 109 s motion, 243001 rows),
path B = 118.5 s (12.5 s warmup + 106 s motion, 237001 rows), 1 row / 0.5 ms slot.
To change trace length, edit `SAMPLE_PERIOD_S` in the generator (1.0 → 2000
rows/sample; scales linearly) and re-run. Keep `TTI_S = 0.0005` (30 kHz SCS).

## 2. Isolate CPU cores (ONCE per boot, as root)
Runtime-only, resets every reboot. Must run before the experiment or MCS pins at 6.
```bash
sudo $RPI/setup_cpu_isolation.sh
cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective   # must print 0-4,10-14
```
If it prints `0-19`, isolation is NOT active → re-run.

## 3. Launch the keep-RT helper (as root, background)
Pins `nr-softmodem` to SCHED_FIFO:80 as each scenario's gNB starts; self-exits
when the run finishes. (The "No such process" lines are harmless — the keeper
also matches transient launcher strings.)
```bash
sudo bash -c '
end=$((SECONDS+1800))
while [ $SECONDS -lt $end ]; do
  for p in $(pgrep -f nr-softmodem 2>/dev/null); do
    chrt -p "$p" 2>/dev/null | grep -q FIFO || { chrt -f -p 80 "$p" && echo "FIFO:80 $p"; }
  done
  pgrep -f run_six_scenarios >/dev/null || break
  sleep 2
done' &
```

## 4. Run the 6 scenarios (~20 min)
Self-manages the OAI stack (CN + gNB on cores 5-8, UE on 9,15-19 + iperf). No
containers need to be started manually; none should be running beforehand.
```bash
source $VENV/bin/activate
cd $EXP
./run_six_scenarios.sh 30 ./plot/snr-30dB 80
#                       ^SNR ^out dir        ^dur(s)/scenario
```
Scenarios → channels:
```
pathA_gt   20261009/channel_gt_A.txt      pathB_gt   20261009/channel_gt_B.txt
pathA_2nn  20261009/channel_2nn_A.txt     pathB_2nn  20261009/channel_2nn_B.txt
pathA_nerf 20261009/channel_nerf_A.txt    pathB_nerf 20261009/channel_nerf_B.txt
```

## 5. Verify it worked (MCS above 9, not pinned at 6)
```bash
cd $EXP
for s in pathA_gt pathA_2nn pathA_nerf pathB_gt pathB_2nn pathB_nerf; do
  awk '/^UL MCS:/{n++;s+=$3;if($3>m)m=$3} END{printf "%-12s mean=%.1f max=%d\n","'"$s"'",s/n,m}' \
    plot/snr-30dB/$s/snr.txt
done
```
PASS: each `mean` well above 9 and `max=28`. FAIL: `mean≈6`, `max≤9` → isolation
not active; redo steps 2-3 and re-run.

### RESOLVED: max-start warmup makes all 6 scenarios produce data
Earlier runs lost pathA_nerf (and gave bogus iperf for 2NN/NeRF at low anchors)
because the warmup held each trace's own weak first sample (nerf 0.034 ->
0.55 dB at 30 dB), too weak for the UE to attach. FIX (in generate_20261009.py):
the warmup now holds the STRONGEST starting tap across all 6 sets (0.103712 ->
~10/15/25 dB at 30/35/45 dB) for every trace, so every scenario attaches
reliably during the long warmup; the real per-trace motion (incl. nerf_A's deep
fade) then plays under traffic. Result: 6/6 scenarios attach at all three
anchors (0 "oaitun did NOT come up" warnings), nerf_A now shows its fade as an
MCS dip instead of an empty plot. Long-warmup results live in
`plot/snr-{30,35,45}dB_lw/`.

### iperf measurement fix (tunnel-wait)
The engine now waits for each UE's `oaitun_ue1` (10.0.0.x) to come up and clears
stale iperf before starting traffic. Without it, the DL server could not bind
10.0.0.x (DL=0) and the UL client fell back to the docker bridge, logging a
bogus ~40 Gbit/s wire-speed flow instead of real radio goodput — this hit the
2NN/NeRF scenarios in the 30/35 dB runs. After the fix (45 dB run), every
connected scenario reports realistic UL (13-23 Mbps mean) and DL ~1 Mbps.
Scenarios where the WARN fires have unreliable iperf and should be treated like
an RLF for throughput.

## 6. Outputs
In `$EXP/plot/snr-30dB/`:
- `multiplot.png`, `multiplot_iperf.png`, `multiplot_tpt_smoothed.png` — 6-trace overlays
- `multiplot_results.txt` — per-channel UL/DL goodput JSON
- `<scenario>/snr.txt`, `tti.txt`, `iperf_*` — raw per-scenario data

## Notes
- GRUB already has `isolcpus=5-9,15-19 nohz_full=... rcu_nocbs=...`, but that
  alone is NOT enough — step 2's systemd-slice eviction + IRQ move and step 3's
  FIFO scheduling are what clear the BLER floor.
- Change SNR/out/duration: `./run_six_scenarios.sh <SNR> <outdir> <dur>`. Keep
  duration ≤ ~100 s so it fits inside the path-B trace (118.5 s).
- The engine `raj_tti_experiment.py` is a verbatim copy of `raj/`'s; it chdirs to
  its own folder, so all artifacts stay under `202610raj/`.
