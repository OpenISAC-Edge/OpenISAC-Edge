// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#include "apps/services/isac/isac_zmq_sink.h"
#include "ocudu/adt/bounded_bitset.h"
#include "ocudu/adt/span.h"
#include "ocudu/phy/support/rb_allocation.h"
#include "ocudu/phy/upper/signal_processors/pusch/dmrs_pusch_estimator.h"
#include "ocudu/ran/resource_allocation/rb_bitmap.h"
#include "ocudu/ran/resource_block.h"
#include "ocudu/ran/slot_point.h"
#include "ocudu/support/executors/task_executor.h"
#include <algorithm>
#include <memory>
#include <utility>

using namespace ocudu;
using namespace ocudu::isac;

zmq_sink::zmq_sink(const std::string& endpoint, task_executor& exec_, unsigned decimation_) :
  pub(std::make_shared<publisher>(endpoint)), exec(exec_), decimation(decimation_ == 0 ? 1 : decimation_)
{
}

void zmq_sink::on_ch_estimate(const dmrs_pusch_estimator_results& est, const pusch_processor::pdu_t& pdu)
{
  // Sampling gate: keep one capture every 'decimation' slots.
  if (decimation > 1 && (pdu.slot.system_slot() % decimation) != 0) {
    return;
  }

  // Active subcarriers: expand the allocated CRBs to all 12 REs per PRB.
  crb_bitmap                             rb_mask = pdu.freq_alloc.get_crb_mask(pdu.bwp_start_rb, pdu.bwp_size_rb);
  bounded_bitset<NOF_SUBCARRIERS_PER_RB> all_re(NOF_SUBCARRIERS_PER_RB);
  all_re.fill();
  bounded_bitset<MAX_NOF_SUBCARRIERS> re_mask = rb_mask.kronecker_product<NOF_SUBCARRIERS_PER_RB>(all_re);

  const unsigned nof_sc = static_cast<unsigned>(re_mask.count());
  if (nof_sc == 0) {
    return;
  }

  // One DMRS symbol is sufficient under the 'average' time-domain strategy.
  int sym = pdu.dmrs_symbol_mask.find_lowest();
  if (sym < 0) {
    return;
  }
  const unsigned i_symbol = static_cast<unsigned>(sym);

  // Read rx_port 0 / layer 0 (SISO uplink) into a temp bf16 buffer, then convert to complex64.
  static_vector<cbf16_t, MAX_NOF_SUBCARRIERS> tmp(nof_sc);
  est.get_symbol_ch_estimate(span<cbf16_t>(tmp.data(), tmp.size()), i_symbol, 0, 0, re_mask);

  auto cap = std::make_unique<capture>();
  cap->iq.resize(nof_sc);
  for (unsigned k = 0; k != nof_sc; ++k) {
    cap->iq[k] = to_cf(tmp[k]);
  }

  const int     lowest_rb = rb_mask.find_lowest();
  frame_header& h         = cap->hdr;
  h.kind                  = KIND_CSI;
  h.scs_khz               = static_cast<uint16_t>(15u << pdu.slot.numerology());
  h.slot                  = pdu.slot.system_slot();
  h.rnti                  = pdu.rnti;
  h.symbol                = static_cast<uint8_t>(i_symbol);
  h.rx_port               = 0;
  h.tx_layer              = 0;
  h.rb_start              = static_cast<uint16_t>(lowest_rb < 0 ? 0 : lowest_rb);
  h.count                 = static_cast<uint16_t>(nof_sc);

  // Relaxed path: hand off to the executor; serialize+send happen off the PHY thread.
  // The publisher is captured by shared_ptr so it stays valid until the deferred task runs.
  std::shared_ptr<publisher> pub_ref = pub;
  (void)exec.defer([pub_ref = std::move(pub_ref), cap = std::move(cap)]() { pub_ref->publish(*cap); });
}

void zmq_sink::on_eq_symbols(span<const cf_t> eq_symbols, uint16_t rnti, slot_point slot)
{
  // Sampling gate: keep one capture every 'decimation' slots.
  if (decimation > 1 && (slot.system_slot() % decimation) != 0) {
    return;
  }

  const unsigned n = std::min<unsigned>(eq_symbols.size(), MAX_NOF_SUBCARRIERS);
  if (n == 0) {
    return;
  }

  auto cap = std::make_unique<capture>();
  cap->iq.resize(n);
  for (unsigned k = 0; k != n; ++k) {
    cap->iq[k] = eq_symbols[k];
  }

  frame_header& h = cap->hdr;
  h.kind          = KIND_EQ;
  h.scs_khz       = static_cast<uint16_t>(15u << slot.numerology());
  h.slot          = slot.system_slot();
  h.rnti          = rnti;
  h.symbol        = 0;
  h.rx_port       = 0;
  h.tx_layer      = 0;
  h.rb_start      = 0;
  h.count         = static_cast<uint16_t>(n);

  std::shared_ptr<publisher> pub_ref = pub;
  (void)exec.defer([pub_ref = std::move(pub_ref), cap = std::move(cap)]() { pub_ref->publish(*cap); });
}
