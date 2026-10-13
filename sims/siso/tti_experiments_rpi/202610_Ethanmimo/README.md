# 202610_Ethanmimo — uplink MIMO complex-channel validation

Validates the new **uplink MIMO** path (branch `dev/mimo`): the gNB applies a
per-rx-antenna **complex** channel to the UE uplink, and its PUSCH channel
estimate (exported via MIMO-RIC) reproduces that channel.

## What's new (vs stock rfsim)
Stock rfsim applies a full per-antenna complex channel only on the **downlink**
(UE receive, `apply_channelmod.c`); the **uplink** (gNB receive) was a SISO
`memcpy` fan-out — every gNB rx antenna saw identical samples, so no per-antenna
phase. `dev/mimo` routes the gNB uplink receive through the same `rxAddInput`
MIMO apply:

- `radio/rfsimulator/simulator.c` — gate changed to `channel_model && taplen>0`
  (was `gnb1_ue0==0 && ...`), so the gNB rx (`gnb1_ue0==1`) also applies the
  per-antenna channel. It forces `channelDesc->nb_rx = nbAnt`, `nb_tx = th.nbAnt`
  so the matrix dimensions match the real fan-out regardless of channelmod config.
- `executables/nr-softmodem.c` — the gNB's `fpr/fpi` now open a **dedicated** UL
  file `channel/channel_mimo_real.txt` / `channel_mimo_imag.txt` (keeps DL,
  `channel_real.txt`, independent).
- Activates ONLY when the gNB is launched with `--rfsimulator.options chanmod
  --TAP <D+1>`; without `--TAP` the gNB keeps the old SISO path (backward-compatible).

DMRS/SRS per-antenna extraction/export (`ric_set_channel`, `metrics.proto`
`ul_channel`/`ul_channel_ls`/`srs_channel`) was imported from Ethan's branch.

## Channel format (channel_mimo_real/imag.txt)
`nb_rx` lines per TTI (one per gNB rx antenna), each line = `nb_tx*taplen` taps.
`generate_mimo.py` injects, on antenna `a`, a single tap at sample-delay `D` with
per-antenna phase `phi_a = pi*a*sin(theta)` (lambda/2 ULA), so the gNB should
estimate `H_a(k) = mag_a*exp(j*phi_a)*exp(-j*2*pi*k*D/Nfft)`.

## Run
```bash
sudo ../setup_cpu_isolation.sh              # once per boot
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/202610_Ethanmimo

python3 mimo_tti_experiment.py --theta 30 --delay 3 --snr 40 --duration 40
python3 mimo_validate.py --dir ./plot/mimo        # extracted vs input, + PASS/CHECK
```
(If docker needs sudo: `TT_DOCKER='sudo docker' python3 mimo_tti_experiment.py ...`)

## Validation (`mimo_validate.py`)
From the captured per-antenna channel (`chan_cap.txt`, `F`=filtered, `L`=raw-LS):
- inter-antenna phase offset `angle(mean_sc(H_a·conj(H_0)))` vs input `pi*a*sin(theta)`
- delay ramp = slope of `unwrap(angle(H_0))` across subcarriers → `D_est` vs input `D`
- relative per-antenna magnitude `|H_a|/|H_0|`

Outputs `plot/mimo/mimo_validation.png` + `.txt` with a PASS/CHECK verdict.

## Requires
gNB rebuilt from `dev/mimo` (`build_oai --gNB --nrUE`), 4-rx conf
(`...4rx.ethan.conf`, `pusch_AntennaPorts=4`), docker images `tt-gnb:v2`,
`tt-nrue:v2`, `edgeric/v2`.
