// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#pragma once

#include "apps/services/isac/isac_frame.h"
#include "ocudu/adt/complex.h"
#include "ocudu/adt/static_vector.h"
#include "ocudu/ran/resource_block.h"
#include <mutex>
#include <string>

namespace ocudu {
namespace isac {

/// One captured frame: header + complex64 payload (channel estimates or equalized symbols).
struct capture {
  frame_header                            hdr;
  static_vector<cf_t, MAX_NOF_SUBCARRIERS> iq;
};

/// \brief Owns a ZeroMQ PUB socket and serializes/sends captures best-effort.
///
/// Held behind a shared_ptr so in-flight deferred sends stay valid even if the sink is destroyed first.
class publisher
{
public:
  explicit publisher(const std::string& endpoint);
  ~publisher();
  publisher(const publisher&)            = delete;
  publisher& operator=(const publisher&) = delete;

  /// True if the PUB socket bound successfully.
  bool is_open() const { return socket != nullptr; }

  /// Serializes the capture (header + complex64 payload) and sends it non-blocking (drops if no room).
  ///
  /// Thread-safe: callable directly from multiple PHY worker threads. A ZMQ PUB socket is not
  /// thread-safe, so the actual zmq_send is serialized by an internal mutex.
  void publish(const capture& cap);

private:
  void*      context = nullptr;
  void*      socket  = nullptr;
  std::mutex send_mtx;
};

} // namespace isac
} // namespace ocudu
