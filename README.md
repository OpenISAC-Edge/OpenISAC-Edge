# OpenISAC-Edge

**An open platform for reproducible integrated sensing and communication (ISAC) experimentation on a complete open 5G network.**

Developed by the [WINGS Lab](https://luna-xue.github.io/) at the University of Hawaiʻi at Mānoa. Publicly developed in this organization since February 2026; first tagged release [v0.1.0](https://github.com/OpenISAC-Edge/OpenISAC-Edge/releases/tag/v0.1.0) (August 2026).

<!-- Replace with badges that describe THIS repository, not upstream OCUDU: -->
[![Release](https://img.shields.io/github/v/release/OpenISAC-Edge/OpenISAC-Edge)](https://github.com/OpenISAC-Edge/OpenISAC-Edge/releases)
[![License](https://img.shields.io/badge/license-<see_License_section>-blue)](#license)
[![Project site](https://img.shields.io/badge/project-site-green)](https://luna-xue.github.io/pages/open-source/openisac-edge/)

## What is OpenISAC-Edge

OpenISAC-Edge lets researchers and students run ISAC experiments end to end on open-source infrastructure. The reference deployment combines the [OCUDU](https://ocudu.org) radio access network with the [free5GC](https://free5gc.org) core into a complete open 5G system, streams channel state information (CSI) from the running network, and provides tools for evaluating and visualizing sensing models against live or recorded measurements. Modular interfaces connect additional sensing models, radios, edge devices, and ISAC accelerators.

## What is in this repository

<!-- Adjust to the actual repository layout. Every component named in the
     PESOSE Project Description Section 2 should be findable from here. -->
- **ISAC-enhanced RAN** (`<path>`): OCUDU-based CU/DU with ISAC-specific metric extraction, extended WebSocket telemetry adapters, and visualization hooks. Tracks upstream OCUDU releases.
- **Reference core configuration** (`<path>`): free5GC configuration files and deployment manifests for the complete open 5G reference system.
- **CSI streaming and sensing tools** (`<path>`): CSI capture and streaming pipeline, sensing model evaluation harness, and visualization tools.
- **Documentation** (`<path>`): installation and deployment guides and the reference configuration used in the v0.1.0 release.

## Relationship to upstream OCUDU

OpenISAC-Edge builds on OCUDU, a permissively licensed open-source 5G CU/DU project governed under the Linux Foundation. This repository tracks upstream OCUDU releases and adds the instrumentation needed for ISAC research. The WINGS Lab participates in the OCUDU community through the University of Hawaiʻi System's associate membership in the Linux Foundation OCUDU Ecosystem Foundation, and changes of general use are proposed upstream through the normal OCUDU contribution and review processes rather than accumulating in this repository. OpenISAC-Edge is an independent project of the WINGS Lab and is not endorsed by the upstream OCUDU maintainers; "OCUDU" is used here to identify the upstream project.

## Getting started

Prerequisites: `<OS / SDR hardware (e.g., USRP <model>) / GPU (optional) / container runtime>`.

1. `<clone / build or pull container images>`
2. `<deploy the reference OCUDU + free5GC configuration>`
3. `<start CSI streaming and run the example sensing model>`

Step-by-step installation, configuration, and deployment instructions: `<link to docs in this repo>`. A companion walkthrough from environment setup to a running O-RAN/AI-RAN testbed is available in the [WINGS Lab testbed tutorial](https://luna-xue.github.io/pages/docs/oran/).

The v0.1.0 release has been validated on the documented OCUDU + free5GC reference configuration. Additional radios, edge devices, and hardware targets are candidate integrations for future releases.

## Documentation and community

- Project site: <https://luna-xue.github.io/pages/open-source/openisac-edge/>
- Questions and discussion: [GitHub Discussions](https://github.com/OpenISAC-Edge/OpenISAC-Edge/discussions)
- Bug reports and feature requests: [Issues](https://github.com/OpenISAC-Edge/OpenISAC-Edge/issues)
- Contributions are welcome through pull requests; see [CONTRIBUTING.md](./CONTRIBUTING.md) and our [Code of Conduct](./CODE_OF_CONDUCT.md). Development happens in public, led by the WINGS Lab team with student contributions.

## Security

Please report suspected vulnerabilities privately to `<security contact email>` rather than through public issues; see [SECURITY.md](./SECURITY.md). We follow coordinated disclosure: details of unresolved vulnerabilities are withheld until affected users have been notified and a fix or mitigation is available.

## Users and adopters

OpenISAC-Edge is used in UHM courses ECE 669 and ECE 693E, by the mmWave ISAC Vertically Integrated Projects team, and in the JESSE physical-AI and robotic-car projects. It underpins the UHM FutureG testbed and the DOE Genesis project STRATOS. Groups outside the developing team have evaluated it in mmWave hardware integration, security, open-source silicon, BioHealth, agriculture, and transportation contexts. If you are using OpenISAC-Edge, we would like to hear from you through Discussions.

## License

<!-- OPTION (a): single license, simplest and always defensible -->
This repository is licensed under the BSD 3-Clause Open MPI variant license inherited from the upstream OCUDU project; see [LICENSE](./LICENSE). Portions of this software may implement 3GPP specifications, which may be subject to additional licensing requirements.

<!-- OPTION (b): split licensing; use ONLY if you separate the code and add
     LICENSE-APACHE and NOTICE files today. Delete option (a) if used.
OCUDU-derived code in `<paths>` remains under the BSD 3-Clause Open MPI variant license of the upstream project; see [LICENSE](./LICENSE). Components original to OpenISAC-Edge in `<paths>` (CSI streaming, sensing model evaluation and visualization tools, and deployment configurations) are licensed under the Apache License 2.0; see [LICENSE-APACHE](./LICENSE-APACHE). The [NOTICE](./NOTICE) file describes which license applies to which part of the tree. Portions of this software may implement 3GPP specifications, which may be subject to additional licensing requirements.
-->

## Citing OpenISAC-Edge

If you use OpenISAC-Edge in your research, please cite:

> X. Xue, H. Tang, Y. Zheng, and B. Murmann. OpenISAC-Edge: An Open-Source Resource for AI-Native ISAC Experimentation. University of Hawaiʻi at Mānoa, 2026. https://github.com/OpenISAC-Edge

A machine-readable citation is provided in [CITATION.cff](./CITATION.cff).

## Acknowledgments

OpenISAC-Edge originated in work supported by the U.S. National Science Foundation under CyberTraining award #2417891 and by an NVIDIA Academic Grant Program award. Any opinions, findings, and conclusions are those of the authors and do not necessarily reflect the views of the sponsors.
