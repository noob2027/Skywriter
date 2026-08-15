# SKYWriter

SKYWriter is prepared for iteration-one development with Codex.

## Current status

The local repository matches the private GitHub repository
`noob2027/Skywriter` on `main`, and Codex working agreements are ready. The
existing Teensy 4.0 Hello World trial is preserved as the current baseline.

The SKYWriter product scope and technology stack still need to be transferred
from the prior discussion before application code is scaffolded.

## Repository map

- `AGENTS.md` — durable instructions Codex reads before working.
- `README.md` — existing repository note, preserved unchanged.
- `Teensy40_HelloWorld/` — existing GitHub connection trial, preserved
  unchanged.
- `docs/ITERATION_ONE.G.md` — iteration-one product brief and acceptance
  criteria.
- `docs/DECISIONS.G.md` — durable product and engineering decisions.
- `docs/CODEX_START.G.md` — startup checklist and a first implementation prompt.

## Start with Codex

1. Open this local repository as the Codex workspace.
2. Transfer the agreed SKYWriter details into `docs/ITERATION_ONE.G.md`.
3. Resolve the blocking decisions listed in `docs/CODEX_START.G.md`.
4. Ask Codex to implement the first thin, end-to-end slice and verify it.

There are no application install, run, or test commands yet because the app
stack has not been confirmed. The existing Teensy trial can be opened directly
in Arduino IDE with Teensy 4.0 selected.
