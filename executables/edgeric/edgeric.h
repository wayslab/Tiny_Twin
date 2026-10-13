#ifndef EDGERIC_H
#define EDGERIC_H

#include <fstream>
#include <iostream>
#include <map>
#include <vector>
#include <tuple>
#include <zmq.hpp>
#include <optional>

// #include "metrics.pb.h"
#include "control_mcs.pb.h"
#include "control_weights.pb.h"
#include "metrics.pb.h"



class edgeric {

private:
    static std::map<uint16_t, float> weights_recved;
    static std::map<uint16_t, uint8_t> mcs_recved;

    static std::map<uint16_t, float> ue_cqis;
    static std::map<uint16_t, float> ue_snrs;
    static std::map<uint16_t, float> rx_bytes;
    static std::map<uint16_t, float> tx_bytes;
    static std::map<uint16_t, uint32_t> ue_ul_buffers;
    static std::map<uint16_t, uint32_t> ue_dl_buffers;
    static std::map<uint16_t, float> dl_tbs_ues;
    // MIMO-RIC: per-antenna UL channel measurement (downsampled), flattened [rx][sc] as re,im
    static std::map<uint16_t, std::vector<float>> ue_channel;
    static std::map<uint16_t, uint32_t> ue_channel_nrx;
    static std::map<uint16_t, uint32_t> ue_channel_nsc;
    // MIMO-RIC: raw LS (pre-filter) per-antenna UL channel, same flattening as ue_channel
    static std::map<uint16_t, std::vector<float>> ue_channel_ls;
    static std::map<uint16_t, uint32_t> ue_channel_ls_nrx;
    static std::map<uint16_t, uint32_t> ue_channel_ls_nsc;
    // SRS-RIC: per-antenna UL channel from the SRS codebook IQ matrix, [gnb_rx][prg] re,im
    static std::map<uint16_t, std::vector<float>> ue_srs_channel;
    static std::map<uint16_t, uint32_t> ue_srs_channel_nrx;
    static std::map<uint16_t, uint32_t> ue_srs_channel_nsc;

    static uint32_t er_ran_index_weights;
    static uint32_t er_ran_index_mcs;
    static bool enable_logging;
    static bool initialized;

    static void ensure_initialized();
    

public:
    
    static uint32_t tti_cnt;
    static void setTTI(uint32_t tti_count) {tti_cnt = tti_count;}
    static void printmyvariables();
    static void init();
    //Setters
    static void set_cqi(uint16_t rnti, float cqi) {ue_cqis[rnti] = cqi;}
    static void set_snr(uint16_t rnti, float snr) {ue_snrs[rnti] = snr;}
    static void set_ul_buffer(uint16_t rnti, uint32_t ul_buffer) {ue_ul_buffers[rnti] = ul_buffer;}
    static void set_dl_buffer(uint16_t rnti, uint32_t dl_buffer) {ue_dl_buffers[rnti] = dl_buffer;}
    static void set_tx_bytes(uint16_t rnti, float tbs) {tx_bytes[rnti] += tbs;} // ue_dl_buffers[rnti] -= tbs; }
    static void set_rx_bytes(uint16_t rnti, float tbs) {rx_bytes[rnti] += tbs;} // ue_ul_buffers[rnti] -= tbs;}
    static void set_dl_tbs(uint16_t rnti, float tbs) {dl_tbs_ues[rnti] = tbs;}  
    // MIMO-RIC: store a downsampled per-antenna UL channel snapshot for this UE
    static void set_channel(uint16_t rnti, const std::vector<float>& ch, uint32_t nrx, uint32_t nsc) {
        ue_channel[rnti] = ch; ue_channel_nrx[rnti] = nrx; ue_channel_nsc[rnti] = nsc;
    }
    // MIMO-RIC: store the raw LS (pre-filter) per-antenna UL channel snapshot for this UE
    static void set_channel_ls(uint16_t rnti, const std::vector<float>& ch, uint32_t nrx, uint32_t nsc) {
        ue_channel_ls[rnti] = ch; ue_channel_ls_nrx[rnti] = nrx; ue_channel_ls_nsc[rnti] = nsc;
    }
    // SRS-RIC: store the per-antenna SRS codebook channel snapshot for this UE
    static void set_srs_channel(uint16_t rnti, const std::vector<float>& ch, uint32_t nrx, uint32_t nsc) {
        ue_srs_channel[rnti] = ch; ue_srs_channel_nrx[rnti] = nrx; ue_srs_channel_nsc[rnti] = nsc;
    }
    //////////////////////////////////// ZMQ function to send RT-E2 Report 
    static void send_to_er();
    
    //////////////////////////////////// ZMQ function to receive RT-E2 Policy - called at end of slot
    static void get_weights_from_er();
    static void get_mcs_from_er();

    //////////////////////////////////// Static getters - sets the control actions - called at slot beginning
    
    static std::optional<float> get_weights(uint16_t);
    static std::optional<uint8_t> get_mcs(uint16_t);
    


};


#endif // EDGERIC_H



