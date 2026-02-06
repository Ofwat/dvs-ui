# AGENTS instructions

These instructions apply to all contributors working in this repository. They are tuned toward the Plotly Dash dashboard pattern used in `examples/plotlydash-basic`.

## Core principles
- Prioritize clean, idiomatic Python and Dash code; favor readability and consistent formatting.
- Maintain or raise test coverage whenever feasible; add or update tests when touching logic that affects behavior.
- Keep diffs minimal and focused: prefer small logical changes and avoid linking unrelated updates together.
- Before adding new files or significant complexity, explore alternatives and confirm the need with the requester.
- Reuse existing components, helpers, and styles rather than duplicating logic; aim for a clear modular structure.
- Dash-specific: organize callbacks and layout so that the dashboard feels cohesive and easy to reason about.

## Workflow guidance
1. Understand the current dashboard structure and reuse shared utilities before inventing new ones.
2. When a change raises doubts about scope or test strategy, pause and communicate rather than proceeding blindly.
3. Document any deviations from these rules in a short note alongside the change (e.g., in the PR description).

All edits should adhere to these rules unless an explicit exception is requested.
