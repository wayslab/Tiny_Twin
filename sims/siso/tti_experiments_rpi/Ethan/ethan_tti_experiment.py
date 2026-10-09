"""
raj TTI experiment (sims/siso/tti_experiments_rpi/raj).

Plays a raj_chan CIR trace (channel/raj_chan/*.txt) through OAI rfsim and sets
the channel noise power from Python only (no conf edits) so that a unit tap
maps to a chosen SNR. Self-contained: every external dependency (edgeric-v2,
oai-cn, conf, channel, logs) is referenced by absolute path, and the driver
runs with cwd = this raj/ folder, so ALL outputs (docker-compose.yaml, plot/,
copied snr.txt/tti.txt) land here under raj/plot/ue<#UE>_<#tap>/.

  # list available raj_chan channels
  python3 raj_tti_experiment.py --list

  # <#UE> <#taps>, default channel channel_gt_los.txt, default 30 dB @ unit tap
  python3 raj_tti_experiment.py 1 1

  # pick a channel and SNR anchor
  python3 raj_tti_experiment.py 1 1 --channel channel_gt_nlos.txt --snr 30

Noise is set via OAI command-line config override on the UE entrypoint
(channelmod.modellist_rfsimu_1.[0]/[1].noise_power_dB); see the noise block
below. If you modify any C code, rebuild first:
  docker compose up -d tt-gnb && docker exec tt-gnb ./build.sh
"""
import sys
import os
import glob
import argparse
import time
import threading
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Path anchors. This driver lives in
#   Tiny_Twin/sims/siso/tti_experiments_rpi/raj/
# Shared OAI/edgeric infra still lives in the original tti_experiments/ and in
# sims/oai-cn. We reference all of it by ABSOLUTE path and run with cwd = raj/
# so that generated artifacts and plots stay inside this folder.
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))                 # .../raj
RPI_DIR = os.path.dirname(HERE)                                   # .../tti_experiments_rpi
SISO_DIR = os.path.dirname(RPI_DIR)                               # .../siso
SIMS_DIR = os.path.dirname(SISO_DIR)                             # .../sims
TT_ROOT = os.path.dirname(SIMS_DIR)                              # .../Tiny_Twin
REPO_ROOT = os.path.dirname(TT_ROOT)                             # .../repos

CHANNEL_DIR = os.path.join(TT_ROOT, "channel")
RAJ_CHAN_DIR = os.path.join(CHANNEL_DIR, "ethan_chan")   # Ethan channel+jammer traces
LOGS_DIR = os.path.join(TT_ROOT, "logs")                         # gNB writes snr/tti here
LOGS_GNB = os.path.join(TT_ROOT, "logs_gnb")
LOGS_UE = os.path.join(TT_ROOT, "logs_ue")
TINYTWIN_OAI = os.path.join(REPO_ROOT, "tinytwin-oai")           # symlink -> Tiny_Twin
GNB_CONF = os.path.join(TT_ROOT, "targets", "PROJECTS", "GENERIC-NR-5GC",
                        "CONF", "gnb.sa.band78.fr1.106PRB.usrpb210.conf")
UE_CONF = os.path.join(TT_ROOT, "ci-scripts", "conf_files", "nrue.uicc.conf")

# shared infra that still lives in the original tti_experiments/ folder
OLD_TTI_EXP = os.path.join(SISO_DIR, "tti_experiments")
EDGERIC_DIR = os.path.join(OLD_TTI_EXP, "edgeric-v2")
DOCKERFILE_EDGERIC = os.path.join(OLD_TTI_EXP, "Dockerfile-edgeric")
OAI_CN_COMPOSE = os.path.join(SIMS_DIR, "oai-cn", "docker-compose.yaml")
EDGERIC_PROM = os.path.join(EDGERIC_DIR, "muApp3", "docker", "prometheus",
                            "docker-compose.yml")
EDGERIC_GRAFANA = os.path.join(EDGERIC_DIR, "muApp3", "docker", "grafana",
                               "docker-compose.yml")

# run.sh / stop.sh / build.sh are generic gNB build/run/stop scripts shared by
# every rpi experiment, so they live one level up in tti_experiments_rpi/.
RUN_SH = os.path.join(HERE, "ethan_run_gnb.sh")   # Ethan's gNB launcher (conf = /opt/tt-ran/etc/gnb.conf)
STOP_SH = os.path.join(RPI_DIR, "stop.sh")
BUILD_SH = os.path.join(RPI_DIR, "build.sh")
os.chdir(HERE)

# The OAI rfsim C opens hardcoded container paths channel/channel_real.txt and
# channel/channel_imag.txt; channel/ is the tinytwin-oai -> Tiny_Twin symlink,
# so they are just Tiny_Twin/channel/channel_real.txt and channel_imag.txt.
CHANNEL_REAL = os.path.join(CHANNEL_DIR, "channel_real.txt")
CHANNEL_IMAG = os.path.join(CHANNEL_DIR, "channel_imag.txt")

# The OAI rfsim C code opens, inside the container, hardcoded paths
#   channel/channel_real.txt   (real taps)
#   channel/channel_imag.txt   (imag taps)
# and channel/ is the tinytwin-oai -> Tiny_Twin symlink, so these are just
# Tiny_Twin/channel/channel_real.txt and channel_imag.txt on the host.
CHANNEL_REAL = os.path.join(CHANNEL_DIR, "channel_real.txt")
CHANNEL_IMAG = os.path.join(CHANNEL_DIR, "channel_imag.txt")


def resolve_channel(name):
    """Resolve a --channel value to a real-taps file on the host.

    Lookup order: as given -> raj_chan/<name> -> channel/<name>.
    """
    for cand in (name, os.path.join(RAJ_CHAN_DIR, name),
                 os.path.join(CHANNEL_DIR, name)):
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    raise FileNotFoundError(f"channel file not found: {name}")


# jammer tap files the UE opens (channel/jammer_real.txt, channel/jammer_imag.txt);
# read through the repo bind-mount, so staging to the host path is enough.
JAMMER_REAL = os.path.join(CHANNEL_DIR, "jammer_real.txt")
JAMMER_IMAG = os.path.join(CHANNEL_DIR, "jammer_imag.txt")


def _stage_pair(real_src, real_dst, imag_dst, tag, imag_src=None):
    """Copy real_src verbatim to real_dst. For the imag taps: if imag_src exists
    (sibling '<name>_imag.txt'), stage it too so a COMPLEX channel (per-antenna
    phase) can be applied; otherwise write a matching all-zeros imag file.
    Works for SISO (1 val/row) and the MIMO matrix format (nb_rx rows/slot)."""
    with open(real_src) as f:
        nrows = sum(1 for _ in f)
    with open(real_src) as src, open(real_dst, "w") as dst:
        dst.write(src.read())
    if imag_src and os.path.exists(imag_src):
        with open(imag_src) as src, open(imag_dst, "w") as dst:
            dst.write(src.read())
        print(f"[ethan] staged {tag} imag: {imag_src} -> {imag_dst}  (COMPLEX channel)")
    else:
        with open(imag_dst, "w") as dst:
            dst.write("".join("0.000000\n" for _ in range(nrows)))
        print(f"[ethan] staged {tag} imag: zeros x{nrows} -> {imag_dst}")
    print(f"[ethan] staged {tag} real: {real_src} ({nrows} rows) -> {real_dst}")


def stage_channel(real_src):
    """Install the chosen real-taps file as channel_real.txt. If a sibling
    '<name>_imag.txt' exists, stage it as channel_imag.txt (complex channel)."""
    imag_src = real_src[:-len("_real.txt")] + "_imag.txt" if real_src.endswith("_real.txt") else None
    _stage_pair(real_src, CHANNEL_REAL, CHANNEL_IMAG, "channel", imag_src)


def stage_jammer(real_src):
    """Install the jammer real-taps file as jammer_real.txt (+ zero imag)."""
    _stage_pair(real_src, JAMMER_REAL, JAMMER_IMAG, "jammer")


def clear_jammer():
    """Zero out jammer_real.txt so a stale jammer trace can't leak into a
    no-jammer run (the UE still opens the file; all-zero taps => no output)."""
    for p in (JAMMER_REAL, JAMMER_IMAG):
        with open(p, "w") as f:
            f.write("0.000000\n")
    print("[ethan] jammer disabled (jammer_real.txt zeroed)")


parser = argparse.ArgumentParser(
    description="raj_exp TTI experiment: raj_chan CIR + Python-set noise power")
parser.add_argument("ue", type=int, nargs="?", help="number of UEs")
parser.add_argument("tap", type=int, nargs="?", help="number of channel taps (--TAP)")
parser.add_argument("--channel", default="channel_gt_los.txt",
                    help="real-taps channel file; bare name is looked up in "
                         "channel/raj_chan/ then channel/ "
                         "(default: channel_gt_los.txt)")
parser.add_argument("--snr", type=float, default=30.0,
                    help="SNR in dB assigned to a unit tap (default: 30)")
parser.add_argument("--out", default=None,
                    help="output directory for this run's snr.txt/tti.txt/plots "
                         "(default: ./plot/ue<#UE>_<#tap>/)")
parser.add_argument("--duration", type=int, default=60,
                    help="traffic-window length in seconds; longer plays more of "
                         "the channel trace (NLOS needs ~250 s for its full "
                         "trajectory) (default: 60)")
parser.add_argument("--ul-rate", default="8M",
                    help="UL iperf offered rate, e.g. 8M; 'sat' => TCP saturates "
                         "(no -b) to find the max UL throughput")
parser.add_argument("--dl-rate", default="8M",
                    help="DL iperf offered rate, e.g. 8M; 'sat' => UDP flood "
                         "(-b 1000M) to find the max DL throughput")
parser.add_argument("--list", action="store_true",
                    help="list available ethan_chan channel files and exit")
# --- Ethan additions: jammer + MIMO ---
parser.add_argument("--jam", action="store_true",
                    help="enable the jammer (--JAM 1 on the UE) and stage the "
                         "jammer tap file given by --jammer")
parser.add_argument("--jammer", default="jammer_real.txt",
                    help="jammer real-taps file (looked up in ethan_chan/ then "
                         "channel/); staged to channel/jammer_real.txt")
parser.add_argument("--jgain", type=float, default=200.0,
                    help="jammer linear output gain (--JGAIN); bigger = stronger "
                         "interference (default 200)")
parser.add_argument("--jtap", type=int, default=1,
                    help="number of jammer taps per slot (--JTAP, default 1)")
parser.add_argument("--gnb-conf", default=None,
                    help="absolute path to the gNB .conf; default is the SISO "
                         "106PRB conf. Pass the 2x2 conf for the MIMO scenario.")
parser.add_argument("--capture-channel", action="store_true",
                    help="during the traffic window, capture the MIMO-RIC UL "
                         "channel export from the edgeric container and save it "
                         "to <out>/chan_cap.txt")
parser.add_argument("--ue-ant", type=int, default=1,
                    help="UE antenna count: adds --ue-nb-ant-rx/tx to the UE "
                         "command. MUST match the gNB RU nb_tx/nb_rx (2 for the "
                         "2x2 MIMO conf), or the rfsim sample framing desyncs.")
args = parser.parse_args()

if args.list:
    print("available channels in", RAJ_CHAN_DIR)
    for p in sorted(glob.glob(os.path.join(RAJ_CHAN_DIR, "*.txt"))):
        print("  ", os.path.basename(p))
    sys.exit(0)

if args.ue is None or args.tap is None:
    parser.error("ue and tap are required (unless --list)")

# gNB conf: default SISO 106PRB; override (e.g. the 2x2 MIMO conf) via --gnb-conf.
if args.gnb_conf:
    GNB_CONF = os.path.abspath(args.gnb_conf)
print(f"[ethan] gNB conf: {GNB_CONF}")

start_ue = args.ue
end_ue = args.ue

start_tap = args.tap
end_tap = args.tap

CHANNEL_FILE = resolve_channel(args.channel)
JAMMER_FILE = resolve_channel(args.jammer) if args.jam else None

# iperf offered-rate flags. 'sat' saturates the link to find the max throughput:
#   UL is TCP  -> omit -b so congestion control climbs to capacity
#   DL is UDP  -> flood with a huge -b so the link (not iperf) is the bottleneck
_SAT = {"0", "sat", "max", "saturate"}
UL_B = "" if args.ul_rate.lower() in _SAT else f"-b {args.ul_rate}"
DL_B = "-b 1000M" if args.dl_rate.lower() in _SAT else f"-b {args.dl_rate}"


def out_dir(kk, ktap):
    """Per-run output dir. --out overrides the default ./plot/ue<#>_<#>/;
    with a sweep + --out, ue/tap subdirs are appended to avoid collisions."""
    if args.out:
        n_combos = len(range(start_ue, end_ue + 1, 3)) * len(range(start_tap, end_tap + 1, 4))
        return args.out if n_combos == 1 else os.path.join(args.out, f"ue{kk}_{ktap}")
    return f"./plot/ue{kk}_{ktap}"

# ---------------------------------------------------------------------------
# Option 1: set rfsim channel noise power from Python ONLY (no conf edits).
#
# OAI applies, per sample (radio/rfsimulator/apply_channelmod.c):
#     pathLossLinear   = 10^(ploss_dB/20)              # amplitude gain
#     noise_per_sample = 10^(noise_power_dB/10) * 256
#     out = rx*pathLossLinear + noise_per_sample*gauss()   # gauss on I and Q
#
# For a single real tap h the received SNR works out to:
#     SNR_dB(h) = 20*log10(h) + ploss_dB - 2*noise_power_dB - 3.01
# so to anchor a reference tap to a target SNR:
#     noise_power_dB = (20*log10(h_ref) + ploss_dB - 3.01 - SNR_target) / 2
#
# We override the conf value on the UE command line via the config-lib path
#   channelmod.modellist_rfsimu_1.[0].noise_power_dB   ([0]=DL, [1]=UL)
# confirmed by config_userapi.c:167 -> sprintf(cfgpath, "%s.[%i]", ...).
#
# Signal power scales as h^2, so SNR follows 20*log10(h): a 30 dB anchor at
# tap=1 puts tap=0.1 at 30 - 20 = 10 dB, tap=0.01 at -10 dB, etc.
# ---------------------------------------------------------------------------
TARGET_SNR_DB = args.snr    # SNR assigned to the reference tap (default 30 dB,
                            # so tap=1 -> 30 dB, tap=0.1 -> 10 dB, tap=0.01 -> -10)
REF_TAP = 1.0               # channel tap value that maps to TARGET_SNR_DB
PLOSS_DB = 15.0             # ploss_dB in nrue.uicc.conf (both DL and UL)
QUAD_OFFSET_DB = 3.01       # 2 noise quadratures vs complex signal power
MODELLIST = "modellist_rfsimu_1"   # channelmod.modellist in nrue.uicc.conf


def snr_to_noise_power_db(target_snr_db, tap_ref=REF_TAP, ploss_dB=PLOSS_DB,
                          quad_offset_dB=QUAD_OFFSET_DB):
    """Noise power (dB) that makes tap `tap_ref` sit at `target_snr_db`."""
    return (20.0 * np.log10(tap_ref) + ploss_dB - quad_offset_dB
            - target_snr_db) / 2.0


# DL = model index [0] (rfsimu_channel_enB0, UE-side).
# UL = model index [1] (rfsimu_channel_ue0, gNB-side); same magnitude as DL.
NOISE_DL_DB = snr_to_noise_power_db(TARGET_SNR_DB)
NOISE_UL_DB = NOISE_DL_DB
NOISE_OVERRIDE = (f"--channelmod.{MODELLIST}.[0].noise_power_dB {NOISE_DL_DB:.4f} "
                  f"--channelmod.{MODELLIST}.[1].noise_power_dB {NOISE_UL_DB:.4f}")
print(f"[ethan] tap {REF_TAP} -> {TARGET_SNR_DB} dB  =>  "
      f"noise_power_dB DL={NOISE_DL_DB:.4f} UL={NOISE_UL_DB:.4f}  "
      f"(ploss={PLOSS_DB} dB)")
print(f"[ethan] channel noise override: {NOISE_OVERRIDE}")

# --- jammer command-line flags (UE side; the UE applies the channel/jammer) ---
# Parsed in common/config/config_load_configmodule.c; applied in
# radio/rfsimulator/apply_channelmod.c on both the DL (rxAddInput) and UL
# (txAddInput) paths. --JAM 0 (or no file) leaves the jammer completely off.
JAM_OVERRIDE = (f"--JAM 1 --JGAIN {args.jgain:.4f} --JTAP {args.jtap}"
                if args.jam else "")
if args.jam:
    print(f"[ethan] JAMMER ON: {JAM_OVERRIDE}  (jammer file: {args.jammer})")

