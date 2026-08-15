# Task 001 — repository foundation

## Goal

Create a tested Windows-ready Python/PySide6 application foundation with stable module boundaries and no mission, map, MAVLink, telemetry, or flight behavior.

## Base and ownership

Base: documentation-only `main`.

May create/edit: `pyproject.toml`, dependency lock, `.gitignore`, `.github/workflows/`, `src/skywriter/` foundation shells, `tests/` foundation tests, developer sections of `README.md`, and minimal packaging/config files. Do not rewrite product/architecture decisions.

## Required work

- Configure supported Python version, PySide6, pytest, formatter, linter, type checker, and exact dependency lock.
- Create `domain`, `application`, `infrastructure`, and `ui` packages matching the architecture.
- Add an application entry point and a main window with clearly labeled placeholder Builder, Preflight, and Flight views.
- Add typed base error/result patterns, logging configuration, and an immutable placeholder application snapshot/event reducer.
- Keep optional/heavy vehicle or WebEngine imports out of domain/application modules.
- Add CI for install, format check, lint/type check, and tests on Windows (plus another OS only if cheap).
- Document clean Windows setup, run, test, and troubleshooting steps.

## Explicit exclusions

No mission classes/validation/compiler, JSON mission files, Leaflet/WebEngine map, serial discovery, pymavlink, SITL, telemetry, commands, parameter access, flight logic, or safety limits.

## Tests and acceptance

- Clean environment can install, import every package, launch/close the shell, and run all checks.
- Dependency-direction test or equivalent proves domain does not import UI/infrastructure.
- No circular imports and no prohibited feature strings/APIs outside documentation.
- CI is green and README steps are repeatable.

## Handoff and stop

Report changed files, dependency versions/rationale, checks run/results, Windows assumptions, and proposed exact shared contracts for maintainer review. Open a PR and stop; do not implement Task 002 or later.
