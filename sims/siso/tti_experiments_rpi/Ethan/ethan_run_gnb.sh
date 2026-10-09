#!/bin/bash
# Ethan gNB launcher. Unlike the shared run.sh (which hardcodes the SISO conf),
# this runs whatever conf is bind-mounted at /opt/tt-ran/etc/gnb.conf, so the
# driver selects SISO vs the 2x2 MIMO conf just by changing GNB_CONF.
cd /opt/tt-ran/tt/cmake_targets/ran_build/build/
./nr-softmodem -O /opt/tt-ran/etc/gnb.conf --rfsim -E --sa --SNR 1 --MCS 1 --CQI 1 --TPT 1 >gnb.log &
pid=$!
echo $pid > pid.txt