# UE antenna count (must match the gNB RU nb_tx/nb_rx or the rfsim socket desyncs).
UE_ANT_OVERRIDE = (f"--ue-nb-ant-rx {args.ue_ant} --ue-nb-ant-tx {args.ue_ant}"
                   if args.ue_ant > 1 else "")
if args.ue_ant > 1:
    print(f"[ethan] UE antennas: {UE_ANT_OVERRIDE}")

start=f'''
services:
    tt-gnb:
        image: tt-gnb:v2
        container_name: tt-gnb
        privileged: true
        cpuset: "5,6,7,8"   # pin gNB to dedicated Cortex-X925 performance cores (3900 MHz)
        cap_drop:
            - ALL
        cap_add:
            - NET_ADMIN  # for interface bringup
            - NET_RAW    # for ping
        volumes:
            - {TINYTWIN_OAI}:/opt/tt-ran/tt:rw
            - {GNB_CONF}:/opt/tt-ran/etc/gnb.conf
            # bind the real/imag tap files the rfsim C actually opens
            # (channel/ is the tinytwin-oai -> Tiny_Twin symlink); these are
            # (re)written each run by stage_channel() from the chosen raj_chan file
            - {CHANNEL_REAL}:/opt/tt-ran/tt/channel/channel_real.txt
            - {CHANNEL_IMAG}:/opt/tt-ran/tt/channel/channel_imag.txt
            - {LOGS_GNB}:/opt/tt-ran/etc/logs
            - {RUN_SH}:/opt/tt-ran/run.sh
            - {STOP_SH}:/opt/tt-ran/stop.sh
            - {BUILD_SH}:/opt/tt-ran/build.sh
        # environment:
        #     USE_ADDITIONAL_OPTIONS: --rfsim -E --sa --rfsimulator.options chanmod -T 10 -O /opt/oai-gnb/etc/gnb.conf
        #     ASAN_OPTIONS: detect_leaks=0
        # entrypoint: >
        #     /bin/bash -c "pwd && cd tt/cmake_targets/ran_build/build/ &&
        #     ./nr-softmodem -O ../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.conf --rfsim -E --sa  --rfsimulator.options chanmod -TP 10 --TTI 1 --SNR 1
        #     exec /bin/bash"
        entrypoint: /bin/bash
        stdin_open: true  # docker run -i
        tty: true        # docker run -t
        # depends_on:
        #     - oai-ext-dn
        networks:
            public_net:
                ipv4_address: 192.168.70.140
        # healthcheck:
        #     test: /bin/bash -c "pgrep nr-softmodem"
        #     interval: 10s
        #     timeout: 5s
        #     retries: 5

'''
end='''
    edgeric:
        container_name: edgeric_v2_2
        # Build info
        image: edgeric/v2
        build:
            context: edgeric-v2
            dockerfile: ../Dockerfile-edgeric
            args:
                OS_VERSION: "24.04"
        # privileged mode is requred only for accessing usb devices
        privileged: true
        ports:
            - "7000:7000"
        # entrypoint: ["/home/EdgeRIC-A-real-time-RIC/srsran_entrypoint.sh"]
        # Extra capabilities always required
        cap_add:
            - SYS_NICE
            - CAP_SYS_PTRACE
        volumes:
            # - ${pwd}/radio_network:/home/EdgeRIC-A-real-time-RIC:rw
            - /tmp/.X11-unix:/tmp/.X11-unix:rw
            - /dev:/dev
            # - ${PWD:-.}:/home/EdgeRIC-A-real-time-RIC:rw
            - ./edgeric-v2:/home/EdgeRIC:rw
        # It creates a file/folder into /config_name inside the container
        # Its content would be the value of the file used to create the config
        # configs:
        #   - gnb_config.yml
        # Customize your desired network mode.
        # current netowrk configuration creastes a private netwoek with both containers attached
        # An alterantive would be `network: host"`. That would expose your host network into the container. It's the easiest to use if the 5gc is not in your PC
        networks:
            public_net:
                ipv4_address: 192.168.70.166
        # Start GNB container after 5gc is up and running
        # depends_on:
        #   gnb:
        #     condition: service_healthy


networks:
    public_net:
        driver: bridge
        external: true
        name:  oai-cn5g-public-net
'''
# edgeric-v2 build context + mount live in the original tti_experiments/ folder;
# point at them by absolute path (kept a plain string to preserve the ${PWD}
# comments above).
end = end.replace("context: edgeric-v2", f"context: {EDGERIC_DIR}")
end = end.replace("- ./edgeric-v2:/home/EdgeRIC:rw",
                  f"- {EDGERIC_DIR}:/home/EdgeRIC:rw")

flag=1

def read_starting(file_name):
    float_values = []
    with open(file_name, 'r') as f:
        for i, line in enumerate(f, start=1):
            # if (i-1) % 3 == 0:
            words = line.split()
            if len(words) >= 1:
                try:
                    float_value = float(words[0])  # Convert the 4th word to float
                    float_values.append(float_value)
                except ValueError:
                    print(f"Cannot convert '{words[0]}' to float on line {i}")
            else:
                print(f"Line {i} does not have 4 words")
    return float_values

def read_exec(file_name):
    float_values = []
    with open(file_name, 'r') as f:
        for i, line in enumerate(f, start=1):
            if i % 3 == 0:
                words = line.split()
                if len(words) >= 4:
                    try:
                        float_value = float(words[2])  # Convert the 4th word to float
                        float_values.append(float_value)
                    except ValueError:
                        print(f"Cannot convert '{words[3]}' to float on line {i}")
                else:
                    print(f"Line {i} does not have 4 words")
    return float_values    

