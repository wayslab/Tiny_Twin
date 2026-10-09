#!/usr/bin/env python3
"""4-antenna lambda/2 AoA experiment driver (gNB 4-rx ULA, 1-Tx UE).

For each ground-truth AoA in --angles (degrees):
  1. write channel/aoa_steer.txt = theta0 (radians); the patched rfsim gNB-rx
     path (radio/rfsimulator/simulator.c) rotates rx antenna a by the lambda/2
     ULA steering phase  phi_a = pi * a * sin(theta0),  so the 4 gNB antennas
     carry a known plane-wave phase ramp from the single-Tx UE.
  2. stage the flat unit-tap channel (channel_aoa_flat_real.txt) + zero imag.
  3. bring up 5GC + edgeric + gNB (4-rx conf) + 1 UE (1 antenna) over rfsim.
  4. capture the MIMO-RIC per-antenna UL channel export (nrx=4) from the edgeric
     container -> plot/aoa/aoa<deg>/chan_cap.txt.
  5. tear the RAN down and move to the next angle.

Then run aoa_analyze.py to compare the extracted 4-antenna channel to ground
truth (magnitude + phase) and estimate AoA / plot angle error.

  source /home/ways_lab/repos/venv/sub6isac_env/bin/activate
  python3 aoa_tti_experiment.py --angles -30 0 30 --snr 30 --duration 40

This driver is standalone (no threads); it mirrors the working bring-up sequence
in ethan_tti_experiment.py but is specialized for the single-UE 4-rx AoA case.
"""
import os
import sys
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

# default gNB conf: the 4-rx ULA conf (nb_rx=4, nb_tx=1)
GNB_CONF = os.path.join(TT_ROOT, "targets", "PROJECTS", "GENERIC-NR-5GC", "CONF",
                        "gnb.sa.band78.fr1.106PRB.usrpb210.4rx.ethan.conf")

CHANNEL_REAL = os.path.join(CHANNEL_DIR, "channel_real.txt")   # what rfsim opens
CHANNEL_IMAG = os.path.join(CHANNEL_DIR, "channel_imag.txt")
JAMMER_REAL = os.path.join(CHANNEL_DIR, "jammer_real.txt")
JAMMER_IMAG = os.path.join(CHANNEL_DIR, "jammer_imag.txt")
AOA_STEER = os.path.join(CHANNEL_DIR, "aoa_steer.txt")         # gNB reads this

# shared infra (lives in the original tti_experiments/ folder)
OLD_TTI_EXP = os.path.join(SISO_DIR, "tti_experiments")
EDGERIC_DIR = os.path.join(OLD_TTI_EXP, "edgeric-v2")
OAI_CN_COMPOSE = os.path.join(SIMS_DIR, "oai-cn", "docker-compose.yaml")
EDGERIC_PROM = os.path.join(EDGERIC_DIR, "muApp3", "docker", "prometheus", "docker-compose.yml")
EDGERIC_GRAFANA = os.path.join(EDGERIC_DIR, "muApp3", "docker", "grafana", "docker-compose.yml")

RUN_SH = os.path.join(HERE, "ethan_run_gnb.sh")   # runs whatever conf is at /opt/tt-ran/etc/gnb.conf
STOP_SH = os.path.join(RPI_DIR, "stop.sh")
BUILD_SH = os.path.join(RPI_DIR, "build.sh")

# The AoA muApp. The CANONICAL copy lives here in Ethan/; it is synced into the
# edgeric mount (/home/EdgeRIC/muApp_aoa.py in the container) at runtime, so
# editing Ethan/muApp_aoa.py is all that is needed.
MUAPP_SRC = os.path.join(HERE, "muApp_aoa.py")
MUAPP_DST = os.path.join(EDGERIC_DIR, "muApp_aoa.py")
os.chdir(HERE)


def sync_muapp():
    shutil.copy(MUAPP_SRC, MUAPP_DST)
    print(f"[aoa] synced muApp: {MUAPP_SRC} -> {MUAPP_DST} (mounted as /home/EdgeRIC/muApp_aoa.py)")

# ---- noise anchor (same model as ethan_tti_experiment.py) --------------------
PLOSS_DB = 15.0
QUAD_OFFSET_DB = 3.01
MODELLIST = "modellist_rfsimu_1"


def snr_to_noise_power_db(target_snr_db, tap_ref=1.0):
    return (20.0 * np.log10(tap_ref) + PLOSS_DB - QUAD_OFFSET_DB - target_snr_db) / 2.0


