// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#include "ocudu/phy/upper/isac/isac_tap.h"
#include <atomic>

using namespace ocudu;

namespace {

/// Process-global ISAC sink. Read on the PHY hot path, written once at startup.
std::atomic<isac::ce_sink*> g_isac_sink{nullptr};

} // namespace

void isac::register_sink(isac::ce_sink* sink)
{
  g_isac_sink.store(sink, std::memory_order_release);
}

isac::ce_sink* isac::get_sink()
{
  return g_isac_sink.load(std::memory_order_acquire);
}
