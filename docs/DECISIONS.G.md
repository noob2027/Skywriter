# SKYWriter decision log

Use this file for decisions that affect scope, architecture, dependencies,
privacy, delivery, or future work. Preserve superseded entries and link to the
replacement decision.

## D-001 — Initialize a dedicated repository

- Date: 2026-08-15
- Status: Accepted
- Decision: Use the private `noob2027/Skywriter` GitHub repository, with a
  matching local checkout and `main` as the default branch.
- Reason: Preserves the existing GitHub history, isolates SKYWriter work, and
  gives Codex a clear project root.

## D-002 — Preserve the GitHub connection trial

- Date: 2026-08-15
- Status: Superseded by D-004 on 2026-08-15
- Decision: Keep the existing `README.md` and `Teensy40_HelloWorld/` trial
  unchanged while preparing iteration one.
- Reason: Those files verify the GitHub connector and form the current remote
  baseline; preparation work should not rewrite known history.

## D-003 — Defer application scaffolding until the brief is captured

- Date: 2026-08-15
- Status: Accepted for preparation
- Decision: Do not select a framework or generate application code until the
  prior SKYWriter discussion is transferred into the iteration-one brief.
- Reason: Platform, core flow, integrations, and acceptance criteria are not
  present in the current workspace; guessing would create avoidable rework.

## D-004 — Remove the GitHub connection trial

- Date: 2026-08-15
- Status: Accepted
- Decision: Delete `Teensy40_HelloWorld/` from the current project and keep
  `README.md` unchanged.
- Reason: The repository connection test is complete, and the project owner
  confirmed that the Hello World trial is no longer needed.
- Consequence: The trial remains recoverable from Git history but is no longer
  part of the iteration-one working tree.
- Supersedes: D-002

## Decision template

### D-XXX — Short title

- Date: YYYY-MM-DD
- Status: Proposed | Accepted | Superseded
- Decision:
- Reason:
- Consequences:
- Supersedes / superseded by:
