# raj — TTI experiment with raj_chan CIR + Python-set noise

## Purpose
Plays a measured/predicted CIR trace (a slowly moving robot) through OAI rfsim
(SISO) and measures UL/DL performance. The channel **noise power is set entirely
from Python** (no `.conf` edits) so a unit tap maps to a chosen SNR. The driver
is self-contained: all shared infra (edgeric-v2, oai-cn, conf, channel, logs) is
referenced by absolute path, and it runs with `cwd = raj/`, so every output
lands under `raj/plot/`.

Location: `sims/siso/tti_experiments_rpi/raj/`

---

## Step 1 — CPU isolation (REQUIRED, once per boot)
**Do this first or every run is wrong:** without it, UL MCS stays pinned at **6**
(OS/IRQ jitter on the gNB cores causes a ~10% first-transmission BLER floor that
the link-adaptation never climbs out of). `cpuset` only pins OAI *to* cores
5–9/15–19; it does not keep the kernel *off* them, so run the shared shielding
script:
```bash
sudo ../setup_cpu_isolation.sh
```
Not persistent — re-run after each reboot. Verify (should print `0-4,10-14`):
```bash
cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective
```
(Details and the permanent GRUB/reboot alternative are in [CPU isolation](#cpu-isolation-details).)

---

## Step 2 — Run a single trial
```bash
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/raj

# list available channels
python3 raj_tti_experiment.py --list

# <#UE> <#taps>; ground-truth LOS, unit tap -> 30 dB, outputs to ./plot/gt_los/
python3 raj_tti_experiment.py 1 1 --channel channel_gt_los.txt --snr 30 --out ./plot/gt_los/

# find the clean UL maximum (saturate UL TCP, keep DL quiet)
python3 raj_tti_experiment.py 1 1 --channel channel_gt_los.txt --snr 30 \
        --ul-rate sat --dl-rate 1M --out ./plot/gt_los_ulmax/

# play the FULL NLOS trajectory (its trace is longer, so it needs more time)
python3 raj_tti_experiment.py 1 1 --channel channel_gt_nlos.txt --snr 30 \
        --duration 250 --out ./plot/gt_nlos/
```
A run lasts `--duration` seconds of traffic + ~1 min Docker up/down. The channel
is consumed one row per slot, so `--duration` sets **how much of the trace
plays**: LOS (145k rows) finishes in ~60 s, NLOS (413k rows) needs ~250 s for its
full trajectory (otherwise only the first ~25% plays).

| flag | meaning | default |
|------|---------|---------|
| `ue tap` | number of UEs, number of channel taps (`--TAP`) | required |
| `--channel` | real-taps file (looked up in `raj_chan/` then `channel/`) | `channel_gt_los.txt` |
| `--snr` | SNR (dB) assigned to a unit tap | `30` |
| `--duration` | traffic-window seconds (how much of the trace plays) | `60` |
| `--ul-rate` | UL iperf offered rate (e.g. `8M`); `sat` = TCP saturates to find the max | `8M` |
| `--dl-rate` | DL iperf offered rate (e.g. `8M`); `sat` = UDP floods (`-b 1000M`) | `8M` |
| `--out` | output directory for this run | `./plot/ue<#UE>_<#tap>/` |
| `--list` | list available channels and exit | — |

Then plot it (plotter lives one level up):
```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi
python3 plot.py --snr-file raj/plot/gt_los/snr.txt --out raj/plot/gt_los/snr_plot.png
```

---

## Step 3 — Run all four scenarios + overlay
Runs gt_los, pred_los, gt_nlos, pred_nlos (UL-saturated) back to back, then
overlays them with `multiplot.py`:
```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/raj
./run_four_scenarios.sh 20 ./plot/snr-20dB
```
Each run uses `--duration 250` (set in the script) so NLOS plays its full
trajectory; ~25 min total. Outputs in the chosen `OUT_DIR`:
`multiplot.png` (6-panel metrics), `multiplot_tpt_smoothed.png`,
`multiplot_iperf.png` (UL/DL goodput), `multiplot_results.txt` (JSON summary of
UL/DL mean/peak/total throughput per channel).

Edit `UL_RATE` / `DL_RATE` at the top of the script to change the load profile.

---

# Reference

## Outputs (per run, in `--out`)
| file | content |
|------|---------|
| `snr.txt` | per-RNTI UL SNR / MCS / BLER / CQI / HARQ / TPT (MAC scheduler) |
| `tti.txt` | per-TTI timestamps (TTI-duration / CCDF analysis) |
| `iperf_ul_ue<N>.txt` | **UL application goodput vs time** (TCP, received at `oai-ext-dn`) |
| `iperf_dl_ue<N>.txt` | **DL application goodput vs time** (UDP, received at the UE) |
| `iperf_cpumem.txt` | `top` snapshot during iperf |
| `snr_plot.png` / `iperf_throughput.png` | written by `plot.py` |

`plot.py` draws a 6-panel UL-vs-TTI figure (TPT bytes/TB, MCS, BLER, PUSCH_SNR,
CQI, HARQ round) plus a DL/UL `iperf_throughput.png`.

### MAC `TPT` vs. iperf goodput — they measure different things
- `snr.txt` **`TPT`** = MAC `ul_thr_ue`, an EWMA of granted **transport-block
  size in bytes** (labelled "UL bytes per TB"). It is **not kbps** — it's a size,
  and it counts HARQ retransmissions + BSR-poll grants.
- `iperf_*` files = real **application goodput** (kbps) at the receiver, so
  retransmissions are excluded. **Use iperf for throughput numbers.**
- Relationship: `rate ≈ bytes/TB × TBs_per_second × 8`. TB counts are reported in
  `gnb.log` as `ulsch_rounds R0/R1/R2/R3` (R0 = new TBs, R1+R2+R3 = retx).

> Note: rfsim is **not real-time** (~60% here), so iperf seconds and TTI slots are
> decoupled — `TTI ≈ seconds × (max_TTI / iperf_duration)`, not × 2000. Absolute
> Mbps scales with sim speed, so treat it as comparative, not a hardware rate.

> iperf capture: receivers write with `stdbuf -oL` (line-buffered) and are
> `docker cp`'d out before teardown. Needs `stdbuf` (coreutils) + a
> dynamically-linked iperf in the images.

## Channel taps (`channel/raj_chan/*.txt`)
Built by `channel/raj_chan/generate_raj.py` from the sample CIR in
`Downloads/raj_cir`. Each source CSV is a single-tap CIR magnitude sampled
**1 sample/second**. One channel row is consumed per gNB **slot** (0.5 ms at
numerology 1), so each 1 s sample is interpolated to **2000 rows/s**. Each trace
is prefixed with a **25 000-row warmup** (holds the first sample) covering
RACH/RRC/PDU setup so the robot's motion starts when traffic starts.

| file | source | warmup | motion | total rows |
|------|--------|--------|--------|------------|
| `channel_gt_los.txt`    | ground-truth LOS   | 25000 | 120001 | 145001 |
| `channel_gt_nlos.txt`   | ground-truth NLOS  | 25000 | 388001 | 413001 |
| `channel_pred_los.txt`  | predicted LOS      | 25000 | 120001 | 145001 |
| `channel_pred_nlos.txt` | predicted NLOS     | 25000 | 388001 | 413001 |

The chosen file is staged into `channel/channel_real.txt` (+ a zeros
`channel_imag.txt`); these are the exact files the rfsim C opens. When the trace
ends, the C re-inits taps to `{1,0,…}` — a marker of where the data ends.
Regenerate (edit `TTI_S` / `WARMUP_TTI` at the top):
```bash
python3 ../../../../channel/raj_chan/generate_raj.py
```

## Noise power (command-line config override)
OAI rfsim adds AWGN per sample; for a single real tap `h`:
```
SNR_dB(h) = 20*log10(h) + ploss_dB - 2*noise_power_dB - 3.01      (ploss_dB=15)
```
The driver sets `noise_power_dB = (ploss_dB - 3.01 - S) / 2` for `--snr S` and
injects it on the UE entrypoint (no conf edit):
```
--channelmod.modellist_rfsimu_1.[0].noise_power_dB <N>   # [0]=DL, [1]=UL (same)
```
Since signal power scales as `h²`, SNR drops 20 dB per 10× tap. With `--snr 30`:
unit tap → 30 dB, 0.1 → 10 dB, 0.01 → −10 dB (`noise_power_dB = -9.005`). The
`3.01` I/Q term carries ~±3 dB uncertainty — confirm from the gNB log's
`output power` / `noise coeff` and nudge `QUAD_OFFSET_DB` if needed.

## CPU isolation (details)
Symptom if skipped: UL MCS pinned at 6, ~10% BLER regardless of SNR, because OS
scheduler preemption + device IRQs + reschedule IPIs jitter the L1 thread →
~10% first-tx BLER floor → OLLA (dead-band 0.05–0.15) never raises MCS.
`sudo ../setup_cpu_isolation.sh` moves IRQs off the cores, evicts host tasks to
cores 0–4,10–14, and raises the gNB priority. Verify with
`grep IPI1 /proc/interrupts` (cpu5–8 should stay near-flat during a run).
Permanent alternative (survives reboot): add
`isolcpus=5-9,15-19 nohz_full=5-9,15-19 rcu_nocbs=5-9,15-19 irqaffinity=0-4,10-14`
to `GRUB_CMDLINE_LINUX`, then `sudo update-grub && sudo reboot`. (A kernel update
wiped the previous boot-param isolation, which is why a run that worked a month
ago regressed to MCS 6.)

## Rebuild after C changes
```bash
docker run --rm -v /home/ways_lab/repos/tinytwin-oai:/opt/tt-ran/tt:rw tt-gnb:v2 \
  bash -c "cd /opt/tt-ran/tt/cmake_targets && ./build_oai --gNB --nrUE"
```
