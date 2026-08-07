# TinyTwin — SISO TTI Experiment (RPi) README

This document explains the `tti_multiue_experiments_demo1.py` experiment driver: how to
run it, what it does at a high level, and how to reproduce the same thing **by hand** if
the script fails — including the Grafana/Prometheus addresses.

Everything below assumes the repo lives at `/home/ways_lab/repos/Tiny_Twin`.

## Contents

- [0. Quick rebuild & setup](#0-quick-rebuild--setup)
- [1. Using the Python command](#1-using-the-python-command)
- [2. Getting it working from scratch (manual steps)](#2-getting-it-working-from-scratch-manual-steps)
  - [2.5.1 CPU pinning (CRITICAL)](#251-cpu-pinning-critical)
- [Monitoring — Grafana & Prometheus addresses](#monitoring--grafana--prometheus-addresses)
- [Fixed issues](#fixed-issues)
- [Known issues](#known-issues)

---

## 0. Quick rebuild & setup

Run from `sims/siso/tti_experiments/`. **If you modified any C code, you must build.**

```bash
# if you modified any c code, make sure to build
docker compose -f ../../oai-cn/docker-compose.yaml up -d   # bring up the 5G core network
docker compose up -d tt-gnb                                # start the gNB container
docker exec tt-gnb ./build.sh                              # rebuild the softmodems after C edits
```

To change the noise power, edit:

```
Tiny_Twin/ci-scripts/conf_files/nrue.uicc.conf
```

---

## 1. Using the Python command

### Command

```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments
python3 tti_multiue_experiments_demo1.py <num_ue> <num_tap>
```

Run an experiment and plot the result:

```bash
python3 tti_multiue_experiments_demo1.py 1 2                     # 1 UE, 2 taps
python3 plot_snr.py plot/ue1_2/snr.txt plot/ue1_2/snr_plot.png  # plot the SNR output
```

> The Python driver does **not** rebuild `nr-softmodem` or `nr-uesoftmodem`. If you edited
> C/C++ code, rebuild first inside the container as shown in `z_docs/docker-tinytwin.md`,
> then run this experiment command. Otherwise the experiment will use the old binary.

- `<num_ue>`  — number of UEs to attach (use `1` for a single UE).
- `<num_tap>` — number of channel taps applied by the RF-simulator channel model.

> **Must be run from `sims/siso/tti_experiments/`.** All of the script's relative paths
> (compose files, channel files, logs) are resolved from that directory.

> **TAP note:** `--TAP 1`, `--TAP 2`, and higher tap counts all work. The multi-tap
> segfault in the custom channel model (`radio/rfsimulator/apply_channelmod.c`) was
> **fixed** — see *Known issues / fixed* below. Keep the tap count `<= 10` (the channel
> file has 10 values per line).

### Channel files

The RF-sim channel taps are **complex**, split across two files in `channel/` that the
softmodems (`nr-softmodem.c`, `nr-uesoftmodem.c`) open directly and read **one line per
channel-model step**, in lockstep:

| File | Role | Staged each run? |
|------|------|------------------|
| `channel/channel_real.txt` | real part of the taps | **Yes** — the driver does `cp channel_real_demo1.txt channel_real.txt` before every run |
| `channel/channel_imag.txt` | imaginary part of the taps | **No** — read as-is; a static input you maintain by hand |

- Line *k* of `channel_real.txt` pairs with line *k* of `channel_imag.txt` to form the taps
  used at step *k*. Each line holds up to 10 space-separated values (one per tap); `--TAP <n>`
  selects how many of them are applied, so keep `n <= 10`.
- Only the **real** part is refreshed from a demo master each run; the **imaginary** part
  persists between runs. To change the full complex channel, edit `channel_imag.txt` too —
  editing only `channel_real_demo1.txt` varies just the real component.
- Keep the two files **tap/line-consistent**. On EOF the reader returns the previous taps
  (no reset), so a shorter file freezes its half while the other keeps advancing.

### What it does (high level)

The script is a one-shot orchestrator for a full OAI 5G **standalone (SA) RF-simulated**
experiment. In sequence it:

1. **Stages the channel** — copies the demo master onto `channel/channel_real.txt` (the
   real part of the RF-sim taps). The imaginary part, `channel/channel_imag.txt`, is a
   static input and is **not** re-staged — see *Channel files* above.
2. **Brings up the 5G core network** (`sims/oai-cn`) — AMF, SMF, UPF, UDM/UDR, AUSF, NRF,
   MySQL, etc. This also creates the shared Docker network `oai-cn5g-public-net`.
3. **Brings up monitoring** — Prometheus + Grafana containers.
4. **Generates a `docker-compose.yaml`** on the fly with one `tt-gnb`, one `edgeric`, and
   `<num_ue>` UE services, then starts the **gNB** (`nr-softmodem`) and **EdgeRIC**.
5. **Starts the UEs** (`nr-uesoftmodem`), which sync to the gNB over the RF simulator and
   register with the core.
6. **Runs uplink/downlink iperf traffic** between the UEs and the external data network.
7. **Collects metrics** for ~6 minutes (`time.sleep(360)`), then copies the logs into
   `plot/ue<num_ue>_<num_tap>/`.
8. **Tears everything down** — stops the gNB, UEs, EdgeRIC, monitoring, and core network.

### Output

Results are copied to:

```
sims/siso/tti_experiments/plot/ue<num_ue>_<num_tap>/
├── snr.txt            # per-TTI radio metrics: CQI, MCS, ACK, throughput  (the real data)
├── tti.txt            # TTI-timing / channel-model compute profiling       (see note)
└── iperf_cpumem.txt   # host CPU/mem snapshot during the iperf run
```

- **`snr.txt`** is the meaningful output (radio-link quality per TTI). Plot it with:
  ```bash
  python3 plot_snr.py plot/ue1_1/snr.txt plot/ue1_1/snr_plot.png
  ```
- **`tti.txt`** is a *system-performance* profiler (per-TTI timestamps / channel-model
  execution time), used to study how tap count affects real-time behaviour. It is
  **currently disabled** (the instrumentation writes are commented out in
  `apply_channelmod.c` / `gNB_scheduler_dlsch.c`), so it dumps all zeros + a `9999` marker.

---

## 2. Getting it working from scratch (manual steps)

Use this if the Python driver fails, or to understand each stage. The commands mirror what
`autoUE()` does internally. Run everything from `sims/siso/tti_experiments/`.

### 2.0 One-time prerequisites (machine setup)

Because this repo was ported from a machine where it lived at `$HOME/tinytwin-oai`, the
container mounts expect a few paths that must be symlinked into this repo. Create them once:

```bash
# work from the experiment directory — every command in §2 assumes this cwd
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments

# repo self-reference used by the container mount `../../../../tinytwin-oai`
ln -s /home/ways_lab/repos/Tiny_Twin /home/ways_lab/repos/tinytwin-oai 2>/dev/null

# the gNB run/stop/build scripts must exist as files in sims/siso (bind-mounted into tt-gnb)
# they already live in tti_experiments/ — copy if missing:
#   cp tti_experiments/{run,stop,build}.sh sims/siso/
```

Also make sure the channel master file exists. The driver stages the channel by copying
`channel_real_demo1.txt` onto `channel_real.txt` (`autoUE()`), so the master must be present:

```bash
ls -la /home/ways_lab/repos/Tiny_Twin/channel/channel_real_demo1.txt   # must be non-empty
# if the driver's cp source is missing, create it from the real channel data:
#   cp channel/channel_real.txt channel/channel_real_demo1.txt
```

> Note: the generated gNB service also bind-mounts `channel/channel.txt` →
> `/opt/tt-ran/tt/channel/channel_gradual.txt`, and the tap count is applied on the **UE**
> side (`--TAP <n>` in each UE's entrypoint), not the gNB.

> If a bind-mount source path does **not** exist, Docker silently creates it as an empty
> root-owned directory and the softmodem reads garbage/empty config. If you see
> `exec: "./run.sh": is a directory` or empty `gnb.conf`, a mount path is wrong — check the
> symlinks above.

### 2.1 Clean any stale containers

```bash
docker rm -f tt-gnb edgeric_v2_2 $(docker ps -aq --filter name=tt-ue) 2>/dev/null
```

### 2.2 Stage the channel

The taps are complex, split across two files in `channel/` (see *Channel files* in §1).
Staging only refreshes the **real** part from the demo master:

```bash
cp ../../../channel/channel_real_demo1.txt ../../../channel/channel_real.txt
```

- `channel_real.txt` — real taps, re-staged here each run from `channel_real_demo1.txt`.
- `channel_imag.txt` — imaginary taps, **not** staged; used as-is. Make sure it exists and
  stays tap/line-consistent with `channel_real.txt`:
  ```bash
  ls -la ../../../channel/channel_imag.txt ../../../channel/channel_real.txt   # both non-empty
  ```

The softmodems read line *k* of each file in lockstep to form the taps for step *k*; each
line has up to 10 space-separated values and `--TAP <n>` picks how many are applied.

### 2.3 Bring up the 5G core network (creates the shared network)

```bash
docker compose -f ../../oai-cn/docker-compose.yaml up -d
# wait for the core to be healthy (~10 s); this creates network `oai-cn5g-public-net`
```

### 2.4 Bring up monitoring

```bash
docker compose -f edgeric-v2/muApp3/docker/prometheus/docker-compose.yml up -d
docker compose -f edgeric-v2/muApp3/docker/grafana/docker-compose.yml up -d
```

### 2.5 Generate the `docker-compose.yaml`

The driver writes `./docker-compose.yaml` on the fly (`tt-gnb` + one `tt-nrue<k>` per UE +
`edgeric`). The exact file below is what it produces for **1 UE / 2 taps** — paste it as-is:

```bash
cat > docker-compose.yaml <<'EOF'
services:
    tt-gnb:
        image: tt-gnb:v2
        container_name: tt-gnb
        privileged: true
        cpuset: "5,6,7,8"   # gNB: dedicated Cortex-X925 performance cores (~3900 MHz)
        cap_drop:
            - ALL
        cap_add:
            - NET_ADMIN
            - NET_RAW
        volumes:
            - ../../../../tinytwin-oai:/opt/tt-ran/tt:rw
            - ../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.conf:/opt/tt-ran/etc/gnb.conf
            - ../../../channel/channel.txt:/opt/tt-ran/tt/channel/channel_gradual.txt
            - ../../../logs_gnb:/opt/tt-ran/etc/logs
            - ./run.sh:/opt/tt-ran/run.sh
            - ./stop.sh:/opt/tt-ran/stop.sh
            - ./build.sh:/opt/tt-ran/build.sh
        entrypoint: /bin/bash
        stdin_open: true
        tty: true
        networks:
            public_net:
                ipv4_address: 192.168.70.140

    tt-nrue1:
        image: tt-nrue:v2
        container_name: tt-ue1
        privileged: true
        cpuset: "9,15,16,17,18,19"   # UE: remaining X925 cores, disjoint from the gNB
        cap_drop:
            - ALL
        cap_add:
            - NET_ADMIN
            - NET_RAW
        volumes:
            - ../../../../tinytwin-oai:/opt/tt-ran/tt:rw
            - ../../../ci-scripts/conf_files/nrue.uicc.conf:/opt/oai-nr-ue/etc/nr-ue.conf
            - ../../../logs_ue:/opt/oai-nr-ue/etc/logs
        entrypoint: >
            /bin/bash -c "ls && cd tt/cmake_targets/ran_build/build/ &&
            ./nr-uesoftmodem --uicc0.imsi 001010000000011 -C 3619200000 -r 106 --numerology 1 --ssb 516 -E --sa --rfsim --rfsimulator.options chanmod -O ../../../ci-scripts/conf_files/nrue.uicc.conf --TAP 2 --rfsimulator.serveraddr 192.168.70.140 &&
            exec /bin/bash"
        stdin_open: true
        tty: true
        networks:
            public_net:
                ipv4_address: 192.168.70.151
        devices:
            - /dev/net/tun:/dev/net/tun
        healthcheck:
            test: /bin/bash -c "pgrep nr-uesoftmodem"
            interval: 10s
            timeout: 5s
            retries: 5

    edgeric:
        container_name: edgeric_v2_2
        image: edgeric/v2
        build:
            context: edgeric-v2
            dockerfile: ../Dockerfile-edgeric
            args:
                OS_VERSION: "24.04"
        privileged: true
        ports:
            - "7000:7000"
        cap_add:
            - SYS_NICE
            - CAP_SYS_PTRACE
        volumes:
            - /tmp/.X11-unix:/tmp/.X11-unix:rw
            - /dev:/dev
            - ./edgeric-v2:/home/EdgeRIC:rw
        networks:
            public_net:
                ipv4_address: 192.168.70.166

networks:
    public_net:
        driver: bridge
        external: true
        name: oai-cn5g-public-net
EOF
```

**To scale it:** add another `tt-nrue<k>` block per UE (`k = 2, 3, …`), bumping
`--TAP <num_tap>`, the IMSI (`0010100000000<10+k>`), and the IP (`192.168.70.<150+k>`) — e.g.
UE #2 → container `tt-ue2`, IMSI `001010000000012`, IP `192.168.70.152`. Keep the UE `cpuset`
off the gNB cores (§2.5.1).

### 2.5.1 CPU pinning (CRITICAL)

**This is not optional.** The RF simulator runs the whole PHY in soft-real-time. If the gNB
and UE softmodems float across cores (or share cores), they miss TTI deadlines, the UE fails
to synchronize, and the run produces empty/garbage `snr.txt`. Each service must be pinned to
its **own disjoint set** of the host's high-performance cores via the compose `cpuset`.

> **Hardware:** this runs on an **NVIDIA DGX Spark** (GB10 Grace-Blackwell) — a 20-core Arm
> CPU: **10× Cortex-X925 performance cores** + **10× Cortex-A725 efficiency cores**. The
> `cpuset` values below assume that core map. On any other machine (or a different GB10
> core enumeration) you **must** re-derive the numbers — see *Finding your cores* below.

The driver sets these in the generated `docker-compose.yaml`:

| Service | `cpuset` | Cores |
|---------|----------|-------|
| `tt-gnb` (`start` block) | `"5,6,7,8"` | 4 dedicated X925 performance cores (~3900 MHz) |
| each `tt-nrue<k>` (`create_docker`) | `"9,15,16,17,18,19"` | other X925 cores, **disjoint** from the gNB |

If you hand-write the compose, put the same `cpuset` on each service, e.g.:

```yaml
services:
    tt-gnb:
        image: tt-gnb:v2
        container_name: tt-gnb
        privileged: true
        cpuset: "5,6,7,8"          # gNB: dedicated performance cores
        # ...

    tt-nrue1:
        image: tt-nrue:v2
        container_name: tt-ue1
        privileged: true
        cpuset: "9,15,16,17,18,19" # UE: disjoint from the gNB set
        # ...
```

Rules of thumb:

- **Never overlap** the gNB and UE core sets — a shared core forces them to contend and
  breaks timing.
- Give the **gNB dedicated cores** (`5,6,7,8` here); it is the most timing-sensitive.
- For multiple UEs they can share the UE set (`9,15,…`) but keep that set off the gNB cores.
- Pin to **performance** cores (Cortex-X925), not efficiency cores (Cortex-A725). The
  `cpuset` numbers above assume the DGX Spark core map — re-derive them for your machine.

#### Finding your cores (do this on a new machine)

The `cpuset` numbers are logical-CPU IDs from the host's core enumeration — they are **not
portable**. Derive them for your box before pinning:

1. **List every logical CPU with its max frequency.** Performance cores clock highest;
   efficiency cores clock lower. This is the fastest way to tell them apart:
   ```bash
   lscpu -e=CPU,CORE,MAXMHZ,MHZ
   # or, if MAXMHZ is blank, read it from sysfs (kHz):
   for c in /sys/devices/system/cpu/cpu[0-9]*; do
     printf "%s %s kHz\n" "${c##*/}" "$(cat $c/cpufreq/cpuinfo_max_freq 2>/dev/null)"
   done | sort -t u -k2 -n
   ```
   On the DGX Spark you'll see two frequency tiers: the **higher-MHz CPUs are the X925
   performance cores** (use these), the lower-MHz ones are the A725 efficiency cores.

2. **Confirm the CPU model / topology** (which cluster each core belongs to):
   ```bash
   lscpu                                            # model names + core counts
   lscpu -e=CPU,SOCKET,CLUSTER,CORE,MAXMHZ          # cluster grouping
   grep -m1 'CPU part' /proc/cpuinfo                # Arm part IDs (0xd8_ = X925, etc.)
   ```

3. **Check what's already busy** so you don't pin onto loaded cores:
   ```bash
   htop        # press F2 → Display → show CPU numbers; watch per-core load
   # or a quick snapshot:
   mpstat -P ALL 1 1
   ```

4. **Pick two disjoint sets from the performance cores** — e.g. 4 for the gNB and a separate
   block for the UE(s) — and confirm the count is enough for your UE count. Then set them as
   `cpuset` on each service (same format as above, comma/range list: `"5,6,7,8"` or `"5-8"`).

5. **(Optional) Reserve the cores from the OS scheduler** for the most deterministic timing.
   Either boot with `isolcpus=<gnb+ue cores>` on the kernel cmdline, or confirm no other
   heavy process is scheduled there. Not required, but it removes jitter from background tasks.

- **Verify** a running container is actually pinned to what you intended:
  ```bash
  docker inspect -f '{{.HostConfig.CpusetCpus}}' tt-gnb   # -> 5,6,7,8
  docker inspect -f '{{.HostConfig.CpusetCpus}}' tt-ue1   # -> 9,15,16,17,18,19
  # live check — which cores the softmodem threads actually land on:
  docker exec tt-gnb bash -c 'ps -eLo psr,comm | grep nr-softmodem | sort -u'
  ```

### 2.6 Start the gNB and EdgeRIC

Order matters — the gNB comes up first, then EdgeRIC, then the metrics exporter:

```bash
docker compose up -d tt-gnb
sleep 10

docker compose up -d edgeric
sleep 10

docker exec -d edgeric_v2_2 python3 ./muApp3/muApp3_monitor_grafana.py   # exposes metrics on :8000

docker exec tt-gnb chmod +x run.sh build.sh
docker exec -d tt-gnb ./run.sh              # launches nr-softmodem in the background
```

> The driver does **not** build here — it only `chmod`s and runs `run.sh`. If you edited
> C/C++ code, run `docker exec tt-gnb ./build.sh` first (see §0), otherwise the old binary
> is used.

### 2.7 Start the UE(s)

Bring up each generated UE service in turn (the driver spaces them out by ~12–15 s):

```bash
docker compose up -d tt-nrue1               # container name: tt-ue1
sleep 12
docker logs -f tt-ue1                        # watch for "UE synchronized!"
```

For N UEs, repeat for `tt-nrue2 … tt-nrueN` (containers `tt-ue2 … tt-ueN`).

### 2.8 Run traffic

The driver starts iperf servers on **both** ends, then the clients. For UE #1
(`10.0.0.2`, port `5212`):

```bash
# servers
docker exec -d tt-ue1     iperf -s -u -i 1 -B 10.0.0.2
docker exec -d oai-ext-dn iperf -s    -i 1 -B 192.168.70.135 -p 5212

# clients (downlink from the data network, uplink from the UE)
docker exec -d oai-ext-dn iperf -u -t 86400 -i 1 -fk -B 192.168.70.135 -b 3M   -c 10.0.0.2
docker exec -d tt-ue1     iperf    -t 86400 -i 1 -fk -c 192.168.70.135 -b 3.5M -B 10.0.0.2 -p 5212

# host CPU/mem snapshot during the run
top -bn6 > ./plot/ue1_2/iperf_cpumem.txt
```

Then let it run (~1 min in the current driver — `time.sleep(60)`, was 360).

### 2.9 Collect metrics

`processDATA()` harvests the metrics that the softmodem wrote into `Tiny_Twin/logs/`
(mounted into the containers via the `tinytwin-oai` repo mount) and copies them into the
per-experiment output folder:

```bash
mkdir -p ./plot/ue1_2
cp ../../../logs/tti.txt ./plot/ue1_2/tti.txt
cp ../../../logs/snr.txt ./plot/ue1_2/snr.txt
```

(`../../../logs/` = `Tiny_Twin/logs/`; `snr.txt` is the meaningful radio data, `tti.txt`
is the disabled timing profiler.)

### 2.10 Tear down

```bash
docker exec tt-gnb chmod +x stop.sh
docker exec -d tt-gnb ./stop.sh
sleep 5

docker compose down
docker compose -f edgeric-v2/muApp3/docker/grafana/docker-compose.yml   down
docker compose -f edgeric-v2/muApp3/docker/prometheus/docker-compose.yml down
docker compose -f ../../oai-cn/docker-compose.yaml down

# belt-and-suspenders: remove any stragglers
docker rm -f tt-gnb edgeric_v2_2 $(docker ps -aq --filter name=tt-ue) 2>/dev/null
```

---

## Monitoring — Grafana & Prometheus addresses

All containers sit on the Docker network `oai-cn5g-public-net` (`192.168.70.0/24`).

| Service    | In-network IP     | Port | Open from the host browser        |
|------------|-------------------|------|-----------------------------------|
| Grafana    | `192.168.70.168`  | 3000 | <http://localhost:3000>           |
| Prometheus | `192.168.70.167`  | 9090 | <http://localhost:9090>           |
| EdgeRIC metrics (muApp3) | `192.168.70.166` | 8000 | scraped by Prometheus (not a UI)  |
| tt-gnb     | `192.168.70.140`  | —    | RF-sim server for the UEs         |
| tt-ue1     | `192.168.70.151`  | —    | first UE (`.15X` = 150 + ue index)|

Both Grafana (`3000`) and Prometheus (`9090`) publish their ports to the host, so from a
browser on the RPi use `localhost`. From another machine on the LAN, use the RPi's IP,
e.g. `http://<rpi-ip>:3000`.

**Data flow:** EdgeRIC's `muApp3_monitor_grafana.py` exposes live RAN metrics on
`192.168.70.166:8000` → Prometheus scrapes `OAI5G_metrics` at that target every 1 s →
Grafana queries Prometheus and renders the dashboards.

- **Grafana** default login: `admin` / `admin` (first run prompts a password change).
- In Grafana, add Prometheus as a data source at `http://192.168.70.167:9090`
  (or `http://prometheus:9090`) if it isn't provisioned already.
- Prometheus scrape target is defined in
  `edgeric-v2/muApp3/docker/prometheus/prometheus/prometheus.yml`
  (`job_name: OAI5G_metrics`, `targets: ['192.168.70.166:8000']`).

---

## Fixed issues

- **`--TAP >= 2` UE crash — FIXED.** `txAddInput()` in
  `radio/rfsimulator/apply_channelmod.c` indexed the input with
  `((i - l - dd) + CirSize) % CirSize`. For the first samples of a block, `i - l` goes
  negative (taps `l > i` need history from *before* the block); the `% CirSize` wrapped
  that to ~460799, but `input_sig` is only `nbSamples` (~23040) long → out-of-bounds read →
  SIGSEGV. With `--TAP 1` the index is never negative, so only `--TAP >= 2` crashed.
  Fix: compute the index signed and skip taps that reach before the block start:
  ```c
  const long j = (long)i - (long)l - (long)dd;
  if (j < 0) continue;              // no history in this block; skip this tap
  const struct complex16 tx16 = input_sig[j];
  ```
  After editing, rebuild the runtime channel-model module (fast, incremental):
  ```bash
  docker run --rm --entrypoint bash -v /home/ways_lab/repos/Tiny_Twin:/opt/tt-ran/tt \
    tt-nrue:v2 -c "cd /opt/tt-ran/tt/cmake_targets/ran_build/build && make rfsimulator"
  ```
  This relinks `librfsimulator.so`, which `--rfsim` loads at runtime (no need to relink the
  softmodem binaries). Verified: `--TAP 1` and `--TAP 2` both run the full experiment and
  collect valid `snr.txt` data.

## Known issues

- **`tti.txt` is empty (all zeros)** — the timing instrumentation is commented out in
  `apply_channelmod.c` (lines ~217, ~252, ~407) and `gNB_scheduler_dlsch.c` (~830). Real
  radio metrics are in `snr.txt`.
- **`--TAP`/`--TTI` on the gNB** — the active `run.sh` launches `nr-softmodem` **without**
  `--TAP`, so the gNB does not apply the channel model. The channel is only applied on the
  UE side. (This is the code path that previously crashed for TAP≥2 — now fixed.)
- **Hardcoded machine paths** — older revisions of the driver had `/home/wcsng5g/...`
  absolute paths; these have been converted to relative paths in this copy. If you see
  `no such file or directory` for a `/home/...` path, check for stale absolute paths.
