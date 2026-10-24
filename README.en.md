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

PCB-Wish is a desktop agent system for PCB design. Users describe a hardware goal in natural language; the system handles requirement clarification, component selection, schematic capture, PCB layout and routing, verification, and finally exports Gerber, drill, BOM, pick-and-place, and inspection reports. If the user explicitly requests a teaching lecture, the system also produces a LaTeX-typeset PDF lecture after the fabrication files are complete.

The project is a task-specific modification of the ZCode open-source framework. The domain plugin lives in `apps/zcode-cli/packages/pcb-wish-plugin/`, and design execution relies on LCEDA Pro (JLCEDA Professional Edition).

JLC is a domestic PCB platform whose EDA scope matches this project's sweet spot; its ecosystem includes an open-source community and the LCSC mall for one-stop fabrication. The agent fully integrates JLC's official mall and EDA APIs—selecting parts from LCSC, then drawing schematics, PCBs, and routing in JLCEDA—covering the full stack from selection to fabrication files. ZCode, a domestic open-source project, provides a complete agent framework that made development faster and easier; its native environment and user interaction, combined with JLC's ecosystem, enable basic wish-style development.

## Background

Users describe a target board of modest drawing difficulty in natural language. The agent works in stages to complete requirement clarification, component selection, schematic and PCB drawing, and verification, then exports fabrication-ready engineering files. It also produces an accompanying lecture when the user explicitly requests one. The board design and delivery workflow uses only domestic production tools: the desktop application is a task-specific modification of the ZCode open-source framework, and design execution uses LCEDA Pro.

PCB-Wish was inspired by the full teaching process of the Engineering Innovation (工科创) course. The course already covers electronic design and board drawing, but cannot cover everyone's needs and interests.

The author hopes it can become an interest-driven learning tool, so that most classmates in the same department, after taking the course and gaining some electronics foundation, can use large-model tools to fully try building a board with a function they are interested in, learn numerical calculation, component selection, and other methods of electronic design, and carry out project-based learning on their own.

Inspired by this, the author hopes to use today's rapidly developing large models to connect electronic design learning with projects that students are interested in. By developing this agent, the author hopes both to help students turn design ideas into working boards and to support their learning through the accompanying lecture. The system workflow comes from the author's complete board-fabrication process, and the project also tries to ground "AI + teaching and learning" in this process.

## Core capabilities

- **Conversational intake.** Describe the board in everyday language; the system lists design-critical gaps, asks at most two rounds of questions, and produces a machine-checkable `MISSION.md`.
- **Staged gates.** Work advances through G0–G7. Every gate requires evidence files; a failed gate means revision or a pause, never proceeding with a known gap.
- **Independent verification.** A separate read-only agent re-reads the canvas, runs netlist checks, and parses Gerber files instead of trusting the design agent's claims.
- **EDA execution.** Drives LCEDA Pro to build footprints, draw schematics, place and route, pour copper, add silkscreen, and export fabrication files.
- **Manufacturing deliverables.** Gerber, drill, BOM, pick-and-place, assembly notes, and inspection reports, written to `projects/<slug>/export/`.
- **Teaching lecture.** Generated only on explicit request: an overview plus module chapters covering operating principles, design rationale, layout and routing, and verification results.
- **Traceability.** Decision logs, gate states, and verification records are written to `logs/` and `reports/`, providing the source of truth for design, review, and lecturing.

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

`MISSION.md` is the single source of truth; gate states are recorded in `projects/<slug>/reports/gates.json`, and each gate has its own evidence files and inspection records.

<p align="center">
  <a href="docs/readme/images/app-conversation.png">
    <img src="docs/readme/images/app-conversation.png" alt="PCB-Wish conversation UI" width="780">
  </a>
</p>

<p align="center"><sub>Task list on the left; natural-language input, agent replies, and model selection on the right.</sub></p>

## Quick start

1. Windows 10/11 64-bit, LCEDA Pro (>= 4.1.60), and Python 3.10+.
2. Install `PCB-Wish-3.16.2-win-x64.exe`, or run the portable `PCB-Wish.exe`.
3. On first launch, enter an API key. The endpoint must be OpenAI-compatible; the model must support tool calling. A 1M context is recommended. Configuration stays on the local machine.
4. Select a workspace and describe the board in natural language; the system asks focused questions to close design gaps.
5. The design advances through G0–G7. Deliverables are written to `projects/<slug>/export/`, with the delivery note at `reports/DELIVERY_FINAL.md`.

Run the desktop app from source:

```bash
pnpm bootstrap
pnpm dev:desktop
```

## Test case: FlexiHeal v2

FlexiHeal v2 is the only complete test so far that ran from requirements to fabrication files and then to a teaching lecture. The task was a flexible bend-sensor angle measurement board: convert the sensor's 53–90 Ω resistance change into 0–3.3 V for an ADC, support USB/lithium battery power and charging, an OLED interface, 0°/90° calibration buttons, and a debug port, with hand soldering by the client.

<p align="center">
  <a href="docs/readme/images/board-pour.png">
    <img src="docs/readme/images/board-pour.png" alt="FlexiHeal v2 top copper pour" width="780">
  </a>
</p>

<p align="center"><sub>FlexiHeal v2 after top copper pour (red: top copper; blue: bottom copper; yellow: silkscreen and pads).</sub></p>

The case produced Gerber, drill, BOM, pick-and-place, assembly notes, inspection reports, and a 58-page lecture; the fabrication files were independently reviewed along two paths. The test used only the project's task book as the requirement input; it does not mean the result has been delivered or put into service.

## Teaching lecture

The teaching lecture is not a default deliverable. It enters the `MISSION.md` deliverable list only when the user explicitly asks for it during intake. Without an explicit request, the intake agent may ask whether one is needed; if it is not confirmed or selected, no lecture is generated and the delivery note records that. Once requested, the system generates the PDF after the board passes G6.

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
- Due to budget constraints, one full human-in-the-loop test (FlexiHeal v2) has been completed; a fully unattended run remains unverified. The lecture currently demonstrates only the default skill package; combinations with other open-source skill packages have not been shown.

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
