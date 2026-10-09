#!/usr/bin/env python3
"""cellsense_sensing.py -- ISAC (bibhor) sensing algorithm, ported for TinyTwin.

Faithful port of the mDTrack-style sensing pipeline in
  ISAC/Python_scripts/Simulation/run_open_scenario.py
refactored into reusable functions so that a SINGLE trial (one trajectory, one
Monte-Carlo realization) can be run and a per-frame (per-TTI) position estimate
+ residual error recorded.

Pipeline, per frame k (= one sensing TTI):
  1. ray-tracing path params (AoA, Doppler, delay, gain) -> synthesize a
     4-antenna ULA OFDM receive signal -> LS channel estimate on the SRS comb
     -> interpolate to full CFR H[k, N_ant, N_ifft]        (build_channel_cube)
  2. mDTrack per-path estimation on H[k]:   AoA via beam-sweep (classical
     beamforming over a steering grid) + ToA/Doppler via the Cross-Ambiguity
     Function (matched filter), with successive-interference cancellation
     (resolve iterations)                                   (estimate_frame)
  3. LoS-from-UE referencing (find_los) + static-path tracking across frames
     -> a path not matching any learned static path is a DYNAMIC object
  4. geometry solve: (bistatic range, AoA) -> (x, y)        (est_pos)
  5. residual = || (x,y)_est - (x,y)_groundtruth ||

The angle estimator needs the N_ant=4 ULA and the moving reflector that live in
the ISAC ray-tracing channel; that is why the sensing trial runs on the ISAC
dataset, not on the live-twin 2-antenna low-pass-filtered PUSCH DMRS estimate
(see README). The muApp (muApp_capture_channel.py) demonstrates the live H[rx][sc]
extraction interface; this module is the offline consumer.

All physical constants and math below are copied verbatim from run_open_scenario.py.
"""
import os
import numpy as np

# --------------------------------------------------------------------------
# Signal / system parameters (verbatim from run_open_scenario.py:275-313)
# --------------------------------------------------------------------------
D = 11                    # UE-gNB distance (m); sets the bistatic reference
c = 299792458.0
f_c = 3.6192e9            # 3.6192 GHz, FR1
bandwidth = 20e6
scs = 30e3
K_TC = 2                  # SRS transmission comb

# 20 MHz numerology
f_s = 30.72e6
N_ifft = 1024
nPRB = 51
N_cp = 72
occ_bandwidth = 12 * nPRB * scs

osf = 8                   # oversampling factor for CAF delay/Doppler search
N_ant = 4                 # gNB ULA size
d_ant = 0.5 * np.arange(0, N_ant, 1)   # element spacing in wavelengths (half-lambda)
P_t = 0.1                 # Tx power (W)
K_BOLTZ = 1.380649e-23
T_KELVIN = 298
noise_var = K_BOLTZ * bandwidth * T_KELVIN

# mDTrack search grids (verbatim)
F_SEARCH = np.arange(-2000, 2000, 1000)
T_SEARCH = np.arange(-50, 50, 1)
THETA_SEARCH0 = np.arange(-5 * np.pi / 12, 5 * np.pi / 12, 0.1)
RESOLVE = 5               # mDTrack refinement iterations
L_EST = 3                 # number of paths estimated per frame


# --------------------------------------------------------------------------
# Geometry solver: (bistatic range l_hat, reflected-path AoA theta_hat) -> (x,y)
# gNB(Tx) at origin, UE(Rx) at (0, D); single-bounce gNB->object->gNB model.
# (verbatim from run_open_scenario.py:20-31)
# --------------------------------------------------------------------------
def est_pos(d, l_hat, theta_hat):
    x1 = (d + l_hat) * np.sin(theta_hat)
    y1 = (d + l_hat) * np.cos(theta_hat)
    m = (y1 - d) / x1
    x_mid = x1 / 2
    y_mid = (y1 + d) / 2
    x_s_hat = (x_mid / m + y_mid) / (1 / m + 1 / np.tan(theta_hat))
    y_s_hat = x_s_hat / np.tan(theta_hat)
    return x_s_hat, y_s_hat


