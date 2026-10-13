# Wall-clock time per run (at --duration 280, 60 s warmup)

Measured from the 45 dB UDP run scenario timestamps:

| unit                        | wall time |
|-----------------------------|-----------|
| per scenario                | ~6 min 15 s (375 s) = 280 s traffic + ~95 s setup/teardown |
| per anchor (6 scenarios)    | ~38 min   |
| 4 anchors (45/35/30/55)     | ~2.5 h    |

## Important: TTI index is NOT proportional to wall-clock
rfsim advances 1 channel row per TTI, but the TTI *processing rate* depends on
load:
- heavy UL traffic (goodput flowing)  -> ~0.6x real-time (~1077 TTI/s), lots of PUSCH decode
- idle UL (after a goodput collapse)  -> faster, closer to real-time (less to decode)

So two scenarios that ran the same 280 s wall-clock can end at very different
final TTI indices: one that went idle early accumulates more (fast) TTIs and
reaches a HIGHER TTI index than one that stayed busy the whole time. Compare
scenarios by wall-time / seconds, not by raw TTI extent. (See the MCS-vs-
throughput note in the chat / COVERAGE_NOTES.md.)