def stage_channel(delay=0, nrows=50000):
    """Stage the SISO UL signal channel (+ zero imag). The AoA per-antenna PHASE
    is injected in the gNB rx path (simulator.c); this file supplies the signal
    channel seen on the UL (txAddInput, fpr[1]).

    delay == 0 -> flat unit tap at delay 0  (|H|=1, flat phase): pure AoA.
    delay  > 0 -> a single unit tap at sample-delay `delay` (taplen = delay+1):
                  a pure propagation DELAY, so H(f)=exp(-j2*pi*k*delay/Nfft) adds a
                  common linear phase RAMP across subcarriers on every antenna
                  (ToA), on top of the per-antenna AoA offset. Run with --TAP delay+1.
    """
    if delay <= 0:
        src = os.path.join(ETHAN_CHAN_DIR, "channel_aoa_flat_real.txt")
        with open(src) as f:
            n = sum(1 for _ in f)
        with open(src) as s, open(CHANNEL_REAL, "w") as d:
            d.write(s.read())
        with open(CHANNEL_IMAG, "w") as d:
            d.write("".join("0.000000\n" for _ in range(n)))
        print(f"[aoa] staged flat channel (delay 0): {n} rows -> {CHANNEL_REAL}")
    else:
        row = " ".join(["0.000000"] * delay + ["1.000000"]) + "\n"   # tap at delay
        with open(CHANNEL_REAL, "w") as d:
            d.write(row * nrows)
        with open(CHANNEL_IMAG, "w") as d:
            d.write("0.000000\n" * nrows)
        print(f"[aoa] staged delayed channel: tap @ delay {delay} samples "
              f"(taplen {delay+1}), {nrows} rows -> {CHANNEL_REAL}")


def clear_jammer():
    for p in (JAMMER_REAL, JAMMER_IMAG):
        with open(p, "w") as f:
            f.write("0.000000\n")


def write_aoa_steer(theta_rad):
    with open(AOA_STEER, "w") as f:
        f.write(f"{theta_rad:.8f}\n")
    print(f"[aoa] aoa_steer.txt = {theta_rad:.6f} rad "
          f"({np.degrees(theta_rad):.2f} deg) -> {AOA_STEER}")


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


def compose_ue(noise_override, tap=1):
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
            ./nr-uesoftmodem --uicc0.imsi 001010000000011 -C 3619200000 -r 106 --numerology 1 --ssb 516 -E --sa --rfsim --rfsimulator.options chanmod -O ../../../ci-scripts/conf_files/nrue.uicc.conf --TAP {tap} {noise_override} --rfsimulator.serveraddr 192.168.70.140 &&
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


