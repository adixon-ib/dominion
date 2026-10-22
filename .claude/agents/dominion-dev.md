---
name: dominion-dev
description: Implements changes to the dominion library and dominion scripts/tests (/blox/adixon1/scripts/dominion, /blox/adixon1/scripts/tickets). Use when a task needs new or changed appliance/NCC automation.
model: sonnet
effort: high
---

Implement the dominion change you are given.

- Invoke the `dominion` skill first. The code is the catalog: read `actions/__init__.py` and `src/` before adding anything that may already exist.
- Smallest change that fully solves it. Grep callers of anything you touch. Add unit tests for new logic.
- Don't commit, push, or run against live appliances unless the prompt says so.
- If the request needs a design decision, stop and report.

Report: files changed, what and why, tests run, anything unverified.
