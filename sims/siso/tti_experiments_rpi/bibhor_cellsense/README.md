# bibhor_cellsense — ISAC PUSCH-DMRS sensing (channel → position)

Ports the ISAC/CellSense sensing pipeline (bibhor, `ISAC/Python_scripts/
Simulation/run_open_scenario.py`) into TinyTwin: extract the **PUSCH DMRS uplink
channel** as `H[rx][sc]` (same muApp as `Ethan/`), then run an **offline app**
that turns the per-TTI channel into an **(x,y) position estimate** of a moving
object and plots **residual error vs time**.

| file | role |
|---|---|
| `muApp_capture_channel.py` | EdgeRIC muApp — extracts the live gNB PUSCH DMRS UL channel `H[rx][sc]` over the ZMQ bus (identical to `Ethan/`). The **interface** that feeds sensing. |
| `cellsense_sensing.py` | the ISAC algorithm, ported verbatim: ray-tracing CFR synth, mDTrack per-path estimator (beam-sweep AoA + CAF ToA/Doppler), `est_pos` geometry solver. |
| `cellsense_position.py` | **offline app** — one trial → per-frame (per-TTI) position estimate + residual error → `residual.txt` / `trial.npz`. |
| `plot_residual.py` | residual position error **vs time**, one trial. |
| `run_cellsense.sh` | one-shot: trial + plot. |
| `Datasets/` | ISAC ray-tracing datasets (Open/Indoor/Outdoor × Trajectory_1-3), copied from the ISAC repo. |

## Quick start
```bash
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/bibhor_cellsense

./run_cellsense.sh Open_scenario Trajectory_1 10 ./plot/trial1
# -> ./plot/trial1/residual_vs_time.png  (+ residual.txt, trial.npz)
```
One trial (100 frames) runs in ~30 s.

### Result (Open_scenario / Trajectory_1, seed 10)
- static-learning phase: frames 0–30 (object not yet visible to the BS)
- dynamic phase: object visible for 24 frames, **all 24 localized**
- residual error: min 0.09 m, median 0.47 m, **RMSE 0.59 m**, max 1.27 m — all
  under the 5 m detection threshold.

## The algorithm (channel → position)

Per frame `k` (one sensing TTI, `Te = 0.08 s` apart):

1. **CFR** `H[k, N_ant=4, N_ifft=1024]` — on the live RAN this is the PUSCH DMRS
   estimate the muApp exports; in the offline trial it is synthesized from the
   ray-tracing path params and LS-estimated on the SRS comb, then comb-interpolated.
2. **mDTrack per-path estimation** (`estimate_frame`):
   - **AoA** by classical **beam-sweep** — max of `|a(θ)ᴴ · H|²` over a steering
     grid `a(θ)=exp(-j2π·d·sinθ)` on the 4-element half-λ ULA (not MUSIC/ESPRIT).
   - **ToA + Doppler** by the **Cross-Ambiguity Function** (matched filter) against
     the known SRS time waveform, oversampled ×8.
   - successive-interference cancellation over 5 refinement iterations.
3. **LoS-from-UE referencing** (`find_los`) → subtract the UE LoS delay to get the
   reflected path's **bistatic delay**; **static-path tracking** across frames — a
   path that matches no learned static path is a **dynamic (moving) object**.
4. **Geometry solve** (`est_pos`): bistatic range `l̂ = cτ̂` + reflected AoA `θ̂`,
   with gNB(Tx) at origin and UE(Rx) at `D=11 m`, single-bounce gNB→object→gNB →
   `(x,y)`.
5. **Residual** `= ||(x,y)_est − (x,y)_groundtruth||`.

Constants (20 MHz FR1): `f_c=3.6192 GHz`, `scs=30 kHz`, `N_ifft=1024`, `nPRB=51`,
`N_ant=4` half-λ ULA, `K_TC=2` SRS comb, `osf=8`, `D=11 m`. (verbatim from ISAC.)

## Why the sensing trial uses the ISAC channel, not the live twin

The muApp extracts a real `H[rx][sc]` off the running gNB — but the live TinyTwin
UL channel **cannot localize**, for the same reasons documented in `Ethan/`:

- only **2 gNB rx antennas** (the ULA AoA estimator needs ≥4 for usable angular
  resolution);
- OAI's UL estimator **low-pass-filters** the CFR to a single delay, destroying the
  multipath ToA the bistatic solve depends on;
- the rfsim replay has **no moving reflector** — the tap file is scripted, so there
  is no dynamic path to detect.

So the muApp is the honest **interface demonstration** (you really can pull
`H[rx][sc]` per TTI off the live RAN), and the **sensing trial runs the real ISAC
ray-tracing channel** (4-antenna ULA + a moving object) through the same offline
consumer. `cellsense_position.py --cap <file>` will accept a live capture and says
so explicitly. Making the *live* twin sense would require: a ≥4-antenna UL config,
exporting the **raw LS** estimate (not `ul_ch_estimates_ext`), and injecting a
moving multipath component into the rfsim UL path.

## Capturing the live PUSCH DMRS channel (interface demo)
During any running experiment (e.g. an `Ethan/` MIMO run with `--capture-channel`):
```bash
docker exec edgeric_v2_2 python3 /home/EdgeRIC/muApp_capture_channel.py \
        --n 40 --out /tmp/cap.txt
docker cp edgeric_v2_2:/tmp/cap.txt ./plot/cap.txt
```
`cap.txt` lines are `<rnti> <nrx> <nsc> <re im re im ...>` = `H[rx][sc]`.
