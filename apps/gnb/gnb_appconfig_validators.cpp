// SPDX-FileCopyrightText: Copyright (C) 2021-2026 Software Radio Systems Limited
// SPDX-License-Identifier: BSD-3-Clause-Open-MPI
// Portions of this file may implement 3GPP specifications, which may be subject to additional licensing requirements.

#include "gnb_appconfig_validators.h"
#include "apps/helpers/logger/logger_appconfig_validator.h"
#include "apps/services/worker_manager/worker_manager_appconfig_validator.h"
#include "apps/units/flexible_o_du/o_du_high/du_high/du_high_config.h"
#include "apps/units/o_cu_cp/cu_cp/cu_cp_unit_config.h"
#include "ocudu/ran/pusch/pusch_constants.h"
#include <algorithm>
#include <arpa/inet.h>

using namespace ocudu;

static bool validate_hal_config(const std::optional<hal_appconfig>& config)
{
#ifdef DPDK_FOUND
  if (config && config->eal_args.empty()) {
    fmt::print("It is mandatory to fill the EAL configuration arguments to initialize DPDK correctly\n");
    return false;
  }
#endif
  return true;
}

bool ocudu::validate_appconfig(const gnb_appconfig& config)
{
  if (!validate_logger_appconfig(config.log_cfg)) {
    return false;
  }

  if (!validate_expert_execution_appconfig(config.expert_execution_cfg)) {
    return false;
  }

  if (!validate_hal_config(config.hal_config)) {
    return false;
  }

  if (config.isac_cfg.port == 0 || config.isac_cfg.nof_rx_ports == 0 || config.isac_cfg.nof_tx_layers == 0) {
    fmt::print("ISAC port and capture counts must be positive, even when export is disabled.\n");
    return false;
  }

  return true;
}

bool ocudu::validate_isac_appconfig(const isac_appconfig& config, const du_high_unit_config& du_hi_cfg)
{
  if (!config.enabled) {
    return true;
  }

  in_addr ipv4{};
  if (::inet_pton(AF_INET, config.bind_addr.c_str(), &ipv4) != 1) {
    fmt::print("Invalid isac.bind_addr '{}': expected a numeric IPv4 address.\n", config.bind_addr);
    return false;
  }

  if (du_hi_cfg.cells_cfg.empty()) {
    fmt::print("ISAC requires at least one configured cell.\n");
    return false;
  }

  for (unsigned cell_index = 0; cell_index != du_hi_cfg.cells_cfg.size(); ++cell_index) {
    const auto& cell = du_hi_cfg.cells_cfg[cell_index].cell;
    if (config.nof_rx_ports > cell.nof_antennas_ul) {
      fmt::print("ISAC requests {} RX ports, but cell {} has {} configured UL RX ports.\n",
                 config.nof_rx_ports,
                 cell_index,
                 cell.nof_antennas_ul);
      return false;
    }

    const bool single_layer =
        cell.pusch_cfg.enable_transform_precoding || !cell.pdcch_cfg.dedicated.dci_format_0_1_and_1_1 ||
        cell.pdcch_cfg.dedicated.ss2_type == search_space_configuration::type_t::common;
    const unsigned max_layers =
        single_layer ? 1U : std::min({cell.nof_antennas_ul, pusch_constants::MAX_NOF_LAYERS, cell.pusch_cfg.max_rank});
    if (config.nof_tx_layers > max_layers) {
      fmt::print("ISAC requests {} TX layers, but cell {} permits at most {} UL layers.\n",
                 config.nof_tx_layers,
                 cell_index,
                 max_layers);
      return false;
    }
  }
  return true;
}

bool ocudu::validate_plmn_and_tacs(const du_high_unit_config& du_hi_cfg, const cu_cp_unit_config& cu_cp_cfg)
{
  if (cu_cp_cfg.amf_config.no_core) {
    return true;
  }

  std::vector<cu_cp_unit_supported_ta_item> supported_tas;

  supported_tas.reserve(cu_cp_cfg.amf_config.amf.supported_tas.size());
  for (const auto& supported_ta : cu_cp_cfg.amf_config.amf.supported_tas) {
    supported_tas.push_back(supported_ta);
  }

  for (const auto& amf : cu_cp_cfg.extra_amfs) {
    for (const auto& supported_ta : amf.supported_tas) {
      supported_tas.push_back(supported_ta);
    }
  }

  for (const auto& cell : du_hi_cfg.cells_cfg) {
    bool found = false;
    for (const auto& supported_ta : supported_tas) {
      for (const auto& plmn_item : supported_ta.plmn_list) {
        if (plmn_item.plmn_id == cell.cell.plmn && supported_ta.tac == cell.cell.tac) {
          found = true;
          break;
        }
      }
    }

    if (!found) {
      fmt::print("Could not find cell PLMN '{}' and cell TAC '{}' in the CU-CP supported tracking areas list\n",
                 cell.cell.plmn,
                 cell.cell.tac);
      return false;
    }
  }

  return true;
}
