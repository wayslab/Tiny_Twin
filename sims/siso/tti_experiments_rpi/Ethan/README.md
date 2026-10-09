# Ethan — jammer / MIMO / channel-extraction experiments

Three TTI experiments that exercise the mimoric2 features merged into TinyTwin
(jammer, MIMO 4×4 channel replay, and the MIMO-RIC per-antenna UL channel
export). Modeled on the sibling `raj/` experiment: self-contained driver, all
shared infra referenced by absolute path, every output under `Ethan/plot/`.

| # | scenario | channel | gNB conf | jammer | what it shows |
|---|----------|---------|----------|--------|---------------|
| 1 | **seesaw** | `channel_seesaw_real.txt` (SISO, 1 tap, slow swing) | SISO 106PRB | off | time-varying baseline link; UL PUSCH SNR tracks the tap |
| 2 | **mimo** | `channel_mimo_real.txt` (2×2 DL matrix) | `...2x2.ethan.conf` | off | MIMO replay active; RIC exports an `nrx=2` per-antenna channel that matches the staged CIR |
| 3 | **jammer** | `channel_jammer_real.txt` (flat) | SISO 106PRB | **on at slot T** | jammer switches on mid-run → UL PUSCH SNR drops → UL throughput drops |

## Prereqs (once)
1. CPU isolation this boot (`cat /sys/fs/cgroup/system.slice/cpuset.cpus.effective`
   should print `0-4,10-14`), else UL MCS pins at 6.
2. The merged gNB/UE must be built once:
   ```bash
   docker run --rm --entrypoint /bin/bash \
     -v /home/ways_lab/repos/tinytwin-oai:/opt/tt-ran/tt:rw tt-gnb:v2 \
     -c "cd /opt/tt-ran/tt/cmake_targets && ./build_oai --gNB --nrUE"
   ```
3. Channel + jammer traces generated:
   ```bash
   python3 /home/ways_lab/repos/Tiny_Twin/channel/ethan_chan/generate_ethan.py
   ```

## Run
```bash
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/Ethan

# all three (snr=30 dB anchor, outputs under ./plot, 80 s traffic each)
./run_three_scenarios.sh 30 ./plot 80

# or one at a time:
python3 ethan_tti_experiment.py 1 1 --channel channel_seesaw_real.txt --snr 30 --out ./plot/seesaw/
python3 ethan_tti_experiment.py 1 1 --channel channel_mimo_real.txt   --snr 30 \
        --gnb-conf ../../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.2x2.ethan.conf \
        --out ./plot/mimo/
python3 ethan_tti_experiment.py 1 1 --channel channel_jammer_real.txt --snr 30 \
        --jam --jgain 200 --jtap 1 --out ./plot/jammer/
```

New flags over `raj`:
| flag | meaning |
|------|---------|
| `--jam` | enable the jammer (`--JAM 1` on the UE) and stage the jammer tap file |
| `--jammer <file>` | jammer real-taps file (default `jammer_real.txt`) |
| `--jgain <g>` | jammer linear gain (`--JGAIN`); bigger = stronger (default 200) |
| `--jtap <n>` | jammer taps per slot (`--JTAP`, default 1) |
| `--gnb-conf <path>` | gNB `.conf` to run (default SISO; pass the 2×2 conf for MIMO) |
| `--ue-ant <n>` | UE antenna count → `--ue-nb-ant-rx/tx n`. **Must equal the gNB RU `nb_tx/nb_rx`** (2 for the 2×2 conf) or the rfsim socket desyncs |
| `--capture-channel` | capture the exported per-antenna UL channel mid-run → `<out>/chan_cap.txt` |

### Full 2×2 MIMO (working)
```bash
MIMO_CONF=../../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.2x2.ethan.conf
python3 ethan_tti_experiment.py 1 1 --channel channel_mimo_real.txt --snr 30 \
        --gnb-conf "$MIMO_CONF" --ue-ant 2 --capture-channel --out ./plot/mimo2x2/
python3 compare_channel.py --cap ./plot/mimo2x2/chan_cap.txt --out ./plot/mimo2x2/channel_compare.png
```
This brings up a real 2×2 link (gNB `nb_tx=2/nb_rx=2`, UE 2 antennas) and the
RIC export reports `nrx=2`. The decisive settings are `pdsch_AntennaPorts_XP=2` +
`pusch_AntennaPorts=2` in the conf and **`--ue-ant 2`** on the UE. Watch the real
gNB log at `cmake_targets/ran_build/build/gnb.log` (NOT `logs_gnb/gnb.log`, which
is stale) for `nb_tx 2, nb_rx 2`.

## Plots
```bash
# 6-panel UL metrics (TPT / MCS / BLER / PUSCH_SNR / CQI / HARQ) + iperf goodput
python3 ../plot.py --snr-file ./plot/jammer/snr.txt --out ./plot/jammer/snr_plot.png
```

## MIMO channel extraction (scenario 2)
The gNB publishes the downsampled per-antenna UL channel over the RT-E2 ZMQ
stream (`ul_channel`, flattened `[rx][sc]` interleaved re,im). Capture it live
and compare to the staged ground-truth CIR:
```bash
# during a mimo run, pull a few channel snapshots out of the edgeric container:
docker exec edgeric_v2_2 python3 /home/EdgeRIC/muApp_capture_channel.py --n 20 --out /tmp/ethan_chan_cap.txt
docker cp edgeric_v2_2:/tmp/ethan_chan_cap.txt ./plot/mimo/chan_cap.txt
# then, on the host, compare extracted |H(f)| / CIR to the staged taps:
python3 compare_channel.py --cap ./plot/mimo/chan_cap.txt --out ./plot/mimo/channel_compare.png
```
(`muApp_capture_channel.py` is the EdgeRIC muApp that subscribes to the ZMQ bus;
it is bind-mounted into the container via the edgeric-v2 mount.)

## How the features map to code (merged from mimoric2)
- **Jammer**: `radio/rfsimulator/apply_channelmod.c` (DL `rxAddInput` + UL
  `txAddInput`), flags in `common/config/config_load_configmodule.c`
  (`--JAM/--JGAIN/--JTAP`), file handles in `executables/nr-uesoftmodem.c`
  (`channel/jammer_real.txt`). Applied UE-side (the UE applies the channel).
- **MIMO replay**: `apply_channelmod.c` loads a full `nb_rx×nb_tx` matrix per
  slot (DL path); one file line per rx antenna, `nb_tx*taplen` taps/line.
- **MIMO-RIC export**: `openair1/PHY/NR_TRANSPORT/nr_ulsch_demodulation.c`
  → `ric_set_channel` → `executables/edgeric/*` → protobuf `ul_channel`
  → `edgeric_messenger.py`.