def read_more(file_name):

    all_vals = {}

    ul_snr_values = []
    ul_mcs_values = []
    dl_mcs_values = []
    ul_tpt_values = []
    dl_tpt_values = []
    ul_cqi_values = []
    ul_ack_values = []
    dl_ack_values = []

    ul_snr_ttis = []
    ul_mcs_ttis = []
    dl_mcs_ttis = []
    ul_tpt_ttis = []
    dl_tpt_ttis = []
    ul_cqi_ttis = []
    ul_ack_ttis = []
    dl_ack_ttis = []

    all_metrics = [
    ul_snr_values, ul_mcs_values, dl_mcs_values, ul_tpt_values, dl_tpt_values,
    ul_cqi_values, ul_ack_values, dl_ack_values,
    ul_snr_ttis, ul_mcs_ttis, dl_mcs_ttis, ul_tpt_ttis, dl_tpt_ttis,
    ul_cqi_ttis, ul_ack_ttis, dl_ack_ttis
    ]

    previous_line = None

    with open(file_name, 'r') as f:
        for i, line in enumerate(f, start=1):
            # if (i-1) % 2 == 0:
            words = line.split()
            if len(words) >= 3:
                try:
                    float_value = float(words[2])  # Convert the last word to float
                    if words[0] == 'UL':
                        rnti = words[-1]
                        if rnti not in all_vals.keys():
                            # all_vals[rnti] = {name: [] for name in all_metrics}  # Initialize metric lists
                            all_vals[rnti] = {name: [] for name in [
                                'ul_snr_values', 'ul_mcs_values', 'dl_mcs_values', 'ul_tpt_values', 'dl_tpt_values',
                                'ul_cqi_values', 'ul_ack_values', 'dl_ack_values',
                                'ul_snr_ttis', 'ul_mcs_ttis', 'dl_mcs_ttis', 'ul_tpt_ttis', 'dl_tpt_ttis',
                                'ul_cqi_ttis', 'ul_ack_ttis', 'dl_ack_ttis'
                            ]}  # Initialize metric lists
                        
                        # while(1){    
                        #     rnti = float(words[4])
                        #     if rnti in all_vals.keys():
                        #         break
                        #     else:
                        #         all_vals[rnti] = {name: [] for name in all_metrics}    
                        # }

                        if words[1] == 'SNR:':
                            # refer to previous line
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['ul_snr_ttis'].append(tti)
                            all_vals[rnti]['ul_snr_values'].append(float_value)
                        elif words[1] == 'MCS:':
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['ul_mcs_ttis'].append(tti)
                            all_vals[rnti]['ul_mcs_values'].append(float_value)
                        elif words[1] == 'TPT:':
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['ul_tpt_ttis'].append(tti)
                            all_vals[rnti]['ul_tpt_values'].append(float_value)
                        elif words[1] == 'CQI:':
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['ul_cqi_ttis'].append(tti)
                            all_vals[rnti]['ul_cqi_values'].append(float_value)
                        elif words[1] == 'ACK:':
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['ul_ack_ttis'].append(tti)
                            all_vals[rnti]['ul_ack_values'].append(float_value)
                    elif words[0] == 'DL':
                        rnti = words[-1]
                        if rnti not in all_vals.keys():
                            all_vals[rnti] = {name: [] for name in [
                                'ul_snr_values', 'ul_mcs_values', 'dl_mcs_values', 'ul_tpt_values', 'dl_tpt_values',
                                'ul_cqi_values', 'ul_ack_values', 'dl_ack_values',
                                'ul_snr_ttis', 'ul_mcs_ttis', 'dl_mcs_ttis', 'ul_tpt_ttis', 'dl_tpt_ttis',
                                'ul_cqi_ttis', 'ul_ack_ttis', 'dl_ack_ttis'
                            ]}  # Initialize metric lists
                        if words[1] == 'MCS:':
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['dl_mcs_ttis'].append(tti)
                            all_vals[rnti]['dl_mcs_values'].append(float_value)
                        elif words[1] == 'TPT:':
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['dl_tpt_ttis'].append(tti)
                            all_vals[rnti]['dl_tpt_values'].append(float_value)
                        elif words[1] == 'ACK:':
                            if previous_line:
                                ttis = previous_line.split()
                                tti = int(ttis[2])
                                all_vals[rnti]['dl_ack_ttis'].append(tti)
                            all_vals[rnti]['dl_ack_values'].append(float_value)
                except ValueError:
                    print(f"Cannot convert '{words[-1]}' to float on line {i}")
            else:
                print(f"Line {i} does not have 1 word...")
            previous_line = line
    # return ul_snr_values, ul_snr_values, ul_cqi_values, ul_cqi_ttis, ul_mcs_values, ul_mcs_ttis, dl_mcs_values, dl_mcs_ttis, ul_tpt_values, ul_tpt_ttis, dl_tpt_values, dl_tpt_ttis, ul_ack_values, ul_ack_ttis, dl_ack_values, dl_ack_ttis
    return all_vals

def Convert(snr_ttis, snr_values):
    res_dct = {snr_ttis[i]: snr_values[i] for i in range(len(snr_ttis))}
    return res_dct

