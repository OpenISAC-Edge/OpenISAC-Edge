// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#pragma once

#include "apps/services/isac/isac_publisher.h"
#include "ocudu/phy/upper/isac/isac_tap.h"
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
  zmq_sink(const std::string& endpoint, task_executor& exec_, unsigned decimation_ = 1);

  void on_ch_estimate(const dmrs_pusch_estimator_results& est, const pusch_processor::pdu_t& pdu) override;
  void on_eq_symbols(span<const cf_t> eq_symbols, uint16_t rnti, slot_point slot) override;

  /// True if the underlying PUB socket bound successfully.
  bool is_open() const { return pub && pub->is_open(); }

private:
  std::shared_ptr<publisher> pub;
  task_executor&             exec;
  unsigned                   decimation;
};

} // namespace isac
} // namespace ocudu
