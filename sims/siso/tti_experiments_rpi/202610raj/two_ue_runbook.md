# two_ue_runbook — 2-UE proportional-fair experiment

Two UEs share the cell so you can see how the gNB's proportional-fair (PF)
scheduler splits resources:

- **UE1** = Path B NeRF channel, anchored at **35 dB** (time-varying, fades).
- **UE2** = a **constant-SNR** user, swept at **35 / 20 / 5 dB** (higher / ~equal /
  lower than UE1's mean) — the three PF-sharing cases.

No OAI/gNB modification is needed: per-UE channels work by bind-mounting a
different `channel_real.txt` into each UE container (handled by the script).
UE2's constant channel is prefixed with a strong 60 s **attach-warmup** (then it
drops to the target), so even weak anchors (5/10 dB) can RACH — a bare constant
10 dB fails to attach. Use `--duration >= 200` so UE2's constant region plays
past the warmup.

## Paths
```
EXP=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/202610raj
RPI=/home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi
VENV=/home/ways_lab/repos/venv/sub6isac_env
```

## Prereqs (once)
```bash
# channels exist (built earlier from AI53_017.zip); rebuild only if missing:
source $VENV/bin/activate
python3 /home/ways_lab/repos/Tiny_Twin/channel/raj_chan/20261009/generate_20261009.py
```

## Run everything (the sweep: UE2 @ 35, 20, 5 dB)
`multiue_propfair_exp.sh` applies CPU isolation + FIFO:80 itself, then runs all
three. Takes ~3-4 min per anchor (2 UEs run slower than 1).
```bash
cd $EXP
./multiue_propfair_exp.sh            # default 150 s traffic/run
sudo ./multiue_propfair_exp.sh 280        # optional: longer window for full motion
```

## Run a single case manually (custom UE2 SNR / duration)
```bash
sudo $RPI/setup_cpu_isolation.sh                       # once per boot
source $VENV/bin/activate && cd $EXP
python3 raj_tti_experiment_2ndUEConst.py 2 1 \
  --channel 20261009/channel_nerf_B.txt --snr 35 \
  --ue2-snr 20 --duration 200 --ul-rate 50M --ul-proto udp --dl-rate 1M \
  --out ./plot/multiue_propfair/ue1nerf35_ue2const20db
```
- `--snr 35`      = UE1 anchor (= shared noise anchor).
- `--ue2-snr X`   = UE2 constant SNR; UE2 gets tap `10^((X-35)/20)` (1.0@35, 0.178@20, 0.0316@5).
- `2 1`           = positional (UEs is forced to 2 internally; tap=1).

## Outputs
```
$EXP/plot/multiue_propfair/ue1nerf35_ue2const{35,20,5}db/
    two_ue_metrics.png     both UEs' UL SNR / MCS / bytes-per-TB / BLER (by RNTI)
    snr.txt                gNB UL log, BOTH UEs keyed by RNTI
    iperf_ul_ue1.txt        UE1 UL goodput (NeRF)
    iperf_ul_ue2.txt        UE2 UL goodput (constant)
    iperf_dl_ue{1,2}.txt, tti.txt, iperf_cpumem.txt
```
In `two_ue_metrics.png` the UE with ~flat PUSCH SNR (low variance) is UE2
(constant); the varying one is UE1 (NeRF).

## What to look for (PF sharing)
- **UE2 @ 35 dB (higher):** UE2 carries the cell during UE1's fades; highest cell
  aggregate; ~fair throughput split.
- **UE2 @ 20 dB (~equal):** multi-user diversity — PF serves UE1 on its peaks,
  UE2 fills the troughs.
- **UE2 @ 5 dB (lower):** PF still serves the weak UE2 for fairness at low
  efficiency -> drags cell aggregate down, steals from UE1. During UE1's deep
  fade both are weak -> aggregate collapses.
- Watch UE1's deep fade (its PUSCH SNR dips, BLER spikes, bytes/TB crashes) while
  UE2 stays steady — the whole point of the comparison.

## Notes / troubleshooting
- CPU isolation resets each boot; the script re-applies it. If MCS sits at 6,
  isolation/FIFO isn't active (see cicd/andrew readmes).
- 2 UEs => more L1 work => sim runs slower than the ~0.6x single-UE rate; use a
  longer `--duration` if you want the full NeRF motion to play.
- Separate UEs by RNTI in snr.txt; iperf is already per-UE (ue1/ue2 files).
- OAI is stock (no gNB code change); the per-UE channel is purely a docker mount.
