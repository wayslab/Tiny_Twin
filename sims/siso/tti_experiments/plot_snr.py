#!/usr/bin/env python3
"""
Parse an snr.txt and plot UL metrics over TTI.

Example: Run experiment then plot
python3 tti_multiue_experiments_demo1.py 1 2
  python3 plot_snr.py plot/ue1_2/snr.txt plot/ue1_2/snr_plot.png

   python3 plot_snr.py plot/ue1_0/snr.txt plot/ue1_0/snr_plot.png
"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def read_more(file_name):
    all_vals = {}
    keys = ['ul_tpt', 'ul_mcs', 'ul_bler', 'ul_pusch_snr', 'ul_cqi', 'ul_ack']
    tags = {
        'TPT:': 'ul_tpt',
        'MCS:': 'ul_mcs',
        'BLER:': 'ul_bler',
        'PUSCH_SNR:': 'ul_pusch_snr',
        'CQI:': 'ul_cqi',
        'ACK:': 'ul_ack',
    }
    current_tti = None
    with open(file_name) as f:
        for line in f:
            w = line.split()
            if len(w) >= 3 and w[0] == 'TTI':
                try:
                    current_tti = int(w[2])
                except ValueError:
                    pass
                continue

            if len(w) >= 3 and w[0] == 'UL':
                try:
                    val = float(w[2])
                except ValueError:
                    continue
                rnti = w[-1]
                d = all_vals.setdefault(rnti, {k + '_ttis': [] for k in keys} | {k + '_vals': [] for k in keys})
                tag = tags.get(w[1])
                if tag and current_tti is not None:
                    d[tag + '_ttis'].append(current_tti)
                    d[tag + '_vals'].append(val)
    return all_vals

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "plot/ue1_3/snr.txt"
    out = sys.argv[2] if len(sys.argv) > 2 else "plot/ue1_3/snr_plot.png"
    data = read_more(src)
    panels = [('ul_tpt', 'UL Throughput (kbps)'), ('ul_mcs', 'UL MCS'),
              ('ul_bler', 'UL BLER'), ('ul_pusch_snr', 'UL PUSCH SNR (dB)'),
              ('ul_cqi', 'UL CQI'), ('ul_ack', 'UL ACK')]
    fig, axes = plt.subplots(len(panels), 1, figsize=(14, 16), sharex=True)
    for ax, (key, label) in zip(axes, panels):
        for rnti, d in data.items():
            t, v = d[key + '_ttis'], d[key + '_vals']
            if t:
                ax.plot(t, v, lw=0.5, label=f'RNTI {rnti} (n={len(v)})')
        ax.set_ylabel(label); ax.grid(True, ls='--', lw=0.4, alpha=0.6); ax.legend(loc='upper right', fontsize=8)
    axes[-1].set_xlabel('TTI Index')
    fig.suptitle(f'UL metrics over TTI — {src}', fontsize=14)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"saved {out}")
    for rnti, d in data.items():
        for key, label in panels:
            v = d[key + '_vals']
            if v:
                print(f"  {rnti} {key}: n={len(v)} min={min(v):.2f} max={max(v):.2f} mean={sum(v)/len(v):.2f}")

if __name__ == "__main__":
    main()
