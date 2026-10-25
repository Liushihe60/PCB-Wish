# PCB-Wish

<p align="center">
  <a href="docs/readme/images/app-home.png">
    <img src="docs/readme/images/app-home.png" alt="PCB-Wish home screen" width="860">
  </a>
</p>

<p align="center">
  <strong>An agent workbench from natural-language requirements to a manufacturable PCB</strong><br>
  Windows 10/11 · v3.16.2 · Local execution · Bring your own API key
</p>

<p align="center">
  <a href="README.md">简体中文</a> | English · <a href="README.pdf">Full report (Chinese)</a> · <a href="README.tex">LaTeX source</a>
</p>

PCB-Wish is a desktop agent system for PCB design. Users describe a hardware goal in natural language; the system handles requirement clarification, component selection, schematic capture, PCB layout and routing, and verification, finally exporting Gerber, drill, BOM, pick-and-place, and inspection reports. When the user explicitly requests a teaching lecture, the system also generates a LaTeX-typeset PDF lecture after the fabrication files are complete.

The project is a task-specific modification of the domestic open-source ZCode framework (with the domain plugin located in `apps/zcode-cli/packages/pcb-wish-plugin/`), and design execution relies on LCEDA Pro (JLCEDA Professional Edition).

JLC is a leading domestic PCB platform whose EDA scope matches this project's target scenarios; its ecosystem spans an open-source hardware community and the LCSC component mall for one-stop fabrication. The system integrates JLC's official mall and EDA APIs—selecting parts from LCSC and drawing schematics, PCBs, and routing in JLCEDA—covering the full workflow from component selection to fabrication files. Meanwhile, ZCode provides a mature open-source agent framework and native desktop interaction that, combined with the JLC ecosystem, enable wish-style hardware development.

## Background

PCB-Wish was inspired by the teaching process of the Engineering Innovation (*工科创*) course. While the course introduces electronic design and board drawing, a unified syllabus cannot cover every student's individual needs and interests. After completing the course and gaining basic electronics knowledge, many students want to build a custom board around their own interests, yet face hurdles in component selection, parameter calculation, and fabrication workflows.

Inspired by this, the author hopes to use rapidly advancing large language models to connect electronic design learning with projects that students genuinely care about, making it an interest-driven self-learning tool. By turning the author's complete board-making workflow into an agent system, the project aims both to help students turn hardware ideas into real boards and, through the accompanying lecture, to guide them in learning numerical calculation and component selection methods—grounding "AI + teaching and learning" in real project practice.

With this goal, users simply describe a target board of moderate difficulty in natural language, and the agent works in stages through requirement clarification, component selection, schematic and PCB drawing, and verification to export fabrication-ready files, plus an optional teaching lecture upon request. The entire design and delivery workflow uses domestic production tools: a ZCode-based desktop workbench paired with LCEDA Pro.

## Core capabilities

- **Conversational intake.** Describe the board in everyday language; the system lists design-critical gaps, asks at most two rounds of questions, and produces a machine-checkable `MISSION.md`.
- **Staged gates.** Work advances through G0–G7. Every gate requires evidence files; a failed gate triggers revision or a pause rather than proceeding with gaps.
- **Independent verification.** A separate read-only agent re-reads the canvas, runs netlist checks, and parses Gerber files instead of relying on the design agent's verbal claims.
- **EDA execution.** Drives LCEDA Pro to build footprints, draw schematics, place and route, pour copper, add silkscreen, and export fabrication files.
- **Manufacturing deliverables.** Outputs Gerber, drill, BOM, pick-and-place, assembly notes, and inspection reports to `projects/<slug>/export/`.
- **Teaching lecture.** Generated only on explicit request: an overview plus module chapters covering operating principles, design rationale, layout and routing, and verification results.
- **Traceability.** Decision logs, gate states, and verification records are saved under `logs/` and `reports/` as the factual basis for design, review, and lecturing.

## Workflow

```text
Requirements
  │
  ▼
Intake + sourcing (G0 / G0.5)
  │
  ▼
Design (G1–G5.5)
  │
  ▼
Independent verification (G6)
  │
  ▼
Delivery: Gerber, drill, BOM, pick-and-place, inspection report
  └── Teaching lecture (G7, explicit user request)
```

`MISSION.md` is the single source of truth; gate states are recorded in `projects/<slug>/reports/gates.json`, with corresponding evidence files and inspection records at every gate.

<p align="center">
  <a href="docs/readme/images/app-conversation.png">
    <img src="docs/readme/images/app-conversation.png" alt="PCB-Wish conversation UI" width="780">
  </a>
</p>

