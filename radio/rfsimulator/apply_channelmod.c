/*
* Licensed to the OpenAirInterface (OAI) Software Alliance under one or more
* contributor license agreements.  See the NOTICE file distributed with
* this work for additional information regarding copyright ownership.
* The OpenAirInterface Software Alliance licenses this file to You under
* the OAI Public License, Version 1.1  (the "License"); you may not use this file
* except in compliance with the License.
* You may obtain a copy of the License at
*
*      http://www.openairinterface.org/?page_id=698
*
* Author and copyright: Laurent Thomas, open-cells.com
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*-------------------------------------------------------------------------------
* For more information about the OpenAirInterface (OAI) Software Alliance:
*      contact@openairinterface.org
*/
#define _ARRAY_SIZE (80 * 1024 * 1024 / sizeof(long long unsigned int))

#include <time.h>
#include <stdio.h>
#include <complex.h>
#include <common/utils/LOG/log.h>
#include <openair1/SIMULATION/TOOLS/sim.h>
#include "rfsimulator.h"
//////
#include <stdio.h>
#include <omp.h>

extern long long unsigned int timing_array[_ARRAY_SIZE];
extern int timing_array_index;
extern FILE *fpr[50];
extern FILE *fpi[50];
extern int first_time;
// extern FILE *fplog4; 
extern int taplen;
// --- jammer channel (white noise replayed through its own per-TTI taps) ---
extern FILE *fpr_jam[50]; // jammer taps, real part (one handle per socket)
extern FILE *fpi_jam[50]; // jammer taps, imag part
extern int jam_enable;    // 0/1 from --JAM
extern double jam_gain;   // linear output gain from --JGAIN
extern int jamtaplen;     // number of jammer taps from --JTAP (default 64)
//extern int counterr;
 //int counter=0;
//  FILE *fpr;
//  FILE *fpi;
//  FILE *fplog;


// void conv(const c16_t *input_sig,
//                 c16_t *after_channel_sig,
//                 int nsamp,
//                 float *mchannelModel){

// }