def create_docker(i, n_tap):
    template=f'''
    tt-nrue{i-160}:
        image: tt-nrue:v2
        container_name: tt-ue{i-160}
        privileged: true
        cpuset: "9,15,16,17,18,19"   # pin UE(s) to remaining X925 performance cores (disjoint from gNB)
        cap_drop:
            - ALL
        cap_add:
            - NET_ADMIN  # for interface bringup
            - NET_RAW    # for ping
        volumes:
            - {TINYTWIN_OAI}:/opt/tt-ran/tt:rw
            - {UE_CONF}:/opt/oai-nr-ue/etc/nr-ue.conf
            - {LOGS_UE}:/opt/oai-nr-ue/etc/logs
        entrypoint: >
            /bin/bash -c "ls && cd tt/cmake_targets/ran_build/build/ &&
            ./nr-uesoftmodem --uicc0.imsi 0010100000000{i-150} -C 3619200000 -r 106 --numerology 1 --ssb 516 -E --sa --rfsim --rfsimulator.options chanmod -O ../../../ci-scripts/conf_files/nrue.uicc.conf --TAP {n_tap} {NOISE_OVERRIDE} {JAM_OVERRIDE} {UE_ANT_OVERRIDE} --rfsimulator.serveraddr 192.168.70.140 &&
            exec /bin/bash"
        # entrypoint: /bin/bash
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
    return template

def autoUE():
    global flag
    flag=1
    for ktap in range(start_tap,end_tap+1,4):
        for kk in range(start_ue,end_ue+1,3):
            os.makedirs(out_dir(kk, ktap), exist_ok=True)
            # route the chosen CIR into channel_real.txt (+ zero imag)
            stage_channel(CHANNEL_FILE)
            # stage (or clear) the jammer tap file
            if args.jam:
                stage_jammer(JAMMER_FILE)
            else:
                clear_jammer()
            time.sleep(10)
            os.system(f"docker compose -f {OAI_CN_COMPOSE} up -d")
            os.system(f"docker compose -f {EDGERIC_PROM} up -d")
            os.system(f"docker compose -f {EDGERIC_GRAFANA} up -d")
            while flag == 0:
                time.sleep(2)
            

            file=""
            for i in range(161,161+kk):
                file=file+"\n"+create_docker(i,ktap)
            file = start+ file+ end
            print(file)

            
            with open("./docker-compose.yaml","+w") as f:
                f.write(file)
            os.system(f"docker compose up -d tt-gnb")
            time.sleep(10)
            os.system(f"docker compose up -d edgeric")
            print("set up ran")
            time.sleep(10)
            os.system(f"docker exec -d edgeric_v2_2 python3 ./muApp3/muApp3_monitor_grafana.py ")
            os.system(f"docker exec tt-gnb chmod +x run.sh build.sh")
            os.system(f"docker exec -d tt-gnb ./run.sh ")
            #os.system(f"docker exec -d tt-gnb  cd /opt/tt-ran/tt/cmake_targets/ran_build/build/ && ./nr-softmodem -O /opt/tt-ran/tt/targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.conf --rfsim -E --sa  --rfsimulator.options chanmod --TAP 1 --TTI 1 --SNR 1 --MCS 1 --CQI 1 --TPT 1")
            for i in range(161,161+kk):
                os.system(f"docker compose up -d tt-nrue{i-160}")
                time.sleep(min((i-160)*12,15))
                #os.system(f"docker exec -it tt-ue{i} ifconfig oaitun_ue1| grep 'inet ' ")

            # for jk in range(161,161+kk): 
            #     os.system(f"""docker exec -d oai-ext-dn bash -c "ping 10.0.0.{jk-159} -c 50 > ./dl_ue{jk-160}.txt" """)
            #     os.system(f"""docker exec -d tt-ue{jk-160} bash -c "ping 10.0.0.1 -c 50 > ./ul_ue{jk-160}.txt" """)
            # time.sleep(1)
            # os.system(f"""top -bn6 > ./plot/ue{kk}_{ktap}/ping_cpumem.txt """)
            # time.sleep(6)


            # for jk in range(161,161+kk): 
            #     os.system(f"docker cp oai-ext-dn:/tmp/dl_ue{jk-160}.txt ./plot/ue{kk}_{ktap}/dl_ue{jk-160}.txt")
            #     os.system(f"docker cp tt-ue{jk-160}:/opt/tt-ran/ul_ue{jk-160}.txt ./plot/ue{kk}_{ktap}/ul_ue{jk-160}.txt")

            for ik in range(161,161+kk):
                ue = ik-160
                # DL receiver (UDP, at the UE) and UL receiver (TCP, at oai-ext-dn).
                # stdbuf -oL => per-line flush so the .txt fills live; -fk => kbps.
                os.system(f'docker exec -d tt-ue{ue} bash -c '
                          f'"stdbuf -oL iperf -s -u -i 1 -fk -B 10.0.0.{ik-159} > /tmp/iperf_dl.txt 2>&1"')
                os.system(f'docker exec -d oai-ext-dn bash -c '
                          f'"stdbuf -oL iperf -s -i 1 -fk -B 192.168.70.135 -p 52{ik-149} > /tmp/iperf_ul_ue{ue}.txt 2>&1"')
            time.sleep(2)
            for jk in range(161,161+kk): 
                os.system(f"docker exec -d oai-ext-dn iperf -u -t 86400 -i 1 -fk -B 192.168.70.135 {DL_B} -c 10.0.0.{jk-159}")
                os.system(f"docker exec -d tt-ue{jk-160} iperf -t 86400 -i 1 -fk -c 192.168.70.135 {UL_B} -B 10.0.0.{jk-159} -p 52{ik-149}")
            time.sleep(5)
            os.system(f"""top -bn6 > {out_dir(kk, ktap)}/iperf_cpumem.txt """)
            # MIMO-RIC: capture the exported per-antenna UL channel mid-traffic.
            if args.capture_channel:
                print("[ethan] capturing MIMO-RIC UL channel export ...")
                os.system("docker exec -d edgeric_v2_2 python3 "
                          "/home/EdgeRIC/muApp_capture_channel.py --n 400 "
                          "--out /tmp/ethan_chan_cap.txt --timeout 60")
            #test
            time.sleep(args.duration) # traffic window (--duration)
            print("kill gnb")
            os.system(f"docker exec tt-gnb chmod +x stop.sh ")
            os.system(f"docker exec -d tt-gnb ./stop.sh ")
            time.sleep(5)

            # pull iperf goodput-vs-time logs out before the containers are removed
            os.makedirs(out_dir(kk, ktap), exist_ok=True)
            for ik in range(161,161+kk):
                ue = ik-160
                os.system(f"docker cp tt-ue{ue}:/tmp/iperf_dl.txt {out_dir(kk, ktap)}/iperf_dl_ue{ue}.txt")
                os.system(f"docker cp oai-ext-dn:/tmp/iperf_ul_ue{ue}.txt {out_dir(kk, ktap)}/iperf_ul_ue{ue}.txt")

            # MIMO-RIC: pull the captured channel out before teardown
            if args.capture_channel:
                os.system(f"docker cp edgeric_v2_2:/tmp/ethan_chan_cap.txt "
                          f"{out_dir(kk, ktap)}/chan_cap.txt")

            os.system(f"docker compose down")
            os.system(f"docker compose -f {EDGERIC_GRAFANA} down")
            os.system(f"docker compose -f {EDGERIC_PROM} down")
            os.system(f"docker compose -f {OAI_CN_COMPOSE} down")


            flag=0
        
    

def processDATA():
    global flag
    time.sleep(2)

    # ul_snr_values_a = []
    # ul_mcs_values_a = []
    # dl_mcs_values_a = []
    # ul_tpt_values_a = []
    # dl_tpt_values_a = []
    # ul_cqi_values_a = []

    # ul_snr_ttis_a = []
    # ul_mcs_ttis_a = []
    # dl_mcs_ttis_a = []
    # ul_tpt_ttis_a = []
    # dl_tpt_ttis_a = []
    # ul_cqi_ttis_a = []

    for ktap in range(start_tap,end_tap+1,4):

        for kk in range(start_ue,end_ue+1,3):

            flag=1
            while flag == 1:
                time.sleep(2)

            # # read from TTI file
            # ### @ALI: change this to whatever file you would like to point
            # vals_tti = read_starting("../../../logs/tti.txt")
            # diff_tti = [vals_tti[i] - vals_tti[i - 1] for i in range(1, len(vals_tti))]
            # diff_tti = [diff_tti[i]/1000000000 for i in range(1, len(diff_tti))]

            # tti = np.array(diff_tti)
            # tti = [max(0, tti_val) for tti_val in tti]
            # # remove all zero values
            # tti = [tti_val for tti_val in tti if tti_val > 0]
            # mean_tti = np.mean(tti)

            # size = 100000

            # # CCDFs of various TTI times across 100,000 instances
            # tti = tti[:size]
            # sorted_tti = np.sort(tti)
            # p1 = np.linspace(0, 1, len(tti))

            # # 8 subplots
            # fig, axes = plt.subplots(2, 4, figsize=(20, 10), dpi=150)

            # # plot CCDF
            # ax1 = axes[0, 0]
            # ax1.plot(sorted_tti, 1 - p1, label='1 Tap', linewidth=2)

            # # add a red vertical line at x=0.001 --> 3GPP TTI timing
            # ax1.axvline(x=0.001, color='red', linestyle='--', linewidth=2)

            # ax1.set_xlabel('TTI Times (s)', fontsize=14)
            # ax1.set_ylabel('$CCDF$', fontsize=14)
            # ax1.set_title('TTI Variation With Number Of Taps (CCDF)', fontsize=16)

            # # Set x-axis limit
            # ax1.set_xlim(0, 0.01)

            # # Customize the grid
            # ax1.grid(True, which='both', linestyle='--', linewidth=0.7, color='gray')

            # # Add minor ticks for a finer grid
            # ax1.minorticks_on()
            # ax1.grid(which='minor', linestyle=':', linewidth=0.5, color='lightgray')

            # ax1.legend()  # Add legend

            # plt.savefig("tti_variation_ccdf.png", format="png", dpi=150)

            # ### read MAC metrics
            # # define all_vals

            # # ul_snr_values = []
            # # ul_mcs_values = []
            # # dl_mcs_values = []
            # # ul_tpt_values = []
            # # dl_tpt_values = []
            # # ul_cqi_values = []

            # # ul_snr_ttis = []
            # # ul_mcs_ttis = []
            # # dl_mcs_ttis = []
            # # ul_tpt_ttis = []
            # # dl_tpt_ttis = []
            # # ul_cqi_ttis = []

            # all_vals = read_more("../../../logs/snr.txt")

            # # ul_snr_values_a.append(ul_snr_values)
            # # ul_mcs_values_a.append(ul_mcs_values)
            # # dl_mcs_values_a.append(dl_mcs_values)
            # # ul_tpt_values_a.append(ul_tpt_values)
            # # dl_tpt_values_a.append(dl_tpt_values)
            # # ul_cqi_values_a.append(ul_cqi_values)

            # # ul_snr_ttis_a.append(ul_snr_ttis)
            # # ul_mcs_ttis_a.append(ul_mcs_ttis)
            # # dl_mcs_ttis_a.append(dl_mcs_ttis)
            # # ul_tpt_ttis_a.append(ul_tpt_ttis)
            # # dl_tpt_ttis_a.append(dl_tpt_ttis)
            # # ul_cqi_ttis_a.append(ul_cqi_ttis)

            # # Plot on the second subplot (0, 1)
            # ax2 = axes[0, 1]

            # for rnti in all_vals.keys():
            #     ax2.plot(all_vals[rnti]['ul_mcs_ttis'], all_vals[rnti]['ul_mcs_values'], label=f'{rnti} UL MCS')
            #     ax2.plot(all_vals[rnti]['dl_mcs_ttis'], all_vals[rnti]['dl_mcs_values'], label=f'{rnti} DL MCS')

            # # Adding labels and title
            # ax2.set_xlabel('(TTI) Index')
            # ax2.set_ylabel('MCS')
            # ax2.set_title('MCS Variation')

            # # Adding legend
            # ax2.legend()

            # # Displaying the plot
            # # plt.tight_layout()
            # # plt.show()

            # cumulative_dl_tpt_values = []
            # cumulative_ul_tpt_values = []

            # # # Plot on the third subplot (0, 2)
            # # ax3 = axes[0, 2]
            
            # # # ul_cumulative = []
            # # # dl_cumulative = []
            # # # for rnti in all_vals.keys():
            # # #     # Assuming tpt_ttis and tpt_values are already defined
            # # #     ul_tpt = Convert(all_vals[rnti]['ul_tpt_ttis'], all_vals[rnti]['ul_tpt_values'])
            # # #     dl_tpt = Convert(all_vals[rnti]['dl_tpt_ttis'], all_vals[rnti]['dl_tpt_values'])

            # # WINDOW_SIZE = 1000
            # # cumulative_tpt = 0

            # # # Iterate through the keys of tpt
            # # for key in ul_tpt:
            # #     for rnti in 
            # #     cumulative_tpt += ul_tpt[key]
            # #     if key % WINDOW_SIZE == 0:
            # #         cumulative_ul_tpt_values.append(cumulative_tpt)
            # #         cumulative_tpt = 0

            # #     cumulative_tpt = 0

            # #     # Iterate through the keys of tpt
            # #     for key in dl_tpt:
            # #         cumulative_tpt += dl_tpt[key]
            # #         if key % WINDOW_SIZE == 0:
            # #             cumulative_dl_tpt_values.append(cumulative_tpt)
            # #             cumulative_tpt = 0   




            # # # Plotting the cumulative TPT values
            # # ax3.plot(ul, label='UL TPT')
            # # ax3.plot(dl, label='DL TPT')

            # # # Adding labels and title
            # # ax3.set_xlabel('(TTI) Index')
            # # ax3.set_ylabel('TPT')
            # # ax3.set_title('UL and DL Traffic at capacity')

            # # # Adding legend
            # # ax3.legend()

            # # plotting on the fourth plot
            # ax4 = axes[0, 3]

            # for rnti in all_vals.keys():
            #     ax4.plot(all_vals[rnti]['ul_tpt_ttis'], all_vals[rnti]['ul_tpt_values'], label=f'{rnti} UL TPT')
            #     ax4.plot(all_vals[rnti]['dl_tpt_ttis'], all_vals[rnti]['dl_tpt_values'], label=f'{rnti} DL TPT')

            # # Adding labels and title
            # ax4.set_xlabel('(TTI) Index')
            # ax4.set_ylabel('Bytes')
            # ax4.set_title('Per UE Throughput Variation')

            # # Adding legend
            # ax4.legend()

            # ## plot on the 5th subplot
            # ax5 = axes[1, 0]

            # # Plotting the cumulative CQI values
            # for rnti in all_vals.keys():
            #     ax5.plot(all_vals[rnti]['ul_cqi_ttis'], all_vals[rnti]['ul_cqi_values'], label=f'{rnti} UL CQI')

            # # Adding labels and title
            # ax5.set_xlabel('(TTI) Index')
            # ax5.set_ylabel('CQI')
            # ax5.set_title('UL CQI Variation')

            # # Adding legend
            # ax5.legend()

            # # Displaying the plot
            # # plt.tight_layout()
            # # plt.show()

            # ## plot 6th subplot
            # ax6 = axes[1, 1]

            # # Plotting the cumulative SNR values
            # for rnti in all_vals.keys():
            #     ax6.plot(all_vals[rnti]['ul_snr_ttis'], all_vals[rnti]['ul_snr_values'], label=f'{rnti} UL SNR')
            #     # ax5.plot(ul_snr_values, label='UL SNR')

            # # Adding labels and title
            # ax6.set_xlabel('(TTI) Index')
            # ax6.set_ylabel('SNR')
            # ax6.set_title('UL SNR Variation')

            # # Adding legend
            # ax6.legend()

            # # Displaying the plot
            # # plt.tight_layout()
            # # plt.show()

            # ## plot 6th subplot
            # ax7 = axes[1, 2]

            # # Plotting the ACK status
            # for rnti in all_vals.keys():
            #     ax7.plot(all_vals[rnti]['ul_ack_ttis'], all_vals[rnti]['ul_ack_values'], label=f'{rnti} UL ACKs')
            #     ax7.plot(all_vals[rnti]['dl_ack_ttis'], all_vals[rnti]['dl_ack_values'], label=f'{rnti} DL ACKs')

            # # Adding labels and title
            # ax7.set_xlabel('(TTI) Index')
            # ax7.set_ylabel('ACKs')
            # ax7.set_title('ACK Status')

            # # Adding legend
            # ax7.legend()

            # # Displaying the plot
            # # plt.tight_layout()
            # # plt.show()

            # fig.savefig("plotting.png", format="png", dpi=150)

            #os.system(f"mkdir ./plot/ue{kk}_{sys.argv[2]}")
            
            os.makedirs(out_dir(kk, ktap), exist_ok=True)
            os.system(f"cp {LOGS_DIR}/tti.txt {out_dir(kk, ktap)}/tti.txt")
            os.system(f"cp {LOGS_DIR}/snr.txt {out_dir(kk, ktap)}/snr.txt")
            # os.system(f"cp plotting.png ./plot/ue{kk}_{ktap}/plotting.png")

if __name__ == "__main__":
    

    thread_audoUE = threading.Thread(target=autoUE)

    thread_processDATA = threading.Thread(target=processDATA)

    thread_audoUE.start()
    thread_processDATA.start()

    thread_audoUE.join()
    thread_processDATA.join()

    print("Done!")