#!/bin/bash
cd /opt/tt-ran/tt/cmake_targets/ran_build/build/
./nr-softmodem -O /opt/tt-ran/etc/gnb.conf --rfsim -E --sa \
  --rfsimulator.options chanmod --TAP 1 \
  --channelmod.modellist_rfsimu_1.[1].noise_power_dB -40.0000 \
  --channelmod.modellist_rfsimu_1.[1].ploss_dB 0 \
  --SNR 1 --MCS 1 --CQI 1 --TPT 1 >gnb.log &
echo $! > pid.txt
