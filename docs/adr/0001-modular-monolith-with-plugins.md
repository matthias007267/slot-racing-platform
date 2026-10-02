# 0001 Modular monolith with plugins instead of services

## Context
Features (cameras, Raspberry Pi, audio, statistics, ...) must be developed, enabled, disabled,
replaced or removed independently. The application is a single desktop program for Windows.

## Decision
One Python package (`carrera`) with a small core and feature modules under `carrera.modules`.
Each module is a plugin with a manifest, lifecycle and contributions. Modules never import each
other; they communicate through core event types and core service interfaces.
There are no separate processes or services. Only the Raspberry Pi agent is a separate program
because it runs on different hardware.

## Consequences
- Simple deployment and debugging, no IPC.
- Independence is a convention that has to be enforced; see ADR 0005.
- A module can later be extracted into its own distribution because it only talks to the core.
