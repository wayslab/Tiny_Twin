#!/usr/bin/env python3
"""SRS-channel capture for the 2x2 2-tap-phase scenario.

Brings up a real 2x2 link (gNB nb_tx=2/nb_rx=2, UE 2 antennas) over OAI rfsim
with the frequency-selective per-antenna phase channel (channel_mimo2tap_phase),
and runs muApp_srs_aoa.py in the edgeric container to capture the NEW SRS-RIC
per-antenna channel export (srs_channel) plus the DMRS ul_channel for comparison.

  source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
  python3 srs_tti_experiment.py --snr 30 --duration 40 --out ./plot/mimo2x2_2tap_phase

Output: <out>/srs_cap.txt  (S = SRS, F = DMRS lines) + <out>/gnb.log
"""
import os
import time
import shutil
import argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RPI_DIR = os.path.dirname(HERE)
SISO_DIR = os.path.dirname(RPI_DIR)
SIMS_DIR = os.path.dirname(SISO_DIR)
TT_ROOT = os.path.dirname(SIMS_DIR)
REPO_ROOT = os.path.dirname(TT_ROOT)

CHANNEL_DIR = os.path.join(TT_ROOT, "channel")
ETHAN_CHAN_DIR = os.path.join(CHANNEL_DIR, "ethan_chan")
TINYTWIN_OAI = os.path.join(REPO_ROOT, "tinytwin-oai")
UE_CONF = os.path.join(TT_ROOT, "ci-scripts", "conf_files", "nrue.uicc.conf")
LOGS_GNB = os.path.join(TT_ROOT, "logs_gnb")
LOGS_UE = os.path.join(TT_ROOT, "logs_ue")

GNB_CONF = os.path.join(TT_ROOT, "targets", "PROJECTS", "GENERIC-NR-5GC", "CONF",
                        "gnb.sa.band78.fr1.106PRB.usrpb210.2x2.ethan.conf")

CHANNEL_REAL = os.path.join(CHANNEL_DIR, "channel_real.txt")
CHANNEL_IMAG = os.path.join(CHANNEL_DIR, "channel_imag.txt")
JAMMER_REAL = os.path.join(CHANNEL_DIR, "jammer_real.txt")
JAMMER_IMAG = os.path.join(CHANNEL_DIR, "jammer_imag.txt")

OLD_TTI_EXP = os.path.join(SISO_DIR, "tti_experiments")
EDGERIC_DIR = os.path.join(OLD_TTI_EXP, "edgeric-v2")
OAI_CN_COMPOSE = os.path.join(SIMS_DIR, "oai-cn", "docker-compose.yaml")
EDGERIC_PROM = os.path.join(EDGERIC_DIR, "muApp3", "docker", "prometheus", "docker-compose.yml")
EDGERIC_GRAFANA = os.path.join(EDGERIC_DIR, "muApp3", "docker", "grafana", "docker-compose.yml")

RUN_SH = os.path.join(HERE, "ethan_run_gnb.sh")
STOP_SH = os.path.join(RPI_DIR, "stop.sh")
BUILD_SH = os.path.join(RPI_DIR, "build.sh")
MUAPP_SRC = os.path.join(HERE, "muApp_srs_aoa.py")
MUAPP_DST = os.path.join(EDGERIC_DIR, "muApp_srs_aoa.py")
os.chdir(HERE)

PLOSS_DB, QUAD_OFFSET_DB, MODELLIST = 15.0, 3.01, "modellist_rfsimu_1"


def snr_to_noise_power_db(snr):
    return (PLOSS_DB - QUAD_OFFSET_DB - snr) / 2.0


def stage_phase_channel():
    """Stage the 2x2 2-tap per-antenna-phase channel (real + imag)."""
    rsrc = os.path.join(ETHAN_CHAN_DIR, "channel_mimo2tap_phase_real.txt")
    isrc = os.path.join(ETHAN_CHAN_DIR, "channel_mimo2tap_phase_imag.txt")
    shutil.copy(rsrc, CHANNEL_REAL)
    shutil.copy(isrc, CHANNEL_IMAG)
    print(f"[srs] staged 2x2 2-tap phase channel -> {CHANNEL_REAL} (+imag)")


