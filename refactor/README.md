# Refactor workspace

This directory will house the refactor of `examples/plotlydash-basic`. Follow the existing AGENTS instructions: promote clean structure, reuse components, keep diffs minimal, and add tests/coverage as soon as logic changes. The current work-in-progress files (from the base example) remain in `examples/plotlydash-basic` so the refactor can start from a clean slate here. Any design notes, new layout files, or prototype modules should live in this folder to keep refactor work isolated.

## Next steps
1. Decide what portions of the current dashboard to carry into the refactor (layout, navigation, dqchecks wiring, assets).
2. Sketch the desired new architecture (componentized layouts, shared utility modules, test harness).
3. Slowly reimplement the dashboard within this folder, referencing the original code rather than modifying it directly.