<p align="center"><sub>Task list on the left; natural-language input, agent replies, and model selection on the right.</sub></p>

## Quick start

1. Prepare Windows 10/11 64-bit, LCEDA Pro (>= 4.1.60), and Python 3.10+.
2. Install `PCB-Wish-3.16.2-win-x64.exe`.
3. On first launch, enter an API key. The endpoint must be OpenAI-compatible and the model must support tool calling (1M context recommended). Configuration stays on the local machine.
4. Select a workspace and describe the target board in natural language; the system asks focused questions to close design gaps.
5. Design advances through G0–G7. Deliverables are written to `projects/<slug>/export/`, with the delivery note at `reports/DELIVERY_FINAL.md`.

Run the desktop app from source:

```bash
pnpm bootstrap
pnpm dev:desktop
```

## Test case: FlexiHeal v2

FlexiHeal v2 is the only complete test so far that ran from natural-language requirements to fabrication files and then to a teaching lecture. The task was a flexible bend-sensor angle measurement board: convert the sensor's 53–90 Ω resistance change into 0–3.3 V for an ADC, support USB/lithium battery power and charging, an OLED interface, 0°/90° calibration buttons, and a debug port, with hand soldering by the client.

<p align="center">
  <a href="docs/readme/images/board-pour.png">
    <img src="docs/readme/images/board-pour.png" alt="FlexiHeal v2 top copper pour" width="780">
  </a>
</p>

<p align="center"><sub>FlexiHeal v2 after top copper pour (red: top copper; blue: bottom copper; yellow: silkscreen and pads).</sub></p>

The case produced Gerber, drill, BOM, pick-and-place, assembly notes, inspection reports, and a 58-page lecture; the fabrication files were independently reviewed along two paths. This test used only the project's task book as the requirement input; it does not mean the result has been delivered or put into service.

## Teaching lecture

The teaching lecture is not a default deliverable. It enters the `MISSION.md` deliverable list only when the user explicitly requests it during intake. Without an explicit request, the intake agent may ask whether one is needed; if not confirmed or selected, no lecture is generated and the delivery note records that. Once requested, the system generates the PDF after the board passes G6.

The current lecture has one overview chapter plus seven functional-module chapters. Each chapter covers key points, operating principles, design rationale and calculations, PCB layout and routing notes, and verification methods and results. The FlexiHeal v2 lecture is 58 pages with 27 real engineering figures, with key numbers tied to measured, calculated, or simulated records wherever possible.

<p align="center">
  <a href="docs/readme/images/lecture-sample.png">
    <img src="docs/readme/images/lecture-sample.png" alt="FlexiHeal v2 lecture sample page" width="620">
  </a>
</p>

<p align="center"><sub>A sample lecture page: PCB layout notes with an engineering example figure.</sub></p>

## Scope and current limits

- Scope: 2–4 layer, low-speed digital and analog boards of small-to-medium density. RF, high-speed, and blind/buried-via requests are steered toward simplification or phasing.
- Routing is primarily autorouted with scripted repair; aesthetics, cost, EMC, and thermal design still require engineering judgment.
- Ordering, payment, external communication, and final sign-off remain the user's responsibility. Before fabrication, review footprints, polarity, and the BOM, and complete the JLC DFM check.
- Due to budget constraints, one full human-in-the-loop test (FlexiHeal v2) has been completed; a fully unattended run remains unverified. The lecture currently demonstrates only the built-in default skill package; combinations with other open-source skill packages have not been shown.

## Repository layout

- `apps/zcode-cli/packages/pcb-wish-plugin/`: the PCB-Wish domain plugin, including agents, skills, gates, and EDA scripts.
- `apps/`: desktop and command-line applications.
- `packages/`: shared runtime and infrastructure.
- `docs/readme/`: report images and build outputs.

## Documentation

- [README.pdf](README.pdf): full report (Chinese)
- [README.tex](README.tex): LaTeX source
- [Plugin technical notes](apps/zcode-cli/packages/pcb-wish-plugin/README.md)
- [docs/readme/images](docs/readme/images): UI, PCB, and lecture images

## License and acknowledgements

First-party desktop code follows the upstream [ZCode](https://github.com/zai-org/ZCode) Apache-2.0 license; the PCB-Wish plugin is MIT; third-party components retain their own licenses. Design execution relies on LCEDA Pro, and lectures are typeset with XeLaTeX. Thanks to the FlexiHeal client for the real task book, acceptance feedback, and revision suggestions.

Constrained by time and funding, the project is still a work in progress with much room for improvement. I sincerely hope it can genuinely help students learning electronic design, myself included. If the opportunity arises, I hope to keep improving it.
