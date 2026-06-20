// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#pragma once

#include <cstdint>

namespace ocudu {
namespace isac {

/// Wire magic, ASCII "ISAC".
constexpr uint32_t FRAME_MAGIC = 0x49534143;
/// Wire format version (v2 added the `kind` byte for multi-stream multiplexing).
constexpr uint16_t FRAME_VERSION = 2;

/// Frame kinds multiplexed on the same ZMQ PUB socket.
constexpr uint8_t KIND_CSI = 0; ///< Channel estimate H[k], indexed by active subcarrier.
constexpr uint8_t KIND_EQ  = 1; ///< Equalized data symbols (constellation points).

/// \brief Logical ISAC frame header (common to all kinds).
///
/// Serialized tightly (little-endian, no struct padding) ahead of the complex64 payload; see the publisher.
/// Python unpack: struct "<IHBHIHBBBHH" (22 bytes) then complex64 * count.
/// `count` = number of subcarriers (KIND_CSI) or number of equalized symbols (KIND_EQ).
struct frame_header {
  uint32_t magic    = FRAME_MAGIC;
  uint16_t version  = FRAME_VERSION;
  uint8_t  kind     = KIND_CSI;
  uint16_t scs_khz  = 0;
  uint32_t slot     = 0;
  uint16_t rnti     = 0;
  uint8_t  symbol   = 0;
  uint8_t  rx_port  = 0;
  uint8_t  tx_layer = 0;
  uint16_t rb_start = 0;
  uint16_t count    = 0;
};

/// Serialized header size in bytes (4+2+1+2+4+2+1+1+1+2+2).
constexpr unsigned HEADER_BYTES = 22;

} // namespace isac
} // namespace ocudu
