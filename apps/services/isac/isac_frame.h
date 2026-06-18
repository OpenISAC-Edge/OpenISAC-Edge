// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#pragma once

#include <cstdint>

namespace ocudu {
namespace isac {

/// Wire magic, ASCII "ISAC".
constexpr uint32_t FRAME_MAGIC = 0x49534143;
/// Wire format version.
constexpr uint16_t FRAME_VERSION = 1;

/// \brief Logical ISAC frame header.
///
/// Serialized tightly (little-endian, no struct padding) ahead of the complex64 payload; see the publisher.
/// Python unpack: struct "<IHHIHBBBHH" (21 bytes) then complex64 * nof_sc.
struct frame_header {
  uint32_t magic    = FRAME_MAGIC;
  uint16_t version  = FRAME_VERSION;
  uint16_t scs_khz  = 0;
  uint32_t slot     = 0;
  uint16_t rnti     = 0;
  uint8_t  symbol   = 0;
  uint8_t  rx_port  = 0;
  uint8_t  tx_layer = 0;
  uint16_t rb_start = 0;
  uint16_t nof_sc   = 0;
};

/// Serialized header size in bytes (4+2+2+4+2+1+1+1+2+2).
constexpr unsigned HEADER_BYTES = 21;

} // namespace isac
} // namespace ocudu
