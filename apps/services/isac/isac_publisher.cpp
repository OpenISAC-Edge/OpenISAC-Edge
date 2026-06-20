// SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI

#include "apps/services/isac/isac_publisher.h"
#include <cstdint>
#include <fmt/format.h>
#include <vector>
#include <zmq.h>

using namespace ocudu;
using namespace ocudu::isac;

namespace {

/// Appends the raw little-endian bytes of \c value to \c buf (host is little-endian on x86/ARM).
template <typename T>
void append_le(std::vector<uint8_t>& buf, T value)
{
  const auto* p = reinterpret_cast<const uint8_t*>(&value);
  buf.insert(buf.end(), p, p + sizeof(T));
}

} // namespace

publisher::publisher(const std::string& endpoint)
{
  context = zmq_ctx_new();
  if (context == nullptr) {
    fmt::print(stderr, "ISAC: failed to create ZMQ context\n");
    return;
  }
  socket = zmq_socket(context, ZMQ_PUB);
  if (socket == nullptr) {
    fmt::print(stderr, "ISAC: failed to create ZMQ PUB socket\n");
    return;
  }
  int linger = 0;
  zmq_setsockopt(socket, ZMQ_LINGER, &linger, sizeof(linger));
  if (zmq_bind(socket, endpoint.c_str()) != 0) {
    fmt::print(stderr, "ISAC: failed to bind ZMQ PUB to '{}': {}\n", endpoint, zmq_strerror(zmq_errno()));
    zmq_close(socket);
    socket = nullptr;
  }
}

publisher::~publisher()
{
  if (socket != nullptr) {
    zmq_close(socket);
  }
  if (context != nullptr) {
    zmq_ctx_term(context);
  }
}

void publisher::publish(const capture& cap)
{
  if (socket == nullptr) {
    return;
  }

  const frame_header& h = cap.hdr;

  // Reused per-thread so there is no heap allocation on the hot path after warm-up (capacity persists,
  // clear() keeps it). Thread-local: each PHY worker has its own build buffer; only zmq_send is locked.
  static thread_local std::vector<uint8_t> buf;
  buf.clear();
  buf.reserve(HEADER_BYTES + static_cast<size_t>(h.count) * 2 * sizeof(float));

  append_le(buf, h.magic);
  append_le(buf, h.version);
  append_le(buf, h.kind);
  append_le(buf, h.scs_khz);
  append_le(buf, h.slot);
  append_le(buf, h.rnti);
  append_le(buf, h.symbol);
  append_le(buf, h.rx_port);
  append_le(buf, h.tx_layer);
  append_le(buf, h.rb_start);
  append_le(buf, h.count);

  for (unsigned k = 0; k != h.count; ++k) {
    append_le(buf, cap.iq[k].real());
    append_le(buf, cap.iq[k].imag());
  }

  // Best-effort, non-blocking: drops the frame if the send queue is full or there is no subscriber.
  // Serialize socket access (PUB is not thread-safe; taps may call from concurrent PHY threads).
  {
    std::lock_guard<std::mutex> lock(send_mtx);
    (void)zmq_send(socket, buf.data(), buf.size(), ZMQ_DONTWAIT);
  }
}