# --------------------------------------------------------------------------
# Comb -> full CFR interpolation (verbatim, K_TC=2 branch used)
# --------------------------------------------------------------------------
def interpolate_channel_estimates(H_combed, nPRB, N_ifft, K_TC):
    H_combed = np.concatenate([H_combed[N_ifft - 6 * nPRB:N_ifft], H_combed[0:6 * nPRB]])
    K_srs = int(np.ceil(12 * nPRB / K_TC))
    H = np.zeros([12 * nPRB], dtype=np.complex128)
    n = 0
    if K_TC == 2:
        filt8_start = np.array([12288, 8192, 4096, 0, 0, 0, 0, 0]) / 16384
        filt8_end = np.array([4096, 8192, 12288, 16384]) / 16384
        filt8_middle2 = np.array([4096, 8192, 8192, 8192, 4096, 0, 0, 0]) / 16384
        filt8_middle4 = np.array([0, 0, 4096, 8192, 8192, 8192, 4096, 0]) / 16384
        for k in range(0, K_srs):
            if k == 0:
                H[n:n + len(filt8_start)] += H_combed[k * K_TC] * filt8_start
            elif k == K_srs - 1:
                H[n:n + len(filt8_end)] += H_combed[k * K_TC] * filt8_end
            elif np.mod(k, 2) == 1:
                H[n:n + len(filt8_middle2)] += H_combed[k * K_TC] * filt8_middle2
            elif np.mod(k, 2) == 0:
                H[n:n + len(filt8_middle4)] += H_combed[k * K_TC] * filt8_middle4
                n = k * K_TC
    else:
        raise NotImplementedError("only K_TC=2 ported (matches the Open scenario)")
    H = np.concatenate([H[6 * nPRB:12 * nPRB], np.zeros(N_ifft - 12 * nPRB), H[0:6 * nPRB]])
    return H


# --------------------------------------------------------------------------
# LoS-from-UE identification (verbatim:105-117)
# --------------------------------------------------------------------------
def find_los(t_est, theta_est, alpha_est, aoa_ue):
    alpha_max = np.max(np.abs(alpha_est))
    while len(t_est) > 0:
        l_ue = np.argmin(t_est)
        if (np.abs(theta_est[l_ue] - aoa_ue) > 7.5) or (np.abs(alpha_est[l_ue]) < 0.5 * alpha_max):
            t_est = np.delete(t_est, l_ue)
            theta_est = np.delete(theta_est, l_ue)
            alpha_est = np.delete(alpha_est, l_ue)
        else:
            break
    return t_est, theta_est, alpha_est, l_ue


# --------------------------------------------------------------------------
# Path-assignment permutation tables (verbatim:123-272)
# --------------------------------------------------------------------------
permute1_3 = np.array([[0, 6, 6], [6, 0, 6], [6, 6, 0]])
permute2_3 = np.array([[0, 1, 6], [1, 0, 6], [0, 6, 1], [1, 6, 0], [6, 0, 1], [6, 1, 0]])
permute3_3 = np.array([[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]])
permute4_3 = np.array([[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0],
                       [3, 1, 2], [3, 2, 1], [3, 0, 2], [3, 2, 0], [3, 0, 1], [3, 1, 0],
                       [0, 3, 2], [0, 3, 1], [1, 3, 2], [1, 3, 0], [2, 3, 1], [2, 3, 0],
                       [0, 1, 3], [0, 2, 3], [1, 0, 3], [1, 2, 3], [2, 0, 3], [2, 1, 3]])
permute5_3 = np.array([[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0],
                       [3, 1, 2], [3, 2, 1], [3, 0, 2], [3, 2, 0], [3, 0, 1], [3, 1, 0],
                       [0, 3, 2], [0, 3, 1], [1, 3, 2], [1, 3, 0], [2, 3, 1], [2, 3, 0],
                       [0, 1, 3], [0, 2, 3], [1, 0, 3], [1, 2, 3], [2, 0, 3], [2, 1, 3],
                       [4, 1, 2], [4, 2, 1], [4, 0, 2], [4, 2, 0], [4, 0, 1], [4, 1, 0],
                       [0, 4, 2], [0, 4, 1], [1, 4, 2], [1, 4, 0], [2, 4, 1], [2, 4, 0],
                       [0, 1, 4], [0, 2, 4], [1, 0, 4], [1, 2, 4], [2, 0, 4], [2, 1, 4],
                       [4, 1, 3], [4, 2, 3], [4, 0, 3], [4, 3, 0], [4, 3, 1], [4, 3, 2],
                       [0, 4, 3], [1, 4, 3], [2, 4, 3], [3, 4, 0], [3, 4, 1], [3, 4, 2],
                       [0, 3, 4], [1, 3, 4], [2, 3, 4], [3, 0, 4], [3, 1, 4], [3, 2, 4]])
