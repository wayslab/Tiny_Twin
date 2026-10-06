# TinyTwin Fresh Docker Setup

This guide assumes the repository is at `/home/ways_lab/repos/Tiny_Twin` and uses the SISO simulation. It reflects the working ARM64 setup, including the corrected Compose mounts, shared core network, multi-architecture external-DN image, and subscriber IMSI.

# How to run one-line python command
cd Tiny_Twin/sims/siso/
source andrew_env/bin/activate # STart virutal environmnet
python3 /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments/tti_multiue_experiments_demo1.py 1 2

# TinyTwin Docker Running (quick start — no rebuild)

Use this once the images are already built (Section 2) and OAI has been compiled at least once (Section 4). Both persist between runs — the images stay in Docker, and the compiled binaries live in the bind-mounted repo (`cmake_targets/ran_build/build`) — so you skip **both** `docker build` and `./build_oai`. You do **not** need to run "Stop the system" first; only tear down for a fresh rebuild or to free resources.

```bash
# 1. Start the 5G core (leave it up across many gNB/UE runs)
cd /home/ways_lab/repos/Tiny_Twin/sims/oai-cn
docker compose up -d

# 2. (Re)create the gNB container — no recompile, binaries already in the mounted repo
cd /home/ways_lab/repos/Tiny_Twin/sims/siso
docker rm -f tt-gnb 2>/dev/null || true
docker compose run -d --name tt-gnb --entrypoint /bin/bash tt-gnb

# 3. Run the gNB
docker exec -d tt-gnb bash -lc '
  cd /opt/tt-ran/tt/cmake_targets/ran_build/build &&
  exec ./nr-softmodem \
    -O ../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.conf \
    --rfsim -E --sa --rfsimulator.options chanmod \
    --TAP 1 --TTI 1 --SNR 1 \
    > /opt/tt-ran/etc/logs/gnb.log 2>&1'

# 4. Start UE1
docker compose up -d tt-nrue1
docker exec -d tt-ue1 bash -lc '
  cd /opt/tt-ran/tt/cmake_targets/ran_build/build &&
  exec ./nr-uesoftmodem \
    --uicc0.imsi 001010000000011 \
    -C 3619200000 -r 106 --numerology 1 --ssb 516 \
    -E --sa --rfsim --rfsimulator.options chanmod \
    -O ../../../ci-scripts/conf_files/nrue.uicc.conf \
    --TAP 1 --rfsimulator.serveraddr 192.168.70.140 \
    > /opt/oai-nr-ue/etc/logs/ue1.log 2>&1'
```

To only change the channel or gNB flags between runs, don't recreate anything — just restart the gNB process:

```bash
docker exec tt-gnb pkill -f nr-softmodem      # then re-run step 3 above
```

The channel data the gNB actually reads is `channel/channel_clean.txt` (hardcoded in `executables/nr-softmodem.c`), edited on the host.

---

## 1. ONLY RUN FROM FRESH Optional scoped cleanup

Only remove resources belonging to this TinyTwin setup:

```bash
cd /home/ways_lab/repos/Tiny_Twin
docker compose -f sims/siso/docker-compose.yaml down --remove-orphans
docker compose -f sims/oai-cn/docker-compose.yaml down --remove-orphans
docker rm -f tt-gnb tt-ue1 2>/dev/null || true
docker image rm tt-gnb:v2 tt-nrue:v2 2>/dev/null || true
```

## 2. Build the TinyTwin images (everytime you make changes to repo)

```bash
cd /home/ways_lab/repos/Tiny_Twin

docker build --target tt-gnb \
  --file docker/tinytwin/Dockerfile.TTgNB.ubuntu22 \
  --tag tt-gnb:v2 .

docker build --target tt-nrue \
  --file docker/tinytwin/Dockerfile.TTnrUE.ubuntu22 \
  --tag tt-nrue:v2 .
```

## 3. Start the 5G core

```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/oai-cn
docker compose pull
docker compose up -d
docker compose ps
```