def clear_jammer():
    for p in (JAMMER_REAL, JAMMER_IMAG):
        with open(p, "w") as f:
            f.write("0.000000\n")


COMPOSE_GNB = f'''
services:
    tt-gnb:
        image: tt-gnb:v2
        container_name: tt-gnb
        privileged: true
        cpuset: "5,6,7,8"
        cap_drop: [ALL]
        cap_add: [NET_ADMIN, NET_RAW]
        volumes:
            - {TINYTWIN_OAI}:/opt/tt-ran/tt:rw
            - {GNB_CONF}:/opt/tt-ran/etc/gnb.conf
            - {CHANNEL_REAL}:/opt/tt-ran/tt/channel/channel_real.txt
            - {CHANNEL_IMAG}:/opt/tt-ran/tt/channel/channel_imag.txt
            - {LOGS_GNB}:/opt/tt-ran/etc/logs
            - {RUN_SH}:/opt/tt-ran/run.sh
            - {STOP_SH}:/opt/tt-ran/stop.sh
            - {BUILD_SH}:/opt/tt-ran/build.sh
        entrypoint: /bin/bash
        stdin_open: true
        tty: true
        networks:
            public_net:
                ipv4_address: 192.168.70.140
'''

COMPOSE_END = f'''
    edgeric:
        container_name: edgeric_v2_2
        image: edgeric/v2
        build:
            context: {EDGERIC_DIR}
            dockerfile: ../Dockerfile-edgeric
            args:
                OS_VERSION: "24.04"
        privileged: true
        ports: ["7000:7000"]
        cap_add: [SYS_NICE, CAP_SYS_PTRACE]
        volumes:
            - /tmp/.X11-unix:/tmp/.X11-unix:rw
            - /dev:/dev
            - {EDGERIC_DIR}:/home/EdgeRIC:rw
        networks:
            public_net:
                ipv4_address: 192.168.70.166

networks:
    public_net:
        driver: bridge
        external: true
        name: oai-cn5g-public-net
'''