permute1_2 = np.array([[0, 6], [6, 0]])
permute2_2 = np.array([[0, 1], [1, 0]])
permute3_2 = np.array([[0, 1], [0, 2], [1, 0], [1, 2], [2, 0], [2, 1]])
permute4_2 = np.array([[0, 1], [0, 2], [0, 3], [1, 0], [1, 2], [1, 3],
                       [2, 0], [2, 1], [2, 3], [3, 0], [3, 1], [3, 2]])
permute5_2 = np.array([[0, 1], [0, 2], [0, 3], [0, 4], [1, 0], [1, 2], [1, 3], [1, 4],
                       [2, 0], [2, 1], [2, 3], [2, 4], [3, 0], [3, 1], [3, 2], [3, 4],
                       [4, 0], [4, 1], [4, 2], [4, 3]])


def _pick_permute(l_idx, n_est):
    tbl = {
        (1, 3): permute1_3, (2, 3): permute2_3, (3, 3): permute3_3,
        (4, 3): permute4_3, (5, 3): permute5_3,
        (1, 2): permute1_2, (2, 2): permute2_2, (3, 2): permute3_2,
        (4, 2): permute4_2, (5, 2): permute5_2,
    }
    if n_est == 1:
        return np.reshape(np.arange(0, l_idx, 1), [l_idx, 1])
    return tbl[(l_idx, n_est)]


# --------------------------------------------------------------------------
# Build the per-frame CFR cube H[K, N_ant, N_ifft] from ray-tracing params.
# Mirrors run_open_scenario.py:333-438 (signal synth + LS estimate + interp).
# --------------------------------------------------------------------------
def load_dataset(scenario, trajectory, root=None):
    if root is None:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.join(root, scenario, trajectory)
    chan_params = np.load(os.path.join(base, 'ISAC_ray_tracing_chan.npy'))
    positions = np.load(os.path.join(base, 'ISAC_locations.npy'))
    obj_params = np.load(os.path.join(base, 'obj_params.npy'))
    return chan_params, positions, obj_params


