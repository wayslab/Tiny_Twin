# Ethan — results

Validation of the mimoric2 features merged into TinyTwin (jammer, MIMO channel
replay, MIMO-RIC UL-channel export). All runs: SISO 106PRB gNB, 1 UE over OAI
rfsim, 30 dB SNR anchor (unit tap → 30 dB), CPU isolation active.

Merged C changes live in the TinyTwin tree (backups of the originals are under
`Tiny_Twin/.merge_backup_mimoric2/`). The gNB/UE were rebuilt with
`build_oai --gNB --nrUE`.

---

## 1. Baseline — seesaw channel (SISO, no jammer)
`plot/seesaw/snr_plot.png`

A single-tap channel whose magnitude slowly swings 1.0 ↔ 0.1. **Result: the UL
PUSCH SNR tracks the tap exactly**, sweeping between ~**12 dB** (troughs) and
~**33 dB** (peaks) over ~4 cycles, with MCS (6↔28), CQI, and TB size following,
and UL BLER spiking at each SNR trough (link-adaptation lag). UL iperf peaked
~14 Mbps, DL ~8.7 Mbps. This confirms the channel-replay engine (the ported MIMO
matrix path, exercised here at nb_rx=1) drives a correct, time-varying link.

| metric | min | max | mean |
|---|---|---|---|
| UL PUSCH SNR (dB) | 12.0 | 38.5 | 25.8 |
| UL MCS | 6 | 28 | 22.8 |
| UL BLER | 0.00 | 0.45 | 0.05 |

---

## 2a. MIMO-RIC channel extraction (SISO, nrx=1) vs ground-truth CIR
`plot/mimo/channel_compare.png`, `plot/mimo/chan_cap.txt`

The gNB exports the per-antenna UL channel estimate over the RT-E2 ZMQ stream
(`ul_channel`, flattened `[rx][sc]` re,im). We captured 40 live snapshots and
compared to the staged ground-truth CIR (a flat unit tap).

**Result: the extracted channel matches the ground truth.** Across the 27
allocated subcarriers the extracted frequency response is **flat at |H| = 2027,
within ±0.5%** (std/mean = 0.0047), and its IFFT is a **single clean tap at
delay 0** — exactly a flat single-tap CIR. Confirms the full
PHY → `ric_set_channel` → protobuf `ul_channel` → ZMQ subscriber path.

## 2b. Full 2×2 MIMO bring-up + nrx=2 channel extraction
`plot/mimo2x2/channel_compare.png`, `plot/mimo2x2/snr_plot.png`, `plot/mimo2x2/chan_cap.txt`

Real 2×2 over rfsim: gNB `nb_tx=2, nb_rx=2` with `pdsch_AntennaPorts_XP=2,
pusch_AntennaPorts=2, do_CSIRS=1` (`...2x2.ethan.conf`), and the UE launched with
**`--ue-nb-ant-rx 2 --ue-nb-ant-tx 2`** (the `--ue-ant 2` driver flag). The gNB
log confirms `XP 2 pusch_AntennaPorts 2`, `nb_tx 2, nb_rx 2`, `NB_RX 2, NB_TX 2`,
the UE attaches, and PUSCH decodes.

**Results:**
- **The RIC export now reports `nrx=2`** (216 floats = 2 rx × 54 sc × re,im) —
  both gNB antenna channels delivered per report.
- The two antennas are each **flat across frequency** but at **different levels**
  (rx0 |H|=360 ±0.5%, rx1 |H|=66 ±2.6%), and both IFFT to a single tap at delay 0
  — distinct, static per-antenna CIRs faithfully recovered.
- **UL PUSCH SNR rose to 39–51 dB (median 40)** vs ~32 dB in the SISO runs — the
  expected **2-antenna receive-combining gain**, i.e. the second rx antenna is
  really being used.

### Why the earlier 2×2 attempt failed (and the fix)
The first try came up 1×1 because (a) the **UE was left at 1 antenna** (default
`--ue-nb-ant-rx 1`), so the two ends of the rfsim socket carried a different
number of streams → `write() failed errno(9)` and an endless RA loop; and (b) I
was reading a **stale `logs_gnb/gnb.log`** — `ethan_run_gnb.sh` writes its log to
the build dir (`cmake_targets/ran_build/build/gnb.log`), not the mounted
`logs_gnb/`. Fix: pass `--ue-ant 2`, set `pdsch_AntennaPorts_XP=2` +
`pusch_AntennaPorts=2` + RU `nb_tx/nb_rx=2`, and read the real log. DL port count
comes only from `pdsch_AntennaPorts_XP/N1/N2`, never from RU `nb_tx`
(`openair2/GNB_APP/gnb_config.c:1316-1352`).

> Note: the ported UL path (`txAddInput`) applies a single-tap channel, so the
> `--TAP 1` single-tap replay is what the extraction recovers; the DL MIMO matrix
> replay (`rxAddInput`) is what carries the 2×2 downlink. Multi-tap (`--TAP>1`)
> replay still destabilises this rfsim path, so runs use `--TAP 1`.

---

## 3. Jammer switches on at slot T
`plot/jammer/snr_plot.png`

A flat signal channel with the jammer tap file zero for the first ~45 000 slots
then strong (`--JAM 1 --JGAIN 200 --JTAP 1`), i.e. the jammer **switches on mid-run**.

**Result: a clean, unambiguous jamming signature.** At the switch-on instant
(≈ TTI 31 500) the UL **PUSCH SNR drops from 32.4 dB → 16.6 dB** (−15.8 dB) and
stays there; simultaneously:

| | before T | after T |
|---|---|---|
| UL PUSCH SNR | 32.4 dB | 16.6 dB |
| UL CQI | ~193 | ~161 |
| UL MCS | 28 | collapses to 9, re-adapts to ~20 |
| UL BLER | ~0.01 | spikes to 0.55, then sawtooth ~0.15 |
| UL TB size | ~5000 B | dips to ~1800, recovers to ~4300 B |
| cumulative UL retransmissions | flat | climbs continuously (1632 total vs 657 baseline) |

The jammer is applied UE-side on both DL (`rxAddInput`) and UL (`txAddInput`);
the UL interference is what the gNB sees on PUSCH, hence the SNR/throughput hit.

---

## Reproduce
```bash
python3 /home/ways_lab/repos/Tiny_Twin/channel/ethan_chan/generate_ethan.py
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/Ethan
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
./run_three_scenarios.sh 30 ./plot 100
```
