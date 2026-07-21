# ADR-004 — Versioned Contracts

## Status
Accepted (Freeze TZ P0/P4; amended Master Spec Part 1 Foundations v6.0)

## Context
Translation and Dub contracts must evolve independently without silent breakage.
Part 1 requires a full layer contract set.

## Decision
Stamp the following at LOCK (and via `stamp_contract_versions`):

| Key | Version |
|-----|---------|
| `recognition_contract_version` | 1 |
| `sentence_contract_version` | 1 |
| `translation_contract_version` | 1 |
| `dub_contract_version` | 1 |
| `scheduler_contract_version` | 1 |
| `alignment_contract_version` | 1 |
| `merge_contract_version` | 1 |
| `studio_contract_version` | 1 |
| `tts_contract_version` | 1 |

Mismatch raises `ContractVersionError`.
`require_contract_versions(..., full=True)` enforces the complete Part 1 set;
default require keeps legacy five keys for older callers.
Metadata (owner, description, compatibility, migration) is available via
`contract_catalog()`.

## Consequences
Future contract bumps require an explicit migration task/PR and ADR update.
See ADR-012.