/*
  Legacy study:
  The parameters are:
  gain&loss (decay, signal power, ...)
  either a fixed gain in dB, a target power in dBm or ACG (automatic control gain) to a target average
  => don't redo the AGC, as it was used in UE case, that must have a AGC inside the UE
  will be better to handle the "set_gain()" called by UE to apply it's gain (enable test of UE power loop)
  lin_amp = pow(10.0,.05*txpwr_dBm)/sqrt(nb_tx_antennas);
  a lot of operations in legacy, grouped in one simulation signal decay: txgain*decay*rxgain

  multi_path (auto convolution, ISI, ...)
  either we regenerate the channel (call again random_channel(desc,0)), or we keep it over subframes
  legacy: we regenerate each sub frame in UL, and each frame only in DL
*/
void rxAddInput(const c16_t *input_sig,
                c16_t *after_channel_sig,
                int rxAnt,
                channel_desc_t *channelDesc,
                int nbSamples,
                uint64_t TS,
                uint32_t CirSize,
                uint32_t sock_num)
{
  
  char strr[2000],stri[2000];
  static int line_count = 0;  
 
  //fgets(strr, sizeof(strr), fpr);
  //fgets(stri, sizeof(stri), fpi);
  
  if ((channelDesc->sat_height > 0) && (channelDesc->enable_dynamic_delay || channelDesc->enable_dynamic_Doppler)) { // model for transparent satellite on circular orbit
    /* assumptions:
       - The Earth is spherical, the ground station is static, and that the Earth does not rotate.
       - An access or link is possible from the satellite to the ground station at all times.
       - The ground station is located at the North Pole (positive Zaxis), and the satellite starts from the initial elevation angle 0° in the second quadrant of the YZplane.
       - Satellite moves in the clockwise direction in its circular orbit.
    */
    const double radius_earth = 6371e3; // m
    const double radius_sat = radius_earth + channelDesc->sat_height;
    const double GM_earth = 3.986e14; // m^3/s^2
    const double w_sat = sqrt(GM_earth / (radius_sat * radius_sat * radius_sat)); // rad/s

    // start_time and end_time are when the pos_sat_z == pos_gnb_z (elevation angle == 0 and 180 degree)
    const double start_phase = -acos(radius_earth / radius_sat); // SAT is just rising above the horizon
    const double end_phase = -start_phase; // SAT is just falling behind the horizon
    const double start_time = start_phase / w_sat; // in seconds
    const double end_time = end_phase / w_sat; // in seconds
    const uint64_t duration_samples = (end_time - start_time) * channelDesc->sampling_rate;
    const double t = start_time + ((TS - channelDesc->start_TS) % duration_samples) / channelDesc->sampling_rate;

    const double pos_sat_x = 0;
    const double pos_sat_y = radius_sat * sin(w_sat * t);
    const double pos_sat_z = radius_sat * cos(w_sat * t);

    const double vel_sat_x = 0;
    const double vel_sat_y =  w_sat * radius_sat * cos(w_sat * t);
    const double vel_sat_z = -w_sat * radius_sat * sin(w_sat * t);

    const double pos_ue_x = 0;
    const double pos_ue_y = 0;
    const double pos_ue_z = radius_earth;

    const double dir_sat_ue_x = pos_ue_x - pos_sat_x;
    const double dir_sat_ue_y = pos_ue_y - pos_sat_y;
    const double dir_sat_ue_z = pos_ue_z - pos_sat_z;

    const double dist_sat_ue = sqrt(dir_sat_ue_x * dir_sat_ue_x + dir_sat_ue_y * dir_sat_ue_y + dir_sat_ue_z * dir_sat_ue_z);
    const double vel_sat_ue = (vel_sat_x * dir_sat_ue_x + vel_sat_y * dir_sat_ue_y + vel_sat_z * dir_sat_ue_z) / dist_sat_ue;

    double dist_gnb_sat = 0;
    double vel_gnb_sat = 0;
    if (channelDesc->modelid == SAT_LEO_TRANS) {
      const double pos_gnb_x = 0;
      const double pos_gnb_y = 0;
      const double pos_gnb_z = radius_earth;

      const double dir_gnb_sat_x = pos_sat_x - pos_gnb_x;
      const double dir_gnb_sat_y = pos_sat_y - pos_gnb_y;
      const double dir_gnb_sat_z = pos_sat_z - pos_gnb_z;

      dist_gnb_sat = sqrt(dir_gnb_sat_x * dir_gnb_sat_x + dir_gnb_sat_y * dir_gnb_sat_y + dir_gnb_sat_z * dir_gnb_sat_z);
      vel_gnb_sat = (vel_sat_x * dir_gnb_sat_x + vel_sat_y * dir_gnb_sat_y + vel_sat_z * dir_gnb_sat_z) / dist_gnb_sat;
    }

    const double c = 299792458; // m/s
    const double prop_delay = (dist_gnb_sat + dist_sat_ue) / c;
    if (channelDesc->enable_dynamic_delay)
      channelDesc->channel_offset = prop_delay * channelDesc->sampling_rate;

    const double f_Doppler_shift_sat_ue = (vel_sat_ue / (c - vel_sat_ue)) * channelDesc->center_freq;
    const double f_Doppler_shift_gnb_sat = (-vel_gnb_sat / c) * channelDesc->center_freq;
    if (channelDesc->enable_dynamic_Doppler)
      channelDesc->Doppler_phase_inc = 2 * M_PI * (f_Doppler_shift_gnb_sat + f_Doppler_shift_sat_ue) / channelDesc->sampling_rate;

    static uint64_t last_TS = 0;
    if(TS - last_TS >= channelDesc->sampling_rate) {
      last_TS = TS;
      LOG_I(HW, "Satellite orbit: time %f s, Doppler: gNB->SAT %f kHz, SAT->UE %f kHz, Delay %f ms\n", t, f_Doppler_shift_gnb_sat / 1000, f_Doppler_shift_sat_ue / 1000, prop_delay * 1000);
    }
  }

  // channelDesc->path_loss_dB should contain the total path gain
  // so, in actual RF: tx gain + path loss + rx gain (+antenna gain, ...)
  // UE and NB gain control to be added
  // Fixme: not sure when it is "volts" so dB is 20*log10(...) or "power", so dB is 10*log10(...)
  const double pathLossLinear = pow(10,channelDesc->path_loss_dB/20.0);
  // Energy in one sample to calibrate input noise
  // the normalized OAI value seems to be 256 as average amplitude (numerical amplification = 1)
  const double noise_per_sample = pow(10,channelDesc->noise_power_dB/10.0) * 256;
  const uint64_t dd = channelDesc->channel_offset;
  const int nbTx=channelDesc->nb_tx;
   // counterr++;
  // int mylen=1;
  // --- MIMO channel matrix, cached per receive cycle (fixes the nb_rx>1 de-sync) ------
  //   The RX loop calls rxAddInput once per rx antenna (rxAnt = 0..nb_rx-1, in order).
  //   We load the WHOLE nb_rx x nb_tx matrix ONCE on the rxAnt==0 call into a per-socket
  //   cache; each rx antenna then points at its own row. Row = flat tx-major
  //   [txAnt*taplen + l]; nb_tx*taplen must be <= 1024.
  #define _MIMO_MAXSOCK 8
  #define _MIMO_MAXRX   8
  static float Hr_cache[_MIMO_MAXSOCK][_MIMO_MAXRX][8*128];
  static float Hi_cache[_MIMO_MAXSOCK][_MIMO_MAXRX][8*128];
  const int nbRx = channelDesc->nb_rx;
  int si = (int)sock_num - first_time;
  if (si < 0) si = 0;
  if (si >= _MIMO_MAXSOCK) si = _MIMO_MAXSOCK - 1;
  const int rxi = (rxAnt >= 0 && rxAnt < _MIMO_MAXRX) ? rxAnt : 0;
  const float *mchannelModelr = Hr_cache[si][rxi]; // this rx antenna's row
  const float *mchannelModeli = Hi_cache[si][rxi];
      //printf("hiii\n");
    
    struct timespec start, end; // Structs to store time
    long long unsigned int diff; // Variable to store time difference
    clock_gettime(CLOCK_REALTIME, &start); // Log start time

    // if (fplog4 != NULL) {
    //   fprintf(fplog4, "%d\n", taplen);
    //   fflush(fplog4); // Ensure it's written to the file immediately
    // }

    // Load the full nb_rx x nb_tx channel matrix ONCE per cycle (on the rxAnt==0 call);
    // one file line per rx antenna. Other rx antennas reuse the cache via the row pointers.
    if (rxAnt == 0) {
      for (int r = 0; r < nbRx && r < _MIMO_MAXRX; r++) {
        if (fgets(strr, sizeof(strr), fpr[si]) == NULL) { rewind(fpr[si]); if (fgets(strr, sizeof(strr), fpr[si]) == NULL) break; }
        char *tk = strtok(strr, " ");
        int k = 0;
        for (; tk != NULL && k < nbTx*taplen && k < 8*128; k++) { Hr_cache[si][r][k] = atof(tk); tk = strtok(NULL, " "); }
        for (; k < 8*128; k++) Hr_cache[si][r][k] = 0.0f;
        if (fgets(stri, sizeof(stri), fpi[si]) == NULL) { rewind(fpi[si]); if (fgets(stri, sizeof(stri), fpi[si]) == NULL) break; }
        tk = strtok(stri, " ");
        k = 0;
        for (; tk != NULL && k < nbTx*taplen && k < 8*128; k++) { Hi_cache[si][r][k] = atof(tk); tk = strtok(NULL, " "); }
        for (; k < 8*128; k++) Hi_cache[si][r][k] = 0.0f;
      }
      line_count += nbRx;
      if (line_count % 1000 < nbRx) printf("TTI %llu: MIMO channel rows loaded, sock %u\n", TS, sock_num);
    }


    // --- jammer channel: load this TTI's jammer taps (real + imag), rewind on EOF ---
    float jamr[128] = {0}; // jammer taps, real (zero-initialised so unused taps are 0)
    float jami[128] = {0}; // jammer taps, imag
    if (jam_enable && fpr_jam[sock_num-first_time] != NULL && fpi_jam[sock_num-first_time] != NULL) {
      char jstr_r[2000], jstr_i[2000];
      if (fgets(jstr_r, sizeof(jstr_r), fpr_jam[sock_num-first_time]) == NULL) {
        rewind(fpr_jam[sock_num-first_time]);
        fgets(jstr_r, sizeof(jstr_r), fpr_jam[sock_num-first_time]);
      }
      char *jt = strtok(jstr_r, " ");
      for (int j = 0; jt != NULL && j < jamtaplen && j < 128; j++) {
        jamr[j] = atof(jt);
        jt = strtok(NULL, " ");
      }
      if (fgets(jstr_i, sizeof(jstr_i), fpi_jam[sock_num-first_time]) == NULL) {
        rewind(fpi_jam[sock_num-first_time]);
        fgets(jstr_i, sizeof(jstr_i), fpi_jam[sock_num-first_time]);
      }
      jt = strtok(jstr_i, " ");
      for (int j = 0; jt != NULL && j < jamtaplen && j < 128; j++) {
        jami[j] = atof(jt);
        jt = strtok(NULL, " ");
      }
    }

    // clock_gettime(CLOCK_REALTIME, &start); // Log start time
    // const struct complexd *channelModel= channelDesc->ch[rxAnt+(txAnt*channelDesc->nb_rx)];
    clock_gettime(CLOCK_REALTIME, &end); // Log end time

    // diff = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);
    // diff.tv_nsec = end.tv_nsec - start.tv_nsec;
    // append to the end of timing_array which has a fixed size, so if the array is full do not add
    diff = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);
    // if (timing_array_index < _ARRAY_SIZE) {
    //   timing_array[timing_array_index] = diff;
    //   timing_array_index = timing_array_index + 1;  
    // }

  // struct timespec start, end, diff; // Structs to store time
  // long long unsigned int diff; // Variable to store time difference
  // clock_gettime(CLOCK_REALTIME, &start); // Log start time

  // if (fplog != NULL) {
  //       fprintf(fplog, "Function started at: %ld.%09ld seconds\n", start.tv_sec, start.tv_nsec);
  //       fflush(fplog); // Ensure it's written to the file immediately
  // }

    // assign value to threads
  int threads = 4;

  // assign value to chunk    
  int chunk = 100;
  // X*H*N/threads;

  // #pragma omp parallel for schedule(guided, chunk) num_threads(threads)
  // jammer white-noise history ring (per call); indexed modulo 128, only last jamtaplen used
  double jnhist_r[128] = {0}, jnhist_i[128] = {0};
  int jhpos = 0;
  for (int i=0; i<nbSamples; i++) {
   
    struct complex16 *out_ptr=after_channel_sig+i;
    struct complexd rx_tmp= {0};

    for (int txAnt=0; txAnt < nbTx; txAnt++) {
      // clock_gettime(CLOCK_REALTIME, &start); // Log start time
      // const struct complexd *channelModel= channelDesc->ch[rxAnt+(txAnt*channelDesc->nb_rx)];
      // clock_gettime(CLOCK_REALTIME, &end); // Log end time

      // // diff = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);
      // // diff.tv_nsec = end.tv_nsec - start.tv_nsec;
      // // append to the end of timing_array which has a fixed size, so if the array is full do not add
      // if (timing_array_index < _ARRAY_SIZE) {
      //   timing_array[timing_array_index] = (end.tv_nsec - start.tv_nsec);
      //   timing_array_index = timing_array_index + 1;  
      // }

// append to the end of global array timing_array

  //   // if (fplog != NULL) {
  //   //   for (int i = 0; i <(int)channelDesc->channel_length; i++) {
  //   //       fprintf(fplog, "Element %d: real = %f, imag = %f\n", 
  //   //               i, channelModel[i].r, channelModel[i].i); // Adjust field names as needed
  //   //   }
  //   //   fprintf(fplog, "Done.");
  //   //   fflush(fplog); // Ensure everything is written to the file
  //   // }

      //const struct complex *channelModelEnd=channelModel+channelDesc->channel_length;
      //for (int l = 0; l<(int)channelDesc->channel_length; l++) {
      for (int l = 0; l<taplen; l=l+1) {
        // let's assume TS+i >= l
        // fixme: the rfsimulator current structure is interleaved antennas
        // this has been designed to not have to wait a full block transmission
        // but it is not very usefull
        // it would be better to split out each antenna in a separate flow
        // that will allow to mix ru antennas freely
        // (X + cirSize) % cirSize to ensure that index is positive
        const int idx = ((TS + i - l - dd) * nbTx + txAnt + CirSize) % CirSize;

        // save iq to a 256 avx2 vector from memory

        // if l is not a multiple of 4, mask the (l%4) last terms

        // save channel taps to a 256 avx2 vector

        // multiply these values

        // add all the complex values to each other --> how to do this in as few commands as possible?

        const struct complex16 tx16 = input_sig[idx];
        // rx_tmp.r += tx16.r * channelModel[l].r - tx16.i * channelModel[l].i;
        // rx_tmp.i += tx16.i * channelModel[l].r + tx16.r * channelModel[l].i;
        // MIMO: taps for the (rxAnt, txAnt) pair live at flat offset txAnt*taplen
        rx_tmp.r += tx16.r * mchannelModelr[txAnt*taplen + l] - tx16.i * mchannelModeli[txAnt*taplen + l];
        rx_tmp.i += tx16.i * mchannelModelr[txAnt*taplen + l] + tx16.r * mchannelModeli[txAnt*taplen + l];
        //printf("Read line: %f", mchannelModelr);
        //printf("Loop l=%d, txAnt=%d, rxAnt=%d\n", l, txAnt, rxAnt);
        //printf("  tx16 (real: %d, imag: %d), channelModel[%d] (real: %f, imag: %f)\n", tx16.r, tx16.i, l, channelModel[l].r, channelModel[l].i);
      } //l
    }


    if (channelDesc->Doppler_phase_inc != 0.0) {
#ifdef CMPLX
      double complex in = CMPLX(rx_tmp.r, rx_tmp.i);
#else
      double complex in = rx_tmp.r + rx_tmp.i * I;
#endif
      double complex out = in * cexp(channelDesc->Doppler_phase_cur[rxAnt] * I);
      rx_tmp.r = creal(out);
      rx_tmp.i = cimag(out);
      channelDesc->Doppler_phase_cur[rxAnt] += channelDesc->Doppler_phase_inc;
    }

    // --- jammer: fresh white noise pushed through the jammer channel taps ---
    double jam_r = 0.0, jam_i = 0.0;
    if (jam_enable) {
      jnhist_r[jhpos] = gaussZiggurat(0.0, 1.0); // newest noise sample
      jnhist_i[jhpos] = gaussZiggurat(0.0, 1.0);
      for (int l = 0; l < jamtaplen && l < 128; l++) {
        const int hp = (jhpos - l + 128) % 128;  // noise(i-l)
        const double sr = jnhist_r[hp], si = jnhist_i[hp];
        jam_r += jamr[l]*sr - jami[l]*si;
        jam_i += jamr[l]*si + jami[l]*sr;
      }
      jhpos = (jhpos + 1) % 128;
      jam_r *= jam_gain;
      jam_i *= jam_gain;
    }

    out_ptr->r += lround(rx_tmp.r*pathLossLinear + noise_per_sample*gaussZiggurat(0.0,1.0) + jam_r);
    out_ptr->i += lround(rx_tmp.i*pathLossLinear + noise_per_sample*gaussZiggurat(0.0,1.0) + jam_i);
    out_ptr++;
  }

  // clock_gettime(CLOCK_REALTIME, &end); // Log end time
    // Log end to file
    // if (fplog != NULL) {
    //     fprintf(fplog, "Function finished at: %ld.%09ld seconds\n", end.tv_sec, end.tv_nsec);
    //     // Optionally log duration
    //     double elapsed = (end.tv_sec - start.tv_sec) + (end.tv_nsec - start.tv_nsec) / 1e9;
    //     fprintf(fplog, "Execution time: %.9f seconds\n", elapsed);
    //     fflush(fplog);
    // }

  if ( (TS*nbTx)%CirSize+nbSamples <= CirSize )
    // Cast to a wrong type for compatibility !
    LOG_D(HW,"Input power %f, output power: %f, channel path loss %f, noise coeff: %f \n",
          10*log10((double)signal_energy((int32_t *)&input_sig[(TS*nbTx)%CirSize], nbSamples)),
          10*log10((double)signal_energy((int32_t *)after_channel_sig, nbSamples)),
          channelDesc->path_loss_dB,
          10*log10(noise_per_sample));
  

}


