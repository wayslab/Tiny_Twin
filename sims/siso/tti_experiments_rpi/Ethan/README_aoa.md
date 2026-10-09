# 4-antenna λ/2 AoA experiment (gNB 4-rx ULA, 1-Tx UE)

Feed a **half-wavelength (λ/2) uniform-linear-array channel** at a known
angle-of-arrival (AoA) into TinyTwin; a **muApp running in the EdgeRIC container**
subscribes to the live channel stream, extracts the **per-antenna uplink channel
(4 CIR, one per gNB rx antenna)** from the MIMO-RIC export, and **computes the AoA
in-container**. Offline we then compare magnitude + phase to ground truth and plot
the AoA angle error (the AoA numbers come from the muApp, not a host recompute).

**AoA is produced by the muApp** (`muApp_aoa.py`, in the edgeric mount): it does
the CIR extraction + beam-sweep live. The host side only plots.

Scenario: **gNB nb_rx = 4** (λ/2 ULA), **UE = 1 Tx**, flat unit-tap SISO link.

```
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/Ethan

python3 /home/ways_lab/repos/Tiny_Twin/channel/ethan_chan/generate_aoa.py   # once: flat channel
python3 aoa_tti_experiment.py --angles -30 0 30 --snr 30 --duration 30      # run OAI, 1 per angle
python3 aoa_analyze.py --root ./plot/aoa --angles -30 0 30                  # compare + AoA + error
```

Outputs: `plot/aoa/aoa<deg>/compare.png` (|H|, phase, per-antenna offset,
beam-sweep per angle) and `plot/aoa/aoa_angle_error.png` (estimated-vs-truth +
angle error).

## Adding a propagation delay (ToA) — `--delay`

`--delay D` stages the UL signal channel as a single unit tap at **sample-delay D**
(`--TAP D+1`) instead of a flat delay-0 tap. A pure delay gives
`H(f)=exp(-j2π·k·D/Nfft)`, i.e. a **common linear phase ramp across subcarriers**
on all 4 antennas (the ToA), with the per-antenna **AoA** offset `π·a·sinθ₀` on top.

```
python3 aoa_tti_experiment.py --angles 30 --snr 30 --delay 8 --out ./plot/aoa_delay
python3 aoa_analyze.py --root ./plot/aoa_delay --angles 30 --keep-ramp   # see the ramp
```

Verified at `--delay 8`: rx0 phase slope = −0.0324 rad/sc (≈ `−2π·8/1536`), all four
antennas parallel, offset by the AoA phases {0, +90°, +180°, −90°}; AoA still
recovered at +30°. The delay is NOT cancelled by timing advance (it is below the TA
step), so the ramp persists. The muApp isolates AoA from the ramp via
`v[a]=mean_sc(H_a·conj(H_0))` (the common ramp cancels), so AoA is unaffected by D.
(ToA estimation from the slope is a natural add-on, not yet in the muApp.)

## SRS reference-signal channel export (new) — `plot/mimo2x2_2tap_phase/`

The muApp can now also subscribe to an **SRS**-based per-antenna channel, not just
DMRS. SRS is **wideband + periodic** (it sounds the whole band regardless of the
PUSCH grant), so it gives the per-antenna CFR across all PRGs.

This did not exist before (not in TinyTwin, not in mimoric2) — SRS was computed
internally (`nr_srs_channel_iq_matrix`, codebook usage) but never published. Added:
- **C hook** `ric_set_srs_channel()` in `openair2/LAYER2/NR_MAC_gNB/gNB_scheduler_ulsch.c`
  (the `usage_codebook` case), flattening the SRS IQ matrix `[gnb_rx][prg]` (UE port 0).
- wrapper (`executables/edgeric/wrapper.{h,cpp}`), store + publish (`edgeric.{h,cpp}`),
  and new proto fields `srs_channel` / `srs_channel_nrx` / `srs_channel_nsc`
  (`metrics.proto`, field 15–17). Protobuf regenerated: C++ via tt-gnb protoc 3.21.12,
  Python via edgeric protoc 3.12.4. gNB rebuilt.
- **`muApp_srs_aoa.py`** (canonical in Ethan/, synced to the edgeric mount by the
  driver) subscribes to `srs_channel` (and `ul_channel` for comparison).
- **`srs_tti_experiment.py`** brings up the 2×2 2-tap-phase channel
  (`channel_mimo2tap_phase`, 2×2 conf, `--ue-nb-ant 2`, `--TAP 16`) and runs the muApp.
- **`srs_plot.py`** plots the SRS per-antenna |H|/phase and SRS-vs-DMRS Δφ.

```
python3 srs_tti_experiment.py --snr 30 --duration 40 --out ./plot/mimo2x2_2tap_phase
python3 srs_plot.py --dir ./plot/mimo2x2_2tap_phase
```

Verified: gNB logs `[srs-ric] exported SRS channel nrx(gnb)=2 nsc(prg)=104`; the muApp
captured 200 SRS snapshots (`S 2 104`); the SRS CFR shows the 2-tap ripple across all
104 PRGs (wideband), and the SRS per-antenna Δφ = [0, 1.00] rad **matches DMRS exactly**.

## Why a C change was needed (important)