Wait until every core service reports `healthy`.

## 4. Build the gNB build container [FIRST TIME ONLY?]

The repository is mounted over the image's build directory, so a clean build must be performed in the running container.

```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso

docker compose run -d \
  --name tt-gnb \
  --entrypoint /bin/bash \
  tt-gnb

docker exec tt-gnb bash -lc '
  cd /opt/tt-ran/tt/cmake_targets &&
  ./build_oai -C &&
  ./build_oai -I -w SIMU --nrUE --gNB \
    --cmake-opt "-DCMAKE_C_FLAGS=-fopenmp" \
    --cmake-opt "-DCMAKE_CXX_FLAGS=-fopenmp"
'
```

## 5. Run the gNB - To change channel, stop gnb process, save another file as  local Tiny_Twin/channel/channel_clean.txt, run gnb again

```bash 
# ONLY IF YOU NEED TO KILL IT
docker exec tt-gnb pkill -f nr-softmodem        # stop the running gNB


docker exec -d tt-gnb bash -lc '
  cd /opt/tt-ran/tt/cmake_targets/ran_build/build &&
  exec ./nr-softmodem \
    -O ../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.conf \
    --rfsim -E --sa \
    --rfsimulator.options chanmod \
    --TAP 1 --TTI 1 --SNR 1 \ 
    > /opt/tt-ran/etc/logs/gnb.log 2>&1 # Run gnb
'
```

Confirm it registered with the AMF:

```bash
docker exec tt-gnb pgrep -a nr-softmodem
docker logs --tail 100 oai-amf
```

## 6. Start UE1

Use IMSI `001010000000011`; it matches the subscriber seeded in the core database.

```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso
docker compose up -d tt-nrue1

docker exec -d tt-ue1 bash -lc '
  cd /opt/tt-ran/tt/cmake_targets/ran_build/build &&
  exec ./nr-uesoftmodem \
    --uicc0.imsi 001010000000011 \
    -C 3619200000 -r 106 --numerology 1 --ssb 516 \
    -E --sa --rfsim --rfsimulator.options chanmod \
    -O ../../../ci-scripts/conf_files/nrue.uicc.conf \
    --TAP 1 --rfsimulator.serveraddr 192.168.70.140 \
    > /opt/oai-nr-ue/etc/logs/ue1.log 2>&1
'
```

## 7. Verify the system

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker exec tt-ue1 ip -brief addr
docker exec oai-ext-dn ping -c 3 10.0.0.2
docker exec tt-ue1 ping -I oaitun_ue1 -c 3 192.168.70.135
```

A successful UE has `oaitun_ue1` at `10.0.0.2/24`, and both pings return zero packet loss.

Logs are written to:

```text
logs_gnb/gnb.log
logs_ue/ue1.log
```

## 8. Plot metrics in Grafana

The RAN publishes RT-E2 metrics over ZMQ; the pipeline turns them into Grafana plots:

```text
gNB (nr-softmodem)  tcp://192.168.70.140:5555  →  edgeric muApp3 exporter :8000
  →  Prometheus 192.168.70.167:9090  →  Grafana 192.168.70.168:3000
```

All services share the external `oai-cn5g-public-net` network.

**Prerequisite:** the gNB must be the EdgeRIC-enabled softmodem publishing on `tcp://192.168.70.140:5555`, i.e. run the RAN from `sims/siso/tti_experiments` (the gNB container is at `192.168.70.140` there), and a UE must be attached and passing traffic.

### 8.1 Start the metrics exporter (Prometheus target on :8000)

The edgeric image builds on **Ubuntu 22.04** (`OS_VERSION` arg in `tti_experiments/docker-compose.yaml`). Do not switch it to 24.04: that base drops `qt5-default` and `python3-distutils`, and PEP-668 blocks the `pip3 install` steps. The Dockerfile already handles the known build/runtime pitfalls:

