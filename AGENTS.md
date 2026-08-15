# SKYWriter project guidance

## Mission

Build and validate iteration one of the SKYWriter app. Treat
`docs/ITERATION_ONE.G.md` as the current product brief and
`docs/DECISIONS.G.md` as the durable decision log.

## Before changing code

- Read `README.G.md`, `docs/ITERATION_ONE.G.md`, and `docs/DECISIONS.G.md`.
- Do not invent missing product requirements, target platforms, or technology
  choices. Record material unknowns and ask only when they block safe progress.
- Keep iteration one narrow. Prefer the smallest end-to-end user outcome over
  a broad but incomplete scaffold.
- Preserve user-authored and known-working files. New or materially co-authored
  files use `.G` immediately before the extension unless a tool requires a
  fixed filename, as with this file, `.gitignore`, or package manifests.

## Implementation agreements

- Never commit secrets. Use example environment files with placeholder values.
- Make small, reviewable changes and avoid unrelated rewrites.
- Add or update tests for behavior changes once a test framework is selected.
- Use accessible defaults and design explicitly for empty, loading, error, and
  success states in user-facing flows.
- Record consequential architecture, dependency, privacy, and scope decisions
  in `docs/DECISIONS.G.md`.
- Keep setup and verification commands current in `README.G.md`.

## Definition of done for a work item

- The requested behavior works for the agreed acceptance criteria.
- Relevant tests, linting, type checks, and builds pass when those commands
  exist.
- The final handoff states what changed, what was verified, and any remaining
  risks or decisions.
