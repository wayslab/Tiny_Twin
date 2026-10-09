#!/usr/bin/env python3
"""cellsense_position.py -- offline per-TTI position estimator (one trial).

Runs the bibhor/ISAC sensing pipeline (cellsense_sensing.py) for ONE trial
(one trajectory, one Monte-Carlo realization) and records, for every frame
(= one sensing TTI, Te = 0.08 s apart), the estimated (x,y) of the moving
object and the residual error vs ground truth. Output: <out>/residual.txt and
<out>/trial.npz, then run plot_residual.py to draw residual-vs-time.

  python3 cellsense_position.py --scenario Open_scenario --traj Trajectory_1 \
          --seed 10 --out ./plot/trial1/

The channel defaults to the ISAC ray-tracing dataset (4-antenna ULA + a moving
reflector -- what the AoA/ToA estimator needs). A live-twin PUSCH DMRS capture
(muApp_capture_channel.py) can be inspected with --cap, but it is a 2-antenna,
low-pass-filtered, static channel and will not localize -- it only demonstrates
the H[rx][sc] extraction interface (see README).

Per-frame tracking state machine ported verbatim from
ISAC/Python_scripts/Simulation/run_open_scenario.py:441-721.
"""
import os
import sys
import argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellsense_sensing as cs

# ISAC datasets are copied next to this script under ./Datasets/ (see README);
# fall back to the ISAC repo if not copied.
DATA_LOCAL = os.path.join(HERE, "Datasets")
DATA_ISAC = "/home/ways_lab/repos/others/ISAC/Python_scripts/Simulation/Datasets"

Te = 0.08    # frame (sensing TTI) spacing in seconds (ISAC trajectory timebase)


def data_root():
    if os.path.isdir(DATA_LOCAL):
        return DATA_LOCAL
    return DATA_ISAC


def run_trial(scenario, trajectory, seed):
    """One trial -> per-frame arrays (residual[K], x_est[K], y_est[K], visible[K])."""
    rng = np.random.default_rng(seed)
    chan_params, positions, obj_params = cs.load_dataset(scenario, trajectory, root=data_root())
    visibility = obj_params[:, 2].astype(int)

    K = 100
    H = cs.build_channel_cube(chan_params, rng)          # [K, N_ant, N_ifft]

    static_point = int(np.min([i for i in range(len(visibility)) if visibility[i] == 1]))

    # per-frame outputs
    residual = np.full(K, np.nan)
    x_est_out = np.full(K, np.nan)
    y_est_out = np.full(K, np.nan)

    # tracking state (verbatim init:443-446)
    track_params = np.zeros([static_point, 5, 2])
    track_params_repo = np.zeros([5, 2])
    track_idx = 0
    n_paths = np.zeros(5)
    l_idx = 0
    static_params = None

    for iter0 in range(0, K):
        flag = 1
        parameters = cs.estimate_frame(H[iter0])

        t_est = np.real(parameters[2, :]) / (cs.osf * cs.f_s) * 1e9
        theta_est = 180 / np.pi * np.real(parameters[0, :])
        alpha_est = parameters[3, :]
        t_est, theta_est, alpha_est, l_ue = cs.find_los(t_est, theta_est, alpha_est, aoa_ue=0)

        if len(t_est) == 0:
            flag = 0

        if flag == 1 and track_idx == 0:
            t_est = t_est - t_est[l_ue]
            track_params[track_idx, 0:len(t_est), 0] = t_est
            track_params[track_idx, 0:len(t_est), 1] = theta_est
            track_params_repo[0:len(t_est), 0] = t_est
            track_params_repo[0:len(t_est), 1] = theta_est
            n_paths[0:len(t_est)] += 1
            l_idx = len(t_est)
        elif flag == 1:
            t_est = t_est - t_est[l_ue]
            # grow the repo with genuinely new paths (verbatim:616-632)
            l = 0
            while l < len(t_est):
                dist = np.concatenate([
                    np.reshape(np.abs(track_params_repo[0:l_idx, 0] - np.tile(t_est[l], l_idx)), [l_idx, 1]),
                    np.reshape(np.abs(track_params_repo[0:l_idx, 1] - np.tile(theta_est[l], l_idx)), [l_idx, 1])], axis=1)
                static_flag = dist < 15
                if np.max(np.sum(static_flag, axis=1)) < 2:
                    if l_idx < 5:
                        track_params_repo[l_idx, 0] = t_est[l]
                        track_params_repo[l_idx, 1] = theta_est[l]
                        l_idx += 1
                    else:
                        t_est = np.delete(t_est, l)
                        theta_est = np.delete(theta_est, l)
                    l -= 1
                l += 1

            # brute-force assignment to existing paths (verbatim:640-682)
            loss = np.zeros([7, len(t_est)])
            loss[0:l_idx, 0:len(t_est)] = \
                np.abs(np.tile(np.reshape(t_est, [1, len(t_est)]), [l_idx, 1]) - np.tile(track_params_repo[0:l_idx, 0:1], [1, len(t_est)])) + \
                np.abs(np.tile(np.reshape(theta_est, [1, len(t_est)]), [l_idx, 1]) - np.tile(track_params_repo[0:l_idx, 1:2], [1, len(t_est)]))
            permute = cs._pick_permute(l_idx, len(t_est))
            min_loss, min_idx = 10000, -1
            for i1 in range(0, np.shape(permute)[0]):
                if len(t_est) == 3:
                    indices = ((permute[i1, 0], permute[i1, 1], permute[i1, 2]), (0, 1, 2))
                elif len(t_est) == 2:
                    indices = ((permute[i1, 0], permute[i1, 1]), (0, 1))
                else:
                    indices = (permute[i1, 0], 0)
                pl = np.sum(loss[indices])
                if pl < min_loss:
                    min_loss, min_idx = pl, i1
            asgn = permute[min_idx, :]
            for l in range(0, len(t_est)):
                if loss[asgn[l], l] < 25 and asgn[l] != 6:
                    track_params[track_idx, asgn[l], 0] = t_est[l]
                    track_params[track_idx, asgn[l], 1] = theta_est[l]
                    n_paths[asgn[l]] += 1

        # per-frame dynamic detection + position (verbatim:691-721)
        if (iter0 >= static_point) and (flag == 1) and (static_params is not None):
            frame_errs = []
            for l in range(0, len(t_est)):
                dist = np.concatenate([
                    np.reshape(np.abs(static_params[:, 0] - np.tile(t_est[l], len(static_params))), [len(static_params), 1]),
                    np.reshape(np.abs(static_params[:, 1] - np.tile(theta_est[l], len(static_params))), [len(static_params), 1])], axis=1)
                static_flag = dist < 15
                if np.max(np.sum(static_flag, axis=1)) < 2:        # dynamic path
                    if visibility[iter0] == 1:
                        x_e, y_e = cs.est_pos(cs.D, l_hat=t_est[l] * 1e-9 * cs.c,
                                              theta_hat=theta_est[l] * np.pi / 180)
                        pe = np.sqrt((x_e - positions[iter0, 0]) ** 2 + (y_e - positions[iter0, 1]) ** 2)
                        frame_errs.append((pe, x_e, y_e))
            if frame_errs:
                pe, x_e, y_e = min(frame_errs, key=lambda z: z[0])
                residual[iter0] = pe
                x_est_out[iter0] = x_e
                y_est_out[iter0] = y_e

        track_idx += 1

        # learn the static paths over the first static_point frames (verbatim:723-740)
        if iter0 == static_point - 1:
            est_mean_delay = np.sum(track_params[:, 0:l_idx, 0], axis=0) / n_paths[0:l_idx]
            est_mean_aoa = np.sum(track_params[:, 0:l_idx, 1], axis=0) / n_paths[0:l_idx]
            static_params = np.zeros([len(est_mean_delay), 2])
            static_params[:, 0] = est_mean_delay
            static_params[:, 1] = est_mean_aoa
            track_params = np.zeros([10, 5, 2])
            track_params_repo = np.zeros([5, 2])
            track_idx = 0
            n_paths = np.zeros(5)
        elif (track_idx == 10) and (iter0 >= static_point):
            # reset the 10-frame tracking window (we record per-frame, not windowed)
            track_params = np.zeros([10, 5, 2])
            track_params_repo = np.zeros([5, 2])
            track_idx = 0
            n_paths = np.zeros(5)

    return {
        "residual": residual, "x_est": x_est_out, "y_est": y_est_out,
        "x_gt": positions[:, 0], "y_gt": positions[:, 1],
        "visible": visibility, "static_point": static_point, "Te": Te,
        "scenario": scenario, "trajectory": trajectory, "seed": seed,
    }


def main():
    ap = argparse.ArgumentParser(description="offline per-TTI CellSense position estimator")
    ap.add_argument("--scenario", default="Open_scenario")
    ap.add_argument("--traj", default="Trajectory_1")
    ap.add_argument("--seed", type=int, default=10)
    ap.add_argument("--out", default="./plot/trial1/")
    ap.add_argument("--cap", default=None,
                    help="(interface demo only) a muApp_capture_channel.py file; "
                         "the live 2-antenna filtered channel cannot localize")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    if a.cap:
        print(f"[cellsense] NOTE: --cap given ({a.cap}). The live-twin PUSCH DMRS "
              "channel is 2-antenna, low-pass-filtered and static; it demonstrates "
              "the H[rx][sc] interface but cannot produce a sensing position. "
              "Running the ISAC-dataset trial instead for the residual plot.")

    res = run_trial(a.scenario, a.traj, a.seed)
    r = res["residual"]
    sp = res["static_point"]
    vis = res["visible"]
    Te = res["Te"]

    # write per-frame table
    txt = os.path.join(a.out, "residual.txt")
    with open(txt, "w") as f:
        f.write("# frame time_s visible x_gt y_gt x_est y_est residual_m\n")
        for k in range(len(r)):
            f.write(f"{k} {k*Te:.3f} {vis[k]} "
                    f"{res['x_gt'][k]:.4f} {res['y_gt'][k]:.4f} "
                    f"{res['x_est'][k]:.4f} {res['y_est'][k]:.4f} "
                    f"{res['residual'][k]:.4f}\n")
    np.savez(os.path.join(a.out, "trial.npz"), **res)

    have = r[~np.isnan(r)]
    n_vis_dyn = int(np.sum(vis[sp:] == 1))
    print(f"[cellsense] scenario={a.scenario} traj={a.traj} seed={a.seed}")
    print(f"[cellsense] static-learning frames: 0..{sp-1}; dynamic frames: {sp}..99")
    print(f"[cellsense] frames with a visible object (dynamic phase): {n_vis_dyn}")
    print(f"[cellsense] frames with a position estimate: {len(have)}")
    if len(have):
        print(f"[cellsense] residual error (m): min={have.min():.3f} "
              f"median={np.median(have):.3f} mean={have.mean():.3f} "
              f"max={have.max():.3f} RMSE={np.sqrt(np.mean(have**2)):.3f}")
    print(f"[cellsense] wrote {txt} and trial.npz")


if __name__ == "__main__":
    main()
