# tti_experiments_rpi

SISO TTI experiments, reorganized so each experiment is an isolated subfolder
that shares a small amount of common tooling. This README explains how the
pieces connect — the folder itself, the shared scripts, the sub-experiments,
and the external infra they reach into.

## Layout
```
tti_experiments_rpi/
├── README.md          ← you are here (how it all connects)
├── plot.py            ← shared plotter for every sub-experiment
├── setup_cpu_isolation.sh ← shared: shield OAI cores from OS jitter (run per boot)
├── run.sh             ← shared: start nr-softmodem (gNB) in the container
├── build.sh           ← shared: build_oai --gNB --nrUE in the container
├── stop.sh            ← shared: kill the gNB by saved pid
└── raj/               ← one experiment (raj_chan CIR + Python-set noise)
    ├── raj_tti_experiment.py
    ├── README.md      ← raj specifics: channel, noise math, run/plot
    └── plot/ue<#UE>_<#tap>/   ← raj outputs (created per run)
```

## The connections

**Shared, experiment-agnostic (this level).** `run.sh` / `build.sh` /
`stop.sh` only touch fixed container paths (`/opt/tt-ran/tt/...`) and carry no
experiment arguments, so every experiment mounts the *same* three. `plot.py`
is likewise generic: it takes an experiment subfolder (`--exp`, default `raj`)
plus `ue`/`tap` and renders that run's `snr.txt`.

**Per-experiment (subfolders).** Each subfolder owns one driver and its own
`plot/` outputs. A driver runs with `cwd = its own folder`, so all generated
artifacts (`docker-compose.yaml`, `plot/`, copied logs) stay inside that
subfolder and never collide with another experiment.

**External infra (reached by absolute path).** Drivers here do **not** carry
their own OAI/edgeric stack. They reference, by absolute path:

| dependency | location | role |
|------------|----------|------|
| `tinytwin-oai` | `repos/tinytwin-oai` → symlink to `Tiny_Twin` | OAI source/build mounted as `/opt/tt-ran/tt` |
| gNB conf | `Tiny_Twin/targets/.../gnb.sa.band78...conf` | gNB radio config |
| UE conf | `Tiny_Twin/ci-scripts/conf_files/nrue.uicc.conf` | UE + `channelmod` (noise) config |
| channel | `Tiny_Twin/channel/channel_real.txt`, `channel_imag.txt` | the per-TTI taps the rfsim C reads |
| logs | `Tiny_Twin/logs`, `logs_gnb`, `logs_ue` | gNB/UE write `snr.txt`, `tti.txt`, … |
| edgeric-v2 + oai-cn | `tti_experiments/edgeric-v2`, `sims/oai-cn` | RIC + 5G core brought up via their own compose files |

Because `tinytwin-oai` is a symlink to `Tiny_Twin`, the container path
`/opt/tt-ran/tt/channel/` **is** `Tiny_Twin/channel/` on the host — that is the
bridge that lets a driver stage a channel file the C code will read.

## Data flow (one run)
```
<exp>/<driver>.py
  └─ stage channel  →  Tiny_Twin/channel/channel_real.txt (+ zero imag)
  └─ write          →  <exp>/docker-compose.yaml   (absolute mounts + Python-set noise)
  └─ docker compose →  oai-cn (5G core) + edgeric (RIC) + tt-gnb + tt-ue(s)
        gNB/UE rfsim reads channel_real/imag.txt each TTI, adds AWGN
        gNB writes metrics  →  Tiny_Twin/logs/{snr,tti}.txt
  └─ copy logs      →  <exp>/plot/ue<#UE>_<#tap>/{snr,tti}.txt

plot.py <ue> <tap> --exp <exp>
  └─ read <exp>/plot/ue<#UE>_<#tap>/snr.txt
  └─ write snr_plot.png   (6-panel UL metrics vs TTI)
```

## Prerequisite — CPU isolation (once per boot)
OAI needs its cores free of OS jitter or UL MCS gets pinned at 6. Before any run:
```bash
sudo ./setup_cpu_isolation.sh    # from tti_experiments_rpi/
```
Shields cores 5–9/15–19 (moves IRQs + host tasks to 0–4,10–14). Not persistent —
re-run after a reboot. See `raj/README.md` → "CPU isolation" for the why.

## Run (raj example)
```bash
source /home/ways_lab/repos/venv/sub6isac_env/bin/activate

cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments_rpi/raj
python3 raj_tti_experiment.py --list          # available channels
python3 raj_tti_experiment.py 1 1             # <#UE> <#taps>

cd ..                                          # back to tti_experiments_rpi/
python3 plot.py 1 1                            # -> raj/plot/ue1_1/snr_plot.png
```
See `raj/README.md` for the channel-tap generation and the noise-power math.

## Adding a new experiment
1. `mkdir <name>/` next to `raj/` and drop a driver in it.
2. Have the driver compute paths from `__file__`, `chdir` into its own folder,
   and reference external infra by absolute path (copy raj's path-anchor block).
3. Mount the shared `../run.sh` / `../stop.sh` / `../build.sh` into tt-gnb.
4. Write outputs to `./plot/ue<#UE>_<#tap>/`; plot with
   `python3 plot.py <ue> <tap> --exp <name>`.

## Relationship to the original `tti_experiments/`
This folder was split out to keep experiments uncluttered. The original
`sims/siso/tti_experiments/` is unchanged and still hosts the shared
`edgeric-v2/` build context, `Dockerfile-edgeric`, and the legacy demo driver;
rpi experiments reuse that edgeric stack by absolute path rather than copying
it.