def run_angle(deg, snr, duration, nsnap, out_root, delay=0):
    theta = np.radians(deg)
    tag = f"aoa{deg:+03d}".replace("+", "p").replace("-", "m")
    outdir = os.path.join(out_root, tag)
    os.makedirs(outdir, exist_ok=True)
    print(f"\n========== AoA ground truth = {deg:+.1f} deg "
          f"({theta:+.4f} rad), delay = {delay} samples -> {outdir} ==========")

    tap = delay + 1 if delay > 0 else 1
    write_aoa_steer(theta)
    stage_channel(delay)
    clear_jammer()

    noise_db = snr_to_noise_power_db(snr)
    noise_override = (f"--channelmod.{MODELLIST}.[0].noise_power_dB {noise_db:.4f} "
                      f"--channelmod.{MODELLIST}.[1].noise_power_dB {noise_db:.4f}")
    print(f"[aoa] snr {snr} dB -> noise_power_dB {noise_db:.4f}")

    # core + monitoring
    os.system(f"docker compose -f {OAI_CN_COMPOSE} up -d")
    os.system(f"docker compose -f {EDGERIC_PROM} up -d")
    os.system(f"docker compose -f {EDGERIC_GRAFANA} up -d")
    time.sleep(8)

    with open("docker-compose.yaml", "w") as f:
        f.write(COMPOSE_GNB + compose_ue(noise_override, tap) + COMPOSE_END)

    os.system("docker compose up -d tt-gnb")
    time.sleep(10)
    os.system("docker compose up -d edgeric")
    time.sleep(10)
    os.system("docker exec -d edgeric_v2_2 python3 ./muApp3/muApp3_monitor_grafana.py")
    os.system("docker exec tt-gnb chmod +x run.sh build.sh stop.sh")
    os.system("docker exec -d tt-gnb ./run.sh")
    print("[aoa] gNB launched; waiting for UE attach ...")
    os.system("docker compose up -d tt-nrue1")
    time.sleep(25)   # RACH/RRC/PDU-session setup before PUSCH flows

    # light UL traffic so PUSCH (and its DMRS channel estimate) runs continuously
    os.system('docker exec -d tt-ue1 bash -c '
              '"stdbuf -oL iperf -s -u -i 1 -fk -B 10.0.0.2 > /tmp/iperf_dl.txt 2>&1"')
    os.system('docker exec -d oai-ext-dn bash -c '
              '"stdbuf -oL iperf -s -i 1 -fk -B 192.168.70.135 -p 5212 > /tmp/iperf_ul.txt 2>&1"')
    time.sleep(2)
    os.system("docker exec -d tt-ue1 iperf -t 86400 -i 1 -fk -c 192.168.70.135 "
              "-b 8M -B 10.0.0.2 -p 5212")
    time.sleep(5)

    # Run the AoA muApp INSIDE the edgeric container: it subscribes to the live
    # channel stream, extracts the 4-antenna CIR, and computes AoA in-container.
    # It also dumps the raw per-antenna channel (chan_cap) from the SAME extraction
    # for the magnitude/phase-vs-ground-truth comparison.
    print(f"[aoa] running muApp_aoa.py live ({nsnap} snapshots) ...")
    os.system(f"docker exec -d edgeric_v2_2 python3 /home/EdgeRIC/muApp_aoa.py "
              f"--n {nsnap} --out /tmp/aoa_muapp.txt --chan-out /tmp/aoa_chan_cap.txt "
              f"--timeout {duration} --spacing 0.5")
    time.sleep(duration)

    os.system(f"docker cp edgeric_v2_2:/tmp/aoa_muapp.txt {outdir}/aoa_muapp.txt")
    os.system(f"docker cp edgeric_v2_2:/tmp/aoa_chan_cap.txt {outdir}/chan_cap.txt")
    os.system(f"docker cp tt-gnb:/opt/tt-ran/tt/cmake_targets/ran_build/build/gnb.log {outdir}/gnb.log")

    # teardown RAN (leave core + monitoring up for the next angle)
    os.system("docker exec -d tt-gnb ./stop.sh")
    time.sleep(3)
    os.system("docker compose down")
    time.sleep(3)

    # quick sanity readout
    if os.path.isfile(os.path.join(outdir, "chan_cap.txt")):
        with open(os.path.join(outdir, "chan_cap.txt")) as f:
            lines = [l for l in f if l.strip()]
        print(f"[aoa] captured {len(lines)} snapshot lines -> {outdir}/chan_cap.txt")
    return outdir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--angles", type=float, nargs="+", default=[-30, 0, 30],
                    help="ground-truth AoAs in degrees (one OAI run each)")
    ap.add_argument("--snr", type=float, default=30.0)
    ap.add_argument("--duration", type=int, default=40,
                    help="capture window seconds per angle")
    ap.add_argument("--nsnap", type=int, default=400,
                    help="UL-channel snapshots to capture per angle")
    ap.add_argument("--out", default=os.path.join(HERE, "plot", "aoa"))
    ap.add_argument("--delay", type=int, default=0,
                    help="propagation delay in samples added to the channel (tap @ "
                         "sample-delay D); 0 = flat (pure AoA). >0 adds a ToA phase "
                         "ramp across subcarriers. Runs with --TAP D+1.")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    sync_muapp()                       # push the canonical Ethan/ muApp into the edgeric mount
    print(f"[aoa] gNB conf: {GNB_CONF}")
    print(f"[aoa] angles: {a.angles} deg   snr={a.snr} dB   duration={a.duration}s   delay={a.delay} samples")

    done = []
    for deg in a.angles:
        done.append((deg, run_angle(int(deg), a.snr, a.duration, a.nsnap, a.out, a.delay)))

    # final full teardown
    os.system(f"docker compose -f {EDGERIC_GRAFANA} down")
    os.system(f"docker compose -f {EDGERIC_PROM} down")
    print("\n[aoa] all angles done:")
    for deg, d in done:
        print(f"   {deg:+.1f} deg -> {d}/chan_cap.txt")
    print(f"\n[aoa] now analyze:\n  python3 aoa_analyze.py --root {a.out} "
          f"--angles {' '.join(str(int(d)) for d,_ in done)}")


if __name__ == "__main__":
    main()
