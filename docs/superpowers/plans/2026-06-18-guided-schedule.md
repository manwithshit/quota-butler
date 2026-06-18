# Guided Schedule Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct schedule generation with a versioned guided preference flow and a human-readable, adoptable V2 plan card.

**Architecture:** Add a pure `schedule_flow` module for preference validation and callback payload construction. Keep provider checks and callback orchestration in `handler`, planning math in `planner`, and Feishu JSON rendering in `notify`; all new behavior is introduced through red-green TDD cycles.

**Tech Stack:** Python 3 standard library, `unittest`, Feishu Card JSON 2.0, existing private bridge `cmd: quota` callback contract.

---

### Task 1: Block legacy plans

**Files:**
- Modify: `tests/test_handler.py`
- Modify: `quota_butler/handler.py`
- Modify: `quota_butler/plan_tasks.py`

- [ ] Add a failing handler test proving `adopt_schedule` rejects a structurally valid plan without `plan_version: 2`.
- [ ] Run `python3 -m unittest tests.test_handler.TestHandler.test_adopt_schedule_rejects_legacy_plan -v`; expect failure because the current handler accepts the plan.
- [ ] Add the minimum version validation and a clear “旧计划已失效，请重新规划” receipt.
- [ ] Re-run the focused test and existing adoption tests; expect pass.
- [ ] Commit with `fix: block legacy schedule adoption`.

### Task 2: Add pure guided-flow preferences

**Files:**
- Create: `quota_butler/schedule_flow.py`
- Create: `tests/test_schedule_flow.py`

- [ ] Add failing tests for task-type normalization, intensity normalization, default 09:00–17:00 preferences, callback payload preservation, target-date validation, and the 16-hour time boundary.
- [ ] Run `python3 -m unittest tests.test_schedule_flow -v`; expect import failure.
- [ ] Implement immutable `SchedulePreferences`, enum-like mappings, `flow_payload`, `parse_preferences`, and `validate_work_time`.
- [ ] Re-run the focused suite; expect pass.
- [ ] Commit with `feat: add guided schedule preferences`.

### Task 3: Render the guided cards

**Files:**
- Modify: `quota_butler/notify.py`
- Modify: `tests/test_notify.py`

- [ ] Add failing tests for task-type card buttons, intensity card buttons, two `picker_time` fields inside a form container, and the preference summary edit actions.
- [ ] Run the focused notify tests; expect missing builder failures.
- [ ] Implement small pure builders for each guided card, preserving `flow_version`, target date, and accumulated preferences in every callback.
- [ ] Re-run the focused tests; expect pass.
- [ ] Commit with `feat: render guided schedule cards`.

### Task 4: Route the guided callbacks

**Files:**
- Modify: `quota_butler/handler.py`
- Modify: `tests/test_handler.py`

- [ ] Add failing tests proving `schedule_intent` sends the task card, each selection advances one step, edit actions return to the chosen step, invalid/expired flow cards are rejected, and invalid time returns the time card with an error.
- [ ] Run the focused handler tests; expect failures because direct planning still occurs.
- [ ] Implement `schedule_flow` callback orchestration without writing preferences to persistent state.
- [ ] Re-run focused handler tests; expect pass.
- [ ] Commit with `feat: add guided schedule callback flow`.

### Task 5: Generate plans from structured preferences

**Files:**
- Modify: `quota_butler/planner.py`
- Modify: `tests/test_planner.py`

- [ ] Add failing tests for all three intensity levels, deterministic relay counts, `plan_version: 2`, retained preferences, and task-specific event explanations.
- [ ] Run `python3 -m unittest tests.test_planner -v`; expect failures for the missing structured API.
- [ ] Extend the planner minimally so intensity controls relay spacing and task type controls human-readable notes while retaining coverage calculations.
- [ ] Re-run planner tests; expect pass.
- [ ] Commit with `feat: plan from guided preferences`.

### Task 6: Auto-select available Agents at generation time

**Files:**
- Modify: `quota_butler/handler.py`
- Modify: `tests/test_handler.py`

- [ ] Add failing tests for Codex-only, Claude-only, dual-Agent, and no-Agent generation paths.
- [ ] Run the focused tests; expect failures because generation does not yet consume the guided summary.
- [ ] Generate with all currently available Agents; suppress individual failure warnings and return only the no-Agent message when both fail.
- [ ] Re-run focused tests; expect pass.
- [ ] Commit with `feat: auto-select schedule agents`.

### Task 7: Render the human-readable plan card

**Files:**
- Modify: `quota_butler/notify.py`
- Modify: `tests/test_notify.py`

- [ ] Add failing tests asserting the card omits `CAS`, contains work hours, actual Agents, coverage percentage, estimated gap, relay count, uncertainty note, task-purpose timeline, and exactly the three required actions.
- [ ] Run focused notify tests; expect failures against the current technical plan card.
- [ ] Replace the schedule-card body with the human-readable summary and versioned action payloads; make “调整设置” return to the summary step.
- [ ] Re-run focused tests; expect pass.
- [ ] Commit with `feat: humanize schedule plan cards`.

### Task 8: Update CLI and documentation

**Files:**
- Modify: `quota_butler/schedule.py`
- Modify: `tests/test_schedule.py`
- Modify: `docs/PRD_V2_SCHEDULER.md`
- Modify: `docs/DEV_PLAN_V2.md`
- Modify: `docs/TEST_PLAN_V2.md`

- [ ] Add a failing CLI test proving dry-run uses V2 default preferences and does not print the `CAS` abbreviation.
- [ ] Run `python3 -m unittest tests.test_schedule -v`; expect failure.
- [ ] Update the CLI summary and project docs to describe the guided flow and legacy-plan block.
- [ ] Re-run focused tests; expect pass.
- [ ] Commit with `docs: document guided schedule flow`.

### Task 9: Full verification

**Files:**
- No production edits unless a failing regression requires another red-green cycle.

- [ ] Run `python3 -m unittest discover -s tests -v`; require zero failures.
- [ ] Run `python3 -m quota_butler.schedule --dry-run --intent '帮我安排明天'`; inspect that the JSON is a V2 human-readable card and no real warmup occurs.
- [ ] Review `git diff` for secret material, bridge service changes, listener additions, and accidental Claude warmup paths.
- [ ] Confirm no App Secret appears in tracked changes with targeted secret-name searches, without reading credential stores.
- [ ] Prepare the existing single-listener group-test checklist; do not execute live adoption until the user reviews the new dry-run card.