Stock rfsim (and Ethan's prior MIMO work) **cannot** put a per-antenna phase on
the *uplink* from the channel files:

- The DL path (`rxAddInput`, `apply_channelmod.c`) reads a full per-rx-antenna
  multi-tap matrix — multi-tap **and** multi-antenna — but that is the
  **downlink** (applied UE-side on what the UE receives).
- The UL path (`txAddInput`) reads a **single SISO tap line** (multi-tap, but no
  rx-antenna index): the same taps hit every gNB antenna.
- With a 1-Tx UE the gNB rx fan-out is a **bit-identical `memcpy`** of antenna 0
  to all rx antennas → zero phase difference → AoA undefined. (The per-antenna
  phase seen in the old 2×2 run came from a **hardcoded** `Hmag/Hphi` matrix in
  `simulator.c`, only reached when the UE has ≥2 Tx antennas.)

The "4 CIR per gNB antenna" we extract is the **uplink** PUSCH channel estimate
(`ric_set_channel` over `nb_antennas_rx`), fed by the UL path — so the λ/2 AoA
must be injected on the gNB rx. We therefore added a small, **tunable** ULA
steering to the gNB-rx fan-out:

- `radio/rfsimulator/simulator.c` — in the 1-Tx `memcpy` branch of
  `rfsimulator_read`, rx antenna `a` is rotated by `phi_a = π·a·sin(θ0)`
  (d = λ/2). `θ0` (ground-truth AoA, rad) is read once per gNB process from
  `channel/aoa_steer.txt`. `a==0` ⇒ identity, so rx0 is untouched.

Rebuild after the C change:
```
docker run --rm --entrypoint /bin/bash -v /home/ways_lab/repos/tinytwin-oai:/opt/tt-ran/tt:rw \
  tt-gnb:v2 -c "cd /opt/tt-ran/tt/cmake_targets && ./build_oai --gNB --nrUE"
```

## Key config knob

`fp->nb_antennas_rx` (the gNB L1 uplink rx-antenna count that drives PUSCH
chan-est **and** the `nrx` of the export) comes from **`pusch_AntennaPorts`**, not
the RU `nb_rx` (`openair2/LAYER2/NR_MAC_gNB/config.c:557`). Both must be 4:

- `gnb.sa.band78.fr1.106PRB.usrpb210.4rx.ethan.conf`: `pusch_AntennaPorts = 4`,
  RU `nb_tx = 1`, `nb_rx = 4` (DL stays SISO so the UE keeps 1 antenna → no
  rfsim socket desync; the 4-rx fan-out is local to the gNB read).

With `pusch_AntennaPorts` left at 1 the export reports `nrx=1` (only antenna 0
processed) and the AoA ramp is invisible — this was the first failure mode.

## Files

| file | role |
|------|------|
| `radio/rfsimulator/simulator.c` | tunable λ/2 ULA steering on the gNB rx (reads `channel/aoa_steer.txt`) |
| `.../CONF/gnb.sa.band78.fr1.106PRB.usrpb210.4rx.ethan.conf` | 4-rx gNB conf (`pusch_AntennaPorts=4`, `nb_rx=4`, `nb_tx=1`) |
| `channel/ethan_chan/generate_aoa.py` | flat unit-tap signal channel |
| `channel/aoa_steer.txt` | ground-truth AoA (rad); rewritten per run by the driver |
| `aoa_tti_experiment.py` | driver: per angle → write steer, run OAI, run the muApp live, collect its output |
| `muApp_aoa.py` (**canonical, in Ethan/**) | **the muApp**: subscribes to the live stream, extracts the 4-antenna CIR, computes AoA by beam-sweep IN-CONTAINER; writes `aoa_muapp.txt` (AoA log) + `chan_cap.txt` (raw channel). Uses the FILTERED estimate (`ul_channel`). The driver syncs this file into the edgeric mount (`../tti_experiments/edgeric-v2/muApp_aoa.py` → container `/home/EdgeRIC/muApp_aoa.py`) at runtime, so edit the Ethan copy only. |
| `aoa_analyze.py` | plots magnitude/phase vs ground truth from the muApp's channel; AoA summary/error uses the muApp's reported AoA |

### muApp AoA math (in `muApp_aoa.py`)
Per snapshot, from the exported `H[rx][sc]` (nrx=4):
1. `v[a] = mean_sc( H_a(sc) · conj(H_0(sc)) )` — the `conj(H_0)` cancels the common
   per-subcarrier timing ramp, leaving the inter-antenna phase `exp(j·φ_a)`.
2. beam-sweep `P(θ) = |Σ_a exp(−j·2π·(0.5a)·sinθ)·v[a]|²`, `θ_est = argmax P`.
Run standalone:
```
docker exec edgeric_v2_2 python3 /home/EdgeRIC/muApp_aoa.py \
    --n 300 --out /tmp/aoa_muapp.txt --chan-out /tmp/aoa_chan_cap.txt --spacing 0.5
```

## Results (−30°, 0°, +30°, 30 dB)

All three angles: `nrx=4` recovered; per-antenna |H| equal (±0.1%, flat in
frequency); per-antenna phase flat at the λ/2 ramp `{0, π·sinθ₀, 2π·sinθ₀, …}`;
**the muApp computed AoA live (300 snapshots/angle) = −30.0° / 0.0° / +30.0°,
angle error ≈ 0°** (per-snapshot std ≤ 0.02°).

The channel is injected as an **ideal, noise-free deterministic λ/2 phase ramp**
and estimated with the matched steering model, so this validates the full path
(injection → PHY PUSCH chan-est → MIMO-RIC export → extraction → beam-sweep AoA)
rather than benchmarking accuracy under impairments. For a realistic error curve,
add per-antenna gain/phase mismatch or lower the SNR anchor.