- replaces `qt5-default` with `qtbase5-dev` (removed after Ubuntu 20.04),
- reinstalls `sympy`/`mpmath` under pip so `torchvision` resolves cleanly,
- pins `protobuf==3.20.*` — the checked-in `*_pb2.py` files were generated with an older protoc and crash on protobuf >= 4 (`Descriptors cannot be created directly`).

```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments
docker compose build edgeric        # first time only (~5 min)
docker compose up -d edgeric        # comes up at 192.168.70.166

# start the exporter, logging to a file so failures are visible
docker exec -d edgeric_v2_2 bash -lc \
  'cd /home/EdgeRIC/muApp3 && exec python3 muApp3_monitor_grafana.py > /tmp/grafana_exporter.log 2>&1'

# confirm the process is alive before checking metrics
docker exec edgeric_v2_2 bash -lc 'pgrep -af muApp3_monitor_grafana || echo "exporter NOT running"'

# verify it is serving metrics (curl is not installed in the image; use python)
docker exec edgeric_v2_2 bash -lc \
  'python3 -c "import urllib.request; print(urllib.request.urlopen(\"http://localhost:8000/metrics\").read().decode())"' | grep ue_
```

The `ue_*` lines only appear once the gNB is publishing RT-E2 metrics and a UE is attached; before that the endpoint responds but lists only the default `python_*`/`process_*` metrics.

**Troubleshooting** — if the metrics check errors with `ConnectionRefusedError: [Errno 111] Connection refused`, the exporter process is not running. Check `pgrep` (above) and read `/tmp/grafana_exporter.log` inside the container for the cause (a common one is the protobuf import crash, fixed by the `protobuf==3.20.*` pin). Re-run the `docker exec -d` start command after fixing.

### 8.2 Start Prometheus

```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments/edgeric-v2/muApp3/docker/prometheus
docker compose up -d
# confirm the OAI5G_metrics target is UP at http://localhost:9090/targets
```

### 8.3 Start Grafana

```bash
cd /home/ways_lab/repos/Tiny_Twin/sims/siso/tti_experiments/edgeric-v2/muApp3/docker/grafana
docker compose up -d
```

### 8.4 Configure the Grafana UI

Open **http://localhost:3000** (default login `admin` / `admin`).

The repo ships no datasource/dashboard provisioning, so wire it up once:

1. Connections → Data sources → add **Prometheus**, URL `http://192.168.70.167:9090` (or `http://prometheus:9090`) → Save & test.
2. New dashboard → Add panel, and enter PromQL queries. Available metrics (per-UE via the `rnti` label, plus `tot_*` aggregates):
   - `ue_cqi`, `ue_snr`
   - `ue_avg_tx_throughput`, `ue_avg_rx_throughput`
   - `ue_avg_dl_buffer`, `ue_avg_ul_buffer`
   - `tot_avg_tx_throughput`, `tot_avg_rx_throughput`, `tot_avg_dl_buffer`, `tot_avg_ul_buffer`
3. Use `{{rnti}}` in the panel legend to label each UE series, and set a low auto-refresh (the Grafana compose allows 50 ms) for near real-time TTI-level updates.

Throughput gauges stay at zero until `tti_count - first_tti >= 1000` TTIs (~1 s of traffic), so give the UE a moment of load before the plots populate.

## Stop the system

```bash
cd /home/ways_lab/repos/Tiny_Twin

# monitoring stack
docker compose -f sims/siso/tti_experiments/edgeric-v2/muApp3/docker/grafana/docker-compose.yml down
docker compose -f sims/siso/tti_experiments/edgeric-v2/muApp3/docker/prometheus/docker-compose.yml down
docker compose -f sims/siso/tti_experiments/docker-compose.yaml down --remove-orphans

# RAN and core
docker compose -f sims/siso/docker-compose.yaml down --remove-orphans
docker compose -f sims/oai-cn/docker-compose.yaml down --remove-orphans
docker rm -f tt-gnb 2>/dev/null || true
```
