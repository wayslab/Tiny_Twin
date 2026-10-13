# channel files — DL vs UL (read this first)

The rfsim channel is applied **on the receiver**, so *which file* is used depends on
*who is receiving*, not on a filename convention. The two directions use **separate**
files with **different formats**.

| direction | file(s) | opened by | applied on | rows per TTI |
|-----------|---------|-----------|------------|--------------|
| **Downlink** | `channel_real.txt` + `channel_imag.txt` | **UE** (`nr-uesoftmodem.c`) | UE receive | **1** (UE rx = 1 antenna) |
| **Uplink (MIMO)** | `channel_mimo_real.txt` + `channel_mimo_imag.txt` | **gNB** (`nr-softmodem.c`, branch `dev/mimo`) | gNB receive | **nb_rx** (one row per gNB rx antenna, e.g. 4) |

Key points:

- **They are NOT interchangeable.** The DL file has 1 antenna row per TTI; the UL MIMO
  file has `nb_rx` rows per TTI (one complex tap-line per gNB rx antenna). Feeding a
  4-row MIMO file to the DL path (or vice-versa) misreads.
- **Each line = `nb_tx*taplen` space-separated taps.** `*_real.txt` holds the real part,
  `*_imag.txt` the imag part (same shape). A flat channel is a single `1.0` tap and zero imag.
- **UL MIMO only activates** when the gNB is launched with
  `--rfsimulator.options chanmod --TAP <D+1>`. Without `--TAP` the gNB ignores
  `channel_mimo_*.txt` and falls back to the legacy SISO memcpy fan-out (backward-compatible).
- **Both are INPUT channels.** The gNB's *measured* (extracted) UL channel comes out
  separately in the experiment's `plot/.../chan_cap.txt`.

## Who writes what (generators)

| channel | generator | output |
|---------|-----------|--------|
| flat AoA (this dir) | `ethan_chan/generate_aoa.py` | `channel_aoa_flat_real.txt` (staged to `channel_real.txt`) |
| UL MIMO (per-antenna) | `../mimo_chan/generate_mimo.py` | `channel_mimo_real/imag.txt` (+ `input_truth.json`) |
| raj LOS/NLOS, Path A/B | `../raj_chan/20261009/generate_20261009.py` | `../raj_chan/*.txt` |

See `sims/siso/tti_experiments_rpi/202610_Ethanmimo/README.md` for the uplink-MIMO
experiment that exercises the UL file end-to-end.
