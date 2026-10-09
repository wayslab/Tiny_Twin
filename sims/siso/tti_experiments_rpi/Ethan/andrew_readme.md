# Ethan — quick cheat-sheet

Everything you must set + run. Dir: `Tiny_Twin/sims/siso/tti_experiments_rpi/Ethan/`.

## 0. One-time
```bash
# CPU isolation must be on (should print 0-4,10-14)
cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective

# rebuild gNB/UE after any C change (jammer/MIMO/export are already merged)
docker run --rm --entrypoint /bin/bash \
  -v /home/ways_lab/repos/tinytwin-oai:/opt/tt-ran/tt:rw tt-gnb:v2 \
  -c "cd /opt/tt-ran/tt/cmake_targets && ./build_oai --gNB --nrUE"

# generate channel + jammer traces
python3 /home/ways_lab/repos/Tiny_Twin/channel/ethan_chan/generate_ethan.py

# activate venv (numpy/zmq/matplotlib)
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/Ethan
```

## 1. Settings that matter
| what | where | value |
|---|---|---|
| SNR anchor | `--snr` | 30 (unit tap → 30 dB) |
| taps | 2nd positional arg → `--TAP` | 1 for flat; >1 works too (e.g. 16), but the gNB's UL estimator low-pass-filters the CFR so far-apart taps don't appear in the extracted CIR |
| channel file | `--channel` | staged into `channel/channel_real.txt` (+ zero imag) |
| jammer on | `--jam --jgain 200 --jtap 1` | stages `channel/jammer_real.txt`; 0→strong at a set slot |
| gNB conf | `--gnb-conf <path>` | default SISO; 2×2 conf for MIMO |
| **UE antennas** | `--ue-ant N` | **must equal gNB RU nb_tx/nb_rx** (2 for 2×2) |
| capture channel | `--capture-channel` | writes `<out>/chan_cap.txt` (RIC export) |

2×2 gNB conf (`...usrpb210.2x2.ethan.conf`) — the only lines that make it 2×2:
```
pdsch_AntennaPorts_XP = 2;   pusch_AntennaPorts = 2;   do_CSIRS = 1;   # gNBs block
nb_tx = 2;   nb_rx = 2;                                                # RUs block
```
(DL ports come ONLY from `pdsch_AntennaPorts_XP/N1/N2`, not from `nb_tx`.)

## 2. Run the three experiments
```bash
CONF=/home/ways_lab/repos/Tiny_Twin/targets/PROJECTS/GENERIC-NR-5GC/CONF

# 1) baseline seesaw (SISO, time-varying tap)
python3 ethan_tti_experiment.py 1 1 --channel channel_seesaw_real.txt --snr 30 \
        --duration 60 --out ./plot/seesaw/

# 2) full 2x2 MIMO + per-antenna channel extraction (nrx=2)
python3 ethan_tti_experiment.py 1 1 --channel channel_mimo_real.txt --snr 30 \
        --gnb-conf "$CONF/gnb.sa.band78.fr1.106PRB.usrpb210.2x2.ethan.conf" \
        --ue-ant 2 --capture-channel --duration 110 --out ./plot/mimo2x2/

# 3) jammer switches on mid-run (PUSCH SNR drops)
python3 ethan_tti_experiment.py 1 1 --channel channel_jammer_real.txt --snr 30 \
        --jam --jgain 200 --jtap 1 --duration 100 --out ./plot/jammer/

# or all three:  ./run_three_scenarios.sh 30 ./plot 100
```

## 3. Plots
```bash
python3 ../plot.py --snr-file ./plot/<scn>/snr.txt --out ./plot/<scn>/snr_plot.png   # UL metrics + iperf
python3 compare_channel.py --cap ./plot/mimo2x2/chan_cap.txt --out ./plot/mimo2x2/channel_compare.png
```

## 4. Gotchas
- **Real gNB log** = `cmake_targets/ran_build/build/gnb.log` (NOT `logs_gnb/gnb.log`, which is stale). Check it for `nb_tx 2, nb_rx 2`.
- `--ue-ant` must match gNB `nb_tx/nb_rx`, else rfsim desyncs → `write() failed errno(9)` → UE never attaches.
- `--TAP>1` brings up fine (tested at 16). But the stock OAI UL estimator collapses the channel to one delay + low-pass-filters it (`nr_ul_channel_estimation.c:197-239`), so a multi-tap CIR is NOT recoverable from the exported estimate — it reads flat. For true multi-path CIR you need the raw LS / SRS estimate (what the ISAC fork dumps), not `ul_ch_estimates_ext`.
- If a run hangs, tear down: `docker rm -f tt-gnb tt-ue1 edgeric_v2_2 && docker compose -f ../../oai-cn/docker-compose.yaml down`.
- Expected 2×2 proof: RIC export shows `nrx=2`; UL PUSCH SNR ~40 dB (vs ~32 SISO = rx-combining gain).
