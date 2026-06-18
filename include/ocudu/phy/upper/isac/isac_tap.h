// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#pragma once

#include "ocudu/phy/upper/channel_processors/pusch/pusch_processor.h"

namespace ocudu {

class dmrs_pusch_estimator_results;

namespace isac {

/// \brief Sink for uplink channel-estimate captures (the ISAC tap).
///
/// Implemented by the egress (ZMQ) layer. \ref on_ch_estimate is invoked on the real-time PHY thread, so
/// implementations must keep the call cheap and defer any I/O to a relaxed executor.
class ce_sink
{
public:
  virtual ~ce_sink() = default;

  /// Called once per processed PUSCH with the fully populated channel estimates and PDU context.
  virtual void on_ch_estimate(const dmrs_pusch_estimator_results& est, const pusch_processor::pdu_t& pdu) = 0;
};

/// Registers the process-global ISAC sink (pass nullptr to clear). Set once at startup.
void register_sink(ce_sink* sink);

/// Returns the current ISAC sink, or nullptr when the tap is disabled.
ce_sink* get_sink();

} // namespace isac
} // namespace ocudu
