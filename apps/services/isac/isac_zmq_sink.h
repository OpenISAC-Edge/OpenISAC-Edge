// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#pragma once

#include "apps/services/isac/isac_publisher.h"
#include "ocudu/phy/upper/isac/isac_tap.h"
#include <atomic>
#include <memory>
#include <string>

namespace ocudu {

class task_executor;

namespace isac {

/// \brief ISAC sink: captures on the PHY thread, defers serialize+send to a relaxed executor.
///
/// The PHY thread does only a cheap copy + enqueue; the executor (a strand) serializes and sends the
/// frame off the real-time path. The single strand also makes the non-thread-safe PUB socket safe.
class zmq_sink : public ce_sink
{
public:
  /// \param endpoint   ZMQ PUB bind address, e.g. "tcp://0.0.0.0:5556".
  /// \param exec_      Relaxed (non-real-time) executor used to offload the serialize/send.
  /// \param decimation Keep one capture every \c decimation slots (1 = every slot).
  /// \param nof_rx_ports_ Capture up to the first N participating RX ports.
  /// \param nof_tx_layers_ Capture up to the first N active TX layers.
  zmq_sink(const std::string& endpoint,
           task_executor&     exec_,
           unsigned           decimation_    = 1,
           unsigned           nof_rx_ports_  = 1,
           unsigned           nof_tx_layers_ = 1);

  void on_ch_estimate(const dmrs_pusch_estimator_results& est, const pusch_processor::pdu_t& pdu) override;
  void on_eq_symbols(span<const cf_t> eq_symbols, uint16_t rnti, slot_point slot) override;

  /// True if the underlying PUB socket bound successfully.
  bool is_open() const { return pub && pub->is_open(); }

  /// Number of CSI path captures whose deferred publication was rejected by the executor.
  uint64_t get_rejected_csi_captures() const { return rejected_csi_captures.load(std::memory_order_relaxed); }

private:
  std::shared_ptr<publisher> pub;
  task_executor&             exec;
  unsigned                   decimation;
  unsigned                   nof_rx_ports;
  unsigned                   nof_tx_layers;
  std::atomic<uint64_t>       rejected_csi_captures{0};
};

} // namespace isac
} // namespace ocudu