def build_channel_cube(chan_params, rng):
    """ray-tracing params -> H[K, N_ant, N_ifft] (full CFR after comb interp)."""
    K = 100
    tau = chan_params[:, 2, :].copy()
    theta = chan_params[:, 0, :].copy()
    Doppler = chan_params[:, 1, :].copy()
    pathgain = chan_params[:, 3, :].copy()

    for k in range(0, K):
        order = np.argsort(pathgain[k, :])[::-1]
        tau[k, :] = tau[k, order]
        theta[k, :] = theta[k, order]
        Doppler[k, :] = Doppler[k, order]
        pathgain[k, :] = pathgain[k, order]

    # AoA convention remap (verbatim:358-360)
    theta = theta + np.pi
    theta = np.pi / 2 - theta
    theta = (theta + np.pi) % (2 * np.pi) - np.pi
    pathgain = P_t * pathgain / noise_var

    L = np.shape(tau)[1]
    gen_signal = np.zeros([K, N_ant, N_ifft], dtype=np.complex128)

    # SRS pilot (ZC on the comb) + QPSK fill (verbatim:391-400)
    Xn = np.zeros([12 * nPRB], dtype=np.complex128)
    m = np.arange(0, int(np.ceil(12 * nPRB / K_TC)), 1)
    Xn[np.arange(0, 12 * nPRB, K_TC)] = np.exp(-1j * np.pi * m * (m + 2) / (len(m)))
    Xn[np.setdiff1d(np.arange(0, 12 * nPRB, 1), np.arange(0, 12 * nPRB, K_TC))] = \
        np.exp(1j * (np.pi * rng.integers(0, 4, [12 * nPRB - len(m)]) / 2 + np.pi / 4))
    Xn = np.concatenate([Xn[6 * nPRB:12 * nPRB], np.zeros(N_ifft - 12 * nPRB), Xn[0:6 * nPRB]])
    Xn = np.reshape(Xn, [N_ifft, 1])

    X_pilot = np.zeros([12 * nPRB], dtype=np.complex128)
    X_pilot[np.arange(0, 12 * nPRB, K_TC)] = np.exp(-1j * np.pi * m * (m + 2) / (len(m)))
    X_pilot = np.concatenate([X_pilot[6 * nPRB:12 * nPRB], np.zeros(N_ifft - 12 * nPRB), X_pilot[0:6 * nPRB]])

    t = np.arange(N_cp, N_cp + N_ifft, 1) / f_s
    n = np.concatenate([np.arange(0, N_ifft // 2, 1), np.arange(-N_ifft // 2, 0, 1)])
    n = np.reshape(n, [N_ifft, 1])
    n = np.tile(n, [1, N_ifft])

    temp_xn = np.tile(Xn, [1, N_ifft])
    phase = np.exp(1j * 2 * np.pi * rng.random((K, L)))
    for k in range(0, K):
        for l in range(0, L):
            if ((theta[k, l] >= -np.pi / 2) and (theta[k, l] < np.pi / 2)) and (pathgain[k, l] > 1):
                temp_sgnl = 1 / (np.sqrt(N_ifft)) * np.sqrt(pathgain[k, l]) * phase[k, l] * \
                    np.exp(1j * 2 * np.pi * Doppler[k, l] * t) * \
                    np.sum(temp_xn * np.exp(1j * 2 * np.pi * scs * n *
                           np.tile(np.reshape(t - N_cp / f_s - tau[k, l], [1, N_ifft]), [N_ifft, 1])), axis=0)
                gen_signal[k, :, :] = gen_signal[k, :, :] + \
                    np.tile(np.reshape(np.exp(1j * 2 * np.pi * d_ant * np.sin(theta[k, l])), [N_ant, 1]), [1, N_ifft]) * \
                    np.tile(np.reshape(temp_sgnl, [1, N_ifft]), [N_ant, 1])

    rcvd_signal = gen_signal + 1 / np.sqrt(2) * (rng.normal(size=[K, N_ant, N_ifft]) +
                                                 1j * rng.normal(size=[K, N_ant, N_ifft]))
    rcvd_sig_freq = 1 / np.sqrt(N_ifft) * np.fft.fft(rcvd_signal, axis=2)

    H_combed = np.zeros([K, N_ant, N_ifft], dtype=np.complex128)
    for i in range(0, N_ifft):
        if X_pilot[i] == 0:
            H_combed[:, :, i] = np.zeros([K, N_ant])
        else:
            H_combed[:, :, i] = rcvd_sig_freq[:, :, i] * np.conjugate(np.tile(X_pilot[i], [K, N_ant]))

    H = np.zeros([K, N_ant, N_ifft], dtype=np.complex128)
    for k in range(0, K):
        for n_ant in range(0, N_ant):
            H[k, n_ant, :] = interpolate_channel_estimates(H_combed[k, n_ant, :], nPRB, N_ifft, K_TC)
    return H


# --------------------------------------------------------------------------
# mDTrack per-frame estimator: H[N_ant, N_ifft] -> parameters[4, L_EST]
# rows = (AoA rad, Doppler Hz, delay in osf-samples, complex gain)
# Mirrors run_open_scenario.py:453-574.
# --------------------------------------------------------------------------
def estimate_frame(H_frame):
    # time-domain SRS reference (long ZC) (verbatim:448-469)
    m = np.arange(0, 12 * nPRB, 1)
    Xn = np.exp(-1j * np.pi * m * (m + 2) / (len(m)))
    Xn = np.concatenate([Xn[6 * nPRB:12 * nPRB], np.zeros(N_ifft - 12 * nPRB), Xn[0:6 * nPRB]])
    Xn = np.reshape(Xn, [N_ifft, 1])

    rx_time = []
    for a in range(0, N_ant):
        sym = H_frame[a, :] * Xn[:, 0]
        sym = np.concatenate([sym[0:N_ifft // 2], np.zeros((osf - 1) * N_ifft), sym[N_ifft // 2:N_ifft]])
        rx_time.append(np.sqrt(osf * N_ifft) * np.fft.ifft(sym))
    mean_chan_subcarrier = np.array(rx_time)                 # [N_ant, osf*N_ifft]

    Xn_os = np.concatenate([Xn[0:N_ifft // 2, 0], np.zeros((osf - 1) * N_ifft), Xn[N_ifft // 2:N_ifft, 0]])
    time_gensym = np.sqrt(osf * N_ifft) * np.fft.ifft(Xn_os)

    parameters = np.zeros([4, L_EST], dtype=np.complex128)
    theta_search = THETA_SEARCH0
    residual_sig = None
    for it in range(0, RESOLVE):
        for l in range(0, L_EST):
            if it > 0:
                gs = np.tile(np.reshape(time_gensym, [1, osf * N_ifft]), [N_ant, 1]) * \
                    np.tile(np.reshape(np.exp(1j * 2 * np.pi * d_ant * np.sin(np.real(parameters[0, l]))), [N_ant, 1]), [1, osf * N_ifft])
                gs = np.roll(gs, int(np.real(parameters[2, l])), axis=1)
                gs = gs * np.tile(np.reshape(np.exp(1j * 2 * np.pi * np.real(parameters[1, l]) *
                                  np.arange(0, osf * N_ifft, 1) / (osf * f_s)), [1, osf * N_ifft]), [N_ant, 1])
                gs = parameters[3, l] * gs
                mean_chan_subcarrier = gs + residual_sig
            if it > 2:
                theta_search = np.arange(-np.pi / 24, np.pi / 24, 0.01) + np.real(parameters[0, l])

            est_mean_rxsym = mean_chan_subcarrier * np.conjugate(np.tile(mean_chan_subcarrier[0:1, :], [N_ant, 1]))

            # AoA: classical beam-sweep over the steering grid
            beam = np.zeros(len(theta_search))
            for i in range(0, len(theta_search)):
                beam[i] = np.sum(np.abs(np.reshape(np.exp(-1j * 2 * np.pi * d_ant * np.sin(theta_search[i])),
                                                   [1, N_ant]) @ est_mean_rxsym) ** 2)
            max_theta = theta_search[np.argmax(beam)]
            parameters[0, l] = max_theta

            # ToA + Doppler via Cross-Ambiguity Function (matched filter)
            nn = np.arange(0, osf * N_ifft, 1)
            combined_chan = np.sum(np.tile(np.reshape(np.exp(-1j * 2 * np.pi * d_ant * np.sin(max_theta)),
                                   [N_ant, 1]), [1, osf * N_ifft]) * mean_chan_subcarrier, axis=0)
            max_caf, t_best = 0, 0
            for t in range(0, len(T_SEARCH)):
                v = np.abs(np.sum(np.roll(np.conj(time_gensym), T_SEARCH[t]) * combined_chan))
                if v > max_caf:
                    t_best, max_caf = t, v
            shifted = np.roll(np.conj(time_gensym), T_SEARCH[t_best]) * combined_chan
            max_caf, f_best = 0, 0
            for f in range(0, len(F_SEARCH)):
                v = np.abs(np.sum(shifted * np.exp(-1j * 2 * np.pi * F_SEARCH[f] * nn / (osf * f_s))))
                if v > max_caf:
                    f_best, max_caf = f, v
            parameters[1, l] = F_SEARCH[f_best]
            parameters[2, l] = T_SEARCH[t_best]

            # regenerate & subtract (SIC)
            gs = np.tile(np.reshape(time_gensym, [1, osf * N_ifft]), [N_ant, 1]) * \
                np.tile(np.reshape(np.exp(1j * 2 * np.pi * d_ant * np.sin(max_theta)), [N_ant, 1]), [1, osf * N_ifft])
            gs = np.roll(gs, T_SEARCH[t_best], axis=1)
            gs = gs * np.tile(np.reshape(np.exp(1j * 2 * np.pi * F_SEARCH[f_best] * nn / (osf * f_s)),
                              [1, osf * N_ifft]), [N_ant, 1])
            alpha = np.average(mean_chan_subcarrier / gs)
            parameters[3, l] = alpha
            residual_sig = mean_chan_subcarrier - alpha * gs
            mean_chan_subcarrier = residual_sig
    return parameters