def compose_ue(noise_override, tap=16):
    i = 161
    return f'''
    tt-nrue{i-160}:
        image: tt-nrue:v2
        container_name: tt-ue{i-160}
        privileged: true
        cpuset: "9,15,16,17,18,19"
        cap_drop: [ALL]
        cap_add: [NET_ADMIN, NET_RAW]
        volumes:
            - {TINYTWIN_OAI}:/opt/tt-ran/tt:rw
            - {UE_CONF}:/opt/oai-nr-ue/etc/nr-ue.conf
            - {LOGS_UE}:/opt/oai-nr-ue/etc/logs
        entrypoint: >
            /bin/bash -c "cd tt/cmake_targets/ran_build/build/ &&
            ./nr-uesoftmodem --uicc0.imsi 001010000000011 -C 3619200000 -r 106 --numerology 1 --ssb 516 -E --sa --rfsim --rfsimulator.options chanmod -O ../../../ci-scripts/conf_files/nrue.uicc.conf --TAP {tap} {noise_override} --ue-nb-ant-rx 2 --ue-nb-ant-tx 2 --rfsimulator.serveraddr 192.168.70.140 &&
            exec /bin/bash"
        stdin_open: true
        tty: true
        networks:
            public_net:
                ipv4_address: 192.168.70.{i-10}
        devices:
            - /dev/net/tun:/dev/net/tun
        healthcheck:
            test: /bin/bash -c "pgrep nr-uesoftmodem"
            interval: 10s
            timeout: 5s
            retries: 5
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snr", type=float, default=30.0)
    ap.add_argument("--duration", type=int, default=40)
    ap.add_argument("--nsnap", type=int, default=200)
    ap.add_argument("--tap", type=int, default=16)
    ap.add_argument("--out", default=os.path.join(HERE, "plot", "mimo2x2_2tap_phase"))
    a = ap.parse_args()

    outdir = a.out
    os.makedirs(outdir, exist_ok=True)
    shutil.copy(MUAPP_SRC, MUAPP_DST)
    print(f"[srs] synced muApp -> {MUAPP_DST}")
    print(f"[srs] gNB conf: {GNB_CONF}")

    stage_phase_channel()
    clear_jammer()
    noise_db = snr_to_noise_power_db(a.snr)
    noise_override = (f"--channelmod.{MODELLIST}.[0].noise_power_dB {noise_db:.4f} "
                      f"--channelmod.{MODELLIST}.[1].noise_power_dB {noise_db:.4f}")
    print(f"[srs] snr {a.snr} dB -> noise_power_dB {noise_db:.4f}")

    os.system(f"docker compose -f {OAI_CN_COMPOSE} up -d")
    os.system(f"docker compose -f {EDGERIC_PROM} up -d")
    os.system(f"docker compose -f {EDGERIC_GRAFANA} up -d")
    time.sleep(8)

    with open("docker-compose.yaml", "w") as f:
        f.write(COMPOSE_GNB + compose_ue(noise_override, a.tap) + COMPOSE_END)

    os.system("docker compose up -d tt-gnb")
    time.sleep(10)
    os.system("docker compose up -d edgeric")
    time.sleep(10)
    os.system("docker exec -d edgeric_v2_2 python3 ./muApp3/muApp3_monitor_grafana.py")
    os.system("docker exec tt-gnb chmod +x run.sh build.sh stop.sh")
    os.system("docker exec -d tt-gnb ./run.sh")
    print("[srs] gNB launched; waiting for UE attach ...")
    os.system("docker compose up -d tt-nrue1")
    time.sleep(28)

    # light UL traffic so the scheduler runs PUSCH/SRS continuously
    os.system('docker exec -d tt-ue1 bash -c '
              '"stdbuf -oL iperf -s -u -i 1 -fk -B 10.0.0.2 > /tmp/iperf_dl.txt 2>&1"')
    os.system('docker exec -d oai-ext-dn bash -c '
              '"stdbuf -oL iperf -s -i 1 -fk -B 192.168.70.135 -p 5212 > /tmp/iperf_ul.txt 2>&1"')
    time.sleep(2)
    os.system("docker exec -d tt-ue1 iperf -t 86400 -i 1 -fk -c 192.168.70.135 "
              "-b 8M -B 10.0.0.2 -p 5212")
    time.sleep(5)

    print(f"[srs] running muApp_srs_aoa.py live ({a.nsnap} SRS snapshots) ...")
    os.system(f"docker exec -d edgeric_v2_2 python3 /home/EdgeRIC/muApp_srs_aoa.py "
              f"--n {a.nsnap} --out /tmp/srs_cap.txt --timeout {a.duration}")
    time.sleep(a.duration)

    os.system(f"docker cp edgeric_v2_2:/tmp/srs_cap.txt {outdir}/srs_cap.txt")
    os.system(f"docker cp tt-gnb:/opt/tt-ran/tt/cmake_targets/ran_build/build/gnb.log {outdir}/gnb.log")

    os.system("docker exec -d tt-gnb ./stop.sh")
    time.sleep(3)
    os.system("docker compose down")
    os.system(f"docker compose -f {EDGERIC_GRAFANA} down")
    os.system(f"docker compose -f {EDGERIC_PROM} down")

    # quick readout
    cap = os.path.join(outdir, "srs_cap.txt")
    if os.path.isfile(cap):
        with open(cap) as f:
            lines = [l for l in f if l.strip()]
        ns = sum(1 for l in lines if l.startswith("S "))
        nf = sum(1 for l in lines if l.startswith("F "))
        print(f"[srs] captured {ns} SRS lines, {nf} DMRS lines -> {cap}")
    srslog = os.popen(f"grep -c 'srs-ric' {outdir}/gnb.log 2>/dev/null").read().strip()
    print(f"[srs] gNB '[srs-ric] exported' log lines: {srslog}")


if __name__ == "__main__":
    main()
