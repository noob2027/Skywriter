# Start SKYWriter iteration one with Codex

## Ready now

- Local repository matching `noob2027/Skywriter`
- `main` branch tracking `origin/main`
- Existing GitHub history preserved locally
- Repository-local Git author identity based on the verified GitHub history
- Connected GitHub app with read/write/admin repository access
- Repository-level Codex instructions
- Product brief, decision log, and secret-safe ignore rules

## Required before implementation

1. Transfer the prior SKYWriter discussion into `ITERATION_ONE.G.md`.
2. Confirm the primary user, core flow, platform, and iteration-one acceptance
   criteria.
3. Confirm the preferred technology stack or explicitly authorize Codex to
   recommend one from the requirements.
4. Identify any designs, assets, APIs, credentials, or existing code that must
   be supplied. Put real secrets only in ignored local environment files.
## Publishing note

Codex can read and write `noob2027/Skywriter` through the connected GitHub app.
The local sandbox cannot currently read Windows Credential Manager, so direct
command-line HTTPS pushes may require authentication outside the sandbox. This
does not block local development or connector-backed GitHub work.

## Suggested first implementation request

> Read `AGENTS.md`, `README.G.md`, and the files in `docs/`. Review the
> iteration-one brief for unresolved blockers. If it is implementation-ready,
> propose the smallest end-to-end vertical slice, implement it, run the
> relevant checks, and report what works and what remains. Do not expand the
> agreed scope or add production dependencies without explaining the need.

## Expected first milestone

The first milestone should produce one demonstrable user outcome, not merely a
large framework scaffold. It should include a runnable path, basic automated
verification, documented setup commands, and explicit known limitations.