void txAddInput(const c16_t *input_sig,
  c16_t *after_channel_sig,
  int rxAnt,
  channel_desc_t *channelDesc,
  int nbSamples,
  uint64_t TS,
  uint32_t CirSize)
{

 char strr[2000],stri[2000];

// fgets(strr, sizeof(strr), fpr);
// fgets(stri, sizeof(stri), fpi);
// // channelDesc->path_loss_dB should contain the total path gain
// // so, in actual RF: tx gain + path loss + rx gain (+antenna gain, ...)
// // UE and NB gain control to be added
// // Fixme: not sure when it is "volts" so dB is 20*log10(...) or "power", so dB is 10*log10(...)
const double pathLossLinear = pow(10,channelDesc->path_loss_dB/20.0);
// // Energy in one sample to calibrate input noise
// // the normalized OAI value seems to be 256 as average amplitude (numerical amplification = 1)
const double noise_per_sample = pow(10,channelDesc->noise_power_dB/10.0) * 256;
const uint64_t dd = channelDesc->channel_offset;
// const int nbTx=channelDesc->nb_tx;
// // counterr++;
// // int mylen=1;

float mchannelModelr[128]={1}; // sized 128 to safely hold up to 128 signal taps (--TAP)
float mchannelModeli[128]={0};
// //printf("hiii\n");


if (fgets(strr, sizeof(strr), fpr[1]) != NULL) {  
  char *token1 = strtok(strr, " ");  
  int idx1 = 0;  
  while (token1 != NULL && idx1 < taplen) {  
    mchannelModelr[idx1] = atof(token1);  
    token1 = strtok(NULL, " ");  
    idx1++;  
  }  
}  
else {  
  rewind(fpr[1]);  // ADD THIS LINE  
  if (fgets(strr, sizeof(strr), fpr[1]) != NULL) {  
    char *token1 = strtok(strr, " ");  
    int idx1 = 0;  
    while (token1 != NULL && idx1 < taplen) {  
      mchannelModelr[idx1] = atof(token1);  
      token1 = strtok(NULL, " ");  
      idx1++;  
    }  
  }  
}  
  
if (fgets(stri, sizeof(stri), fpi[1]) != NULL) {  
  char *token2 = strtok(stri, " ");  
  int idx2 = 0;  
  while (token2 != NULL && idx2 < taplen) {  
    mchannelModeli[idx2] = atof(token2);  
    token2 = strtok(NULL, " ");  
    idx2++;  
  }  
}  
else {  
  rewind(fpi[1]);  // ADD THIS LINE  
  if (fgets(stri, sizeof(stri), fpi[1]) != NULL) {  
    char *token2 = strtok(stri, " ");  
    int idx2 = 0;  
    while (token2 != NULL && idx2 < taplen) {  
      mchannelModeli[idx2] = atof(token2);  
      token2 = strtok(NULL, " ");  
      idx2++;
    }
  }
}

// --- jammer channel (UL): load this TTI's jammer taps, NULL-guarded, rewind on EOF ---
float jamr[128] = {0}, jami[128] = {0};
if (jam_enable && fpr_jam[1] != NULL && fpi_jam[1] != NULL) {
  char jstr_r[2000], jstr_i[2000];
  if (fgets(jstr_r, sizeof(jstr_r), fpr_jam[1]) == NULL) {
    rewind(fpr_jam[1]);
    fgets(jstr_r, sizeof(jstr_r), fpr_jam[1]);
  }
  char *jt = strtok(jstr_r, " ");
  for (int j = 0; jt != NULL && j < jamtaplen && j < 128; j++) { jamr[j] = atof(jt); jt = strtok(NULL, " "); }
  if (fgets(jstr_i, sizeof(jstr_i), fpi_jam[1]) == NULL) {
    rewind(fpi_jam[1]);
    fgets(jstr_i, sizeof(jstr_i), fpi_jam[1]);
  }
  jt = strtok(jstr_i, " ");
  for (int j = 0; jt != NULL && j < jamtaplen && j < 128; j++) { jami[j] = atof(jt); jt = strtok(NULL, " "); }
}

// // clock_gettime(CLOCK_REALTIME, &start); // Log start time
// // const struct complexd *channelModel= channelDesc->ch[rxAnt+(txAnt*channelDesc->nb_rx)];
// clock_gettime(CLOCK_REALTIME, &end); // Log end time

// // diff = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);
// // diff.tv_nsec = end.tv_nsec - start.tv_nsec;
// // append to the end of timing_array which has a fixed size, so if the array is full do not add
// diff = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);
// // if (timing_array_index < _ARRAY_SIZE) {
// //   timing_array[timing_array_index] = diff;
// //   timing_array_index = timing_array_index + 1;  
// // }

// jammer white-noise history ring (UL), per call; last jamtaplen samples used
double jnhist_r[128] = {0}, jnhist_i[128] = {0};
int jhpos = 0;
for (int i=0; i<nbSamples; i++) {

struct complex16 *out_ptr=after_channel_sig+i;
struct complexd rx_tmp= {0};

// for (int txAnt=0; txAnt < nbTx; txAnt++) {

for (int l = 0; l<taplen; l++) {

// input_sig is ONLY the current TX block ([0..nbSamples)), with no inter-block history.
// The old code did ((i-l-dd)+CirSize)%CirSize, so for i-l-dd < 0 it wrapped to ~CirSize and
// read far out of bounds of input_sig (garbage) — harmless at taplen=1 (only l=0 is read),
// but with taplen>=2 every block's first `taplen` samples got corrupted, which broke SISO
// UL (RACH/PUSCH) attach. Skip taps with no in-block history instead (treat input as 0);
// the echo then applies to the rest of the block, so a multi-tap UL comes through.
const int sidx = (int)i - l - (int)dd;
if (sidx < 0 || sidx >= nbSamples) continue;

const struct complex16 tx16 = input_sig[sidx];
// // rx_tmp.r += tx16.r * channelModel[l].r - tx16.i * channelModel[l].i;
// // rx_tmp.i += tx16.i * channelModel[l].r + tx16.r * channelModel[l].i;
rx_tmp.r += tx16.r * mchannelModelr[l] - tx16.i * mchannelModeli[l];
rx_tmp.i += tx16.i * mchannelModelr[l] + tx16.r * mchannelModeli[l];
// //printf("Read line: %f", mchannelModelr);
// //printf("Loop l=%d, txAnt=%d, rxAnt=%d\n", l, txAnt, rxAnt);
// //printf("  tx16 (real: %d, imag: %d), channelModel[%d] (real: %f, imag: %f)\n", tx16.r, tx16.i, l, channelModel[l].r, channelModel[l].i);
} //l
//}


if (channelDesc->Doppler_phase_inc != 0.0) {
#ifdef CMPLX
double complex in = CMPLX(rx_tmp.r, rx_tmp.i);
#else
double complex in = rx_tmp.r + rx_tmp.i * I;
#endif
double complex out = in * cexp(channelDesc->Doppler_phase_cur[rxAnt] * I);
rx_tmp.r = creal(out);
rx_tmp.i = cimag(out);
channelDesc->Doppler_phase_cur[rxAnt] += channelDesc->Doppler_phase_inc;
}

// --- jammer (UL): fresh white noise pushed through the jammer taps ---
double jam_r = 0.0, jam_i = 0.0;
if (jam_enable) {
  jnhist_r[jhpos] = gaussZiggurat(0.0, 1.0);
  jnhist_i[jhpos] = gaussZiggurat(0.0, 1.0);
  for (int l = 0; l < jamtaplen && l < 128; l++) {
    const int hp = (jhpos - l + 128) % 128;
    const double sr = jnhist_r[hp], si = jnhist_i[hp];
    jam_r += jamr[l]*sr - jami[l]*si;
    jam_i += jamr[l]*si + jami[l]*sr;
  }
  jhpos = (jhpos + 1) % 128;
  jam_r *= jam_gain;
  jam_i *= jam_gain;
}

out_ptr->r = lround(rx_tmp.r*pathLossLinear + noise_per_sample*gaussZiggurat(0.0,1.0) + jam_r);
out_ptr->i = lround(rx_tmp.i*pathLossLinear + noise_per_sample*gaussZiggurat(0.0,1.0) + jam_i);
 out_ptr++;
}

}

