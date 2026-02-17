# Database Migration Policy (Alembic)

Implementation Status: Complete (policy), Partial (examples)
Limitations / Known Issues: Squashing requires careful verification of both clean installs and upgrades; complex data migrations may need manual steps.
Security Vulnerabilities: None inherent; avoid destructive SQL in manual fixes.

## Purpose
Standardize how we manage database schema changes so that:
- New installs work from scratch with `alembic upgrade head`
- Development databases can be upgraded without wipes
- Each release clearly shows its schema delta via a single squashed migration

## Policy
- Development (pre-release):
  - Create standard Alembic revisions for each schema change; no DB wipes.
  - Prefer Alembic over manual SQL. Use manual SQL only when Alembic cannot express a required data fix; write → run → verify → delete.
- Release cut:
  - Squash all development revisions since the last release into a single migration using `replaces = ("rev1", "rev2", ...)`.
  - Tag the release. Keep Alembic able to set up from scratch at all times.
  - Verify both clean install and upgrade paths before archiving old revisions.


## Revision ID and Filename Policy
- Reserve numeric-only Alembic revision IDs for squashed releases only: "001", "002", "003", ...
- Squashed release filenames: 001_<release_name>_squash.py, 002_<release_name>_squash.py
- Development (pre-squash) migrations MUST NOT use bare numbers. Use prefixed, ordered IDs tied to the target release:
  - Example: r002_0001_add_tool_registry.py, r002_0002_seed_plugins.py
  - Set `revision = "r002_0001"`, `down_revision = "r002_0000"` (or prior dev head)
- Rationale: avoids conflicts with release revisions and prevents duplicate numeric prefixes in filenames.
- Code review checklist: reject dev migrations whose `revision` is numeric-only.

## Post-squash operational guidance
- New installs: `alembic upgrade head` must succeed from an empty database.
- Deployed DBs at pre-squash heads should be aligned by stamping to the squashed release as needed.
  - Example for this release: instances at legacy 006 will be set to 001; then `alembic upgrade head` applies 002.
  - Local dev DB (already at 017) should be `alembic stamp 002` after the squash lands.
- Always verify both paths locally before removing replaced dev revisions:
  1) Clean DB -> `alembic upgrade head`
  2) DB at pre-squash dev head -> `alembic stamp 002` (or appropriate) -> application sanity checks

## Implementation note for this release
- 001 = first release squash (formerly 006_alpha_squash.py; revision set to "001")
- 002 = second release squash replacing 007..017; includes net schema and seeds; drops legacy tables and FKs as they no longer exist in the final schema.

## Squash Procedure
1. List dev revisions to squash (from last release head to current head).
2. Generate a new migration: `alembic revision -m "rX_Y_Z squashed"` and add `replaces` with the listed revs.
3. Implement `upgrade()` as the net schema; `downgrade()` should revert to pre-release state if feasible.
4. **Verify no `replaces` collisions**: No entry in any migration's `replaces` tuple may match another migration file's `revision` ID. Alembic treats `replaces` entries as aliases for the replacing migration, which shadows the real migration and breaks the chain. Run the verification script:
   ```bash
   cd backend && PYTHONPATH=. python3 -c "
   from alembic.config import Config
   from alembic.script import ScriptDirectory
   cfg = Config('alembic.ini')
   cfg.set_main_option('script_location', 'migrations')
   sd = ScriptDirectory.from_config(cfg)
   revs = {s.revision for s in sd.walk_revisions()}
   for s in sd.walk_revisions():
       for r in getattr(s.module, 'replaces', ()):
           if r in revs and r != s.revision:
               print(f'COLLISION: {s.revision}.replaces contains \"{r}\" which is a real migration')
   print('No collisions found' if True else '')
   "
   ```
5. Verify migration paths:
   - Clean DB → `alembic upgrade head` passes
   - DB at last dev head → `alembic upgrade head` passes
   - `alembic heads` shows exactly one head (the new squash revision)
6. After validation, archive/remove replaced revisions if desired.
7. **Cleanup stale `replaces`**: Once all deployed environments have been upgraded past a squash migration's replaced revisions, clear its `replaces` tuple to `()`. Stale entries are a latent collision risk as new release squash IDs are allocated.

## Verification & CI
- Include a CI job that provisions a clean ephemeral DB and runs `alembic upgrade head`.
- Add integration tests to exercise code paths affected by schema changes.

## Manual SQL Updates (Exception Path)
- Only when Alembic cannot express the change.
- Place transient scripts in `scripts/db/one_off/` (git-ignored).
- Use explicit, non-destructive statements; add comments noting expected error output for re-runs.
- Process: write → run → verify → delete.

## Pointers
- See `.augment/rules/shu-base-rules.md` for the concise rules that LLMs must follow.
- See `docs/policies/DEVELOPMENT_STANDARDS.md` for engineering expectations.
