#!/usr/bin/env python3
"""mimo_tti_experiment.py — uplink-MIMO complex-channel validation (dev/mimo).

Injects a KNOWN per-rx-antenna complex UL channel (per-antenna phase offset
phi_a = pi*a*sin(theta) at propagation delay D) into the patched rfsim gNB-rx
path, brings up a 4-rx gNB + 1-Tx UE over rfsim, and captures the gNB's EXTRACTED
per-antenna PUSCH channel (MIMO-RIC ul_channel / ul_channel_ls) via a muApp in
the edgeric container. mimo_validate.py then shows extracted == input.

  source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
  cd .../tti_experiments_rpi/202610_Ethanmimo
  python3 mimo_tti_experiment.py --theta 30 --delay 3 --snr 40 --duration 40

Uplink MIMO activates only because the gNB is launched with
  --rfsimulator.options chanmod --TAP <D+1>
so taplen>0 and a channel model exists (see radio/rfsimulator/simulator.c gate).
"""
import os
import sys
import time
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
RPI_DIR = os.path.dirname(HERE)
SISO_DIR = os.path.dirname(RPI_DIR)
SIMS_DIR = os.path.dirname(SISO_DIR)
TT_ROOT = os.path.dirname(SIMS_DIR)
REPO_ROOT = os.path.dirname(TT_ROOT)

CHANNEL_DIR = os.path.join(TT_ROOT, "channel")
MIMO_GEN = os.path.join(CHANNEL_DIR, "mimo_chan", "generate_mimo.py")
TINYTWIN_OAI = os.path.join(REPO_ROOT, "tinytwin-oai")
UE_CONF = os.path.join(TT_ROOT, "ci-scripts", "conf_files", "nrue.uicc.conf")
LOGS_GNB = os.path.join(TT_ROOT, "logs_gnb")
LOGS_UE = os.path.join(TT_ROOT, "logs_ue")
GNB_CONF = os.path.join(TT_ROOT, "targets", "PROJECTS", "GENERIC-NR-5GC", "CONF",
                        "gnb.sa.band78.fr1.106PRB.usrpb210.4rx.ethan.conf")

CHANNEL_REAL = os.path.join(CHANNEL_DIR, "channel_real.txt")        # DL (UE), benign flat
CHANNEL_IMAG = os.path.join(CHANNEL_DIR, "channel_imag.txt")
CHANNEL_MIMO_REAL = os.path.join(CHANNEL_DIR, "channel_mimo_real.txt")  # UL (gNB), per-antenna
CHANNEL_MIMO_IMAG = os.path.join(CHANNEL_DIR, "channel_mimo_imag.txt")

OLD_TTI_EXP = os.path.join(SISO_DIR, "tti_experiments")
EDGERIC_DIR = os.path.join(OLD_TTI_EXP, "edgeric-v2")
OAI_CN_COMPOSE = os.path.join(SIMS_DIR, "oai-cn", "docker-compose.yaml")
STOP_SH = os.path.join(RPI_DIR, "stop.sh")
BUILD_SH = os.path.join(RPI_DIR, "build.sh")
RUN_SH = os.path.join(HERE, "mimo_run_gnb.sh")   # written per-run (carries --TAP + chanmod)

# muApp: reuse the imported AoA muApp -- it captures the raw per-antenna channel
# (F = filtered ul_channel, L = raw LS) in chan_cap.txt, exactly what we validate.
MUAPP_SRC = os.path.join(EDGERIC_DIR, "muApp_aoa.py")
MODELLIST = "channelmod.modellist_rfsimu_1"
os.chdir(HERE)


# docker may need sudo in some shells: `TT_DOCKER='sudo docker' python3 mimo_tti_experiment.py`
DOCKER = os.environ.get("TT_DOCKER", "docker")


def sh(cmd):
    if DOCKER != "docker":
        cmd = cmd.replace("docker ", DOCKER + " ")  # trailing space -> won't touch docker-compose.yaml paths
    print(f"  $ {cmd}")
    os.system(cmd)


def write_run_sh(taplen, noise_db):
    """gNB launcher: enable chanmod + --TAP so the UL MIMO gate activates; set a
    clean UL SNR on the gNB model (index [1] = rfsimu_channel_ue0) and zero its
    pathloss so the extracted channel's per-antenna phase/magnitude is clean."""
    with open(RUN_SH, "w") as f:
        f.write(f"""#!/bin/bash
cd /opt/tt-ran/tt/cmake_targets/ran_build/build/
./nr-softmodem -O /opt/tt-ran/etc/gnb.conf --rfsim -E --sa \\
  --rfsimulator.options chanmod --TAP {taplen} \\
  --{MODELLIST}.[1].noise_power_dB {noise_db:.4f} \\
  --{MODELLIST}.[1].ploss_dB 0 \\
  --SNR 1 --MCS 1 --CQI 1 --TPT 1 >gnb.log &
echo $! > pid.txt
""")
    os.chmod(RUN_SH, 0o755)


def stage_dl_flat(nrows=50000):
    """Benign flat unit-tap DL channel (UE reads channel_real.txt, --TAP 1)."""
    with open(CHANNEL_REAL, "w") as f:
        f.write("1.000000\n" * nrows)
    with open(CHANNEL_IMAG, "w") as f:
        f.write("0.000000\n" * nrows)


def compose_gnb(taplen):
    return f"""
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
            - {CHANNEL_MIMO_REAL}:/opt/tt-ran/tt/channel/channel_mimo_real.txt
            - {CHANNEL_MIMO_IMAG}:/opt/tt-ran/tt/channel/channel_mimo_imag.txt
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
"""


def compose_ue(noise_override):
    return f"""
    tt-nrue1:
        image: tt-nrue:v2
        container_name: tt-ue1
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
            ./nr-uesoftmodem --uicc0.imsi 001010000000011 -C 3619200000 -r 106 --numerology 1 --ssb 516 -E --sa --rfsim --rfsimulator.options chanmod -O ../../../ci-scripts/conf_files/nrue.uicc.conf --TAP 1 {noise_override} --rfsimulator.serveraddr 192.168.70.140 &&
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
"""


COMPOSE_END = f"""
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
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theta", type=float, default=30.0, help="plane-wave AoA (deg) -> phi_a = pi*a*sin(theta)")
    ap.add_argument("--delay", type=int, default=3, help="propagation delay D (samples); taplen = D+1")
    ap.add_argument("--snr", type=float, default=40.0, help="clean UL SNR for a crisp channel estimate")
    ap.add_argument("--duration", type=int, default=40, help="muApp capture window (s)")
    ap.add_argument("--nsnap", type=int, default=300, help="snapshots to capture")
    ap.add_argument("--out", default="./plot/mimo", help="output dir")
    args = ap.parse_args()

    taplen = args.delay + 1
    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)

    # 1) generate the known per-antenna complex UL channel + a benign flat DL
    print(f"== staging channel: theta={args.theta} deg, delay={args.delay}, taplen={taplen} ==")
    sh(f"python3 {MIMO_GEN} --theta {args.theta} --delay {args.delay} --nant 4")
    stage_dl_flat()
    sh(f"cp {os.path.join(CHANNEL_DIR, 'mimo_chan', 'input_truth.json')} {outdir}/input_truth.json")

    # 2) gNB launcher with chanmod + --TAP; clean UL SNR (noise on model idx [1])
    #    noise_power_dB ~ -snr gives a high-SNR estimate (amplitude is common to all
    #    antennas, so SNR only affects estimate noise, not the per-antenna phase).
    noise_db = -float(args.snr)
    write_run_sh(taplen, noise_db)
    ue_noise = f"--{MODELLIST}.[0].noise_power_dB {noise_db:.4f} --{MODELLIST}.[1].noise_power_dB {noise_db:.4f}"

    # 3) bring up core + RAN + edgeric
    sh(f"docker compose -f {OAI_CN_COMPOSE} up -d")
    time.sleep(8)
    with open("docker-compose.yaml", "w") as f:
        f.write(compose_gnb(taplen) + compose_ue(ue_noise) + COMPOSE_END)
    sh("cp " + MUAPP_SRC + " " + os.path.join(EDGERIC_DIR, "muApp_aoa.py"))  # sync muApp into mount
    sh("docker compose up -d tt-gnb"); time.sleep(10)
    sh("docker compose up -d edgeric"); time.sleep(10)
    sh("docker exec tt-gnb chmod +x run.sh stop.sh build.sh")
    sh("docker exec -d tt-gnb ./run.sh")
    print("[mimo] gNB launched; waiting for UE attach ...")
    sh("docker compose up -d tt-nrue1"); time.sleep(25)

    # 4) light UL traffic so PUSCH (and its DMRS channel estimate) runs continuously
    sh('docker exec -d tt-ue1 bash -c "stdbuf -oL iperf -s -u -i 1 -fk -B 10.0.0.2 > /tmp/iperf_dl.txt 2>&1"')
    sh('docker exec -d oai-ext-dn bash -c "stdbuf -oL iperf -s -i 1 -fk -B 192.168.70.135 -p 5212 > /tmp/iperf_ul.txt 2>&1"')
    time.sleep(2)
    sh("docker exec -d tt-ue1 iperf -t 86400 -i 1 -fk -c 192.168.70.135 -b 8M -B 10.0.0.2 -p 5212")
    time.sleep(5)

    # 5) capture the EXTRACTED per-antenna channel (F=filtered, L=raw-LS) via the muApp
    print(f"[mimo] capturing extracted channel ({args.nsnap} snapshots) ...")
    sh(f"docker exec -d edgeric_v2_2 python3 /home/EdgeRIC/muApp_aoa.py "
       f"--n {args.nsnap} --out /tmp/mimo_muapp.txt --chan-out /tmp/mimo_chan_cap.txt "
       f"--timeout {args.duration} --spacing 0.5")
    time.sleep(args.duration + 5)
    sh(f"docker cp edgeric_v2_2:/tmp/mimo_chan_cap.txt {outdir}/chan_cap.txt")
    sh(f"docker cp edgeric_v2_2:/tmp/mimo_muapp.txt {outdir}/muapp.txt")
    sh(f"docker cp tt-gnb:/opt/tt-ran/tt/cmake_targets/ran_build/build/gnb.log {outdir}/gnb.log")

    # 6) teardown
    sh("docker exec -d tt-gnb ./stop.sh"); time.sleep(3)
    sh("docker compose down"); time.sleep(3)

    if os.path.isfile(f"{outdir}/chan_cap.txt"):
        n = sum(1 for l in open(f"{outdir}/chan_cap.txt") if l.strip())
        print(f"[mimo] captured {n} channel lines -> {outdir}/chan_cap.txt")
        print(f"[mimo] validate:  python3 mimo_validate.py --dir {outdir}")
    else:
        print("[mimo] WARNING: no chan_cap.txt captured — check gnb.log / UE attach")


if __name__ == "__main__":
    main()
