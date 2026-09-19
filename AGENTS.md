# Project development workflow

Use [Superpowers](https://github.com/obra/superpowers) for development in this repository. Read and follow the applicable installed skills: brainstorm and agree the design before implementation, plan multi-step changes, use systematic debugging for failures, and verify changes before reporting completion. Apply testing and review appropriate to the change. Existing user instructions and approvals remain in effect.

## Worktrees and branches

- Do development in a Git worktree under `.worktrees/` inside the original repository directory. Keep the main checkout available for syncing and creating worktrees.
- Give every worktree its own named task branch. Use descriptive branch names; Codex-created branches use `codex/<task>`.
- If already in the appropriate worktree, continue there. Do not create nested worktrees. Find the original repository with `git worktree list` when needed.
- Ensure `.worktrees/` is ignored before creating a worktree. Preserve unrelated local edits when moving work between checkouts.
- Fetch `origin` before starting a new task and normally branch from `origin/main`, unless the task depends on another branch.
- Run all edits, setup and verification commands in the chosen worktree.

## Remote visibility

- Always publish each task branch to `origin` with upstream tracking (`git push -u origin <branch>`). Push it when created, then push subsequent commits at meaningful checkpoints and before handing work back so colleagues can see progress.
- Commit the intended changes after the appropriate checks; unfinished work may be explicitly labelled as a work-in-progress checkpoint, with verification status reported accurately.
- The user has authorised routine task-branch commits and pushes; do not ask for that permission again. This does not authorise merging into `main`, force-pushing shared history, or deleting colleagues' branches.
- If a push fails, report the failure and retain the local branch and worktree. Do not claim remote visibility without checking it.
- At handoff, include the branch link, worktree path, and verification result. Keep the worktree available for continued work.

## Project context

`readme.md` is the MVP scope and the shared interface reference (section 5 is the location snapshot contract; `CONTRACTS.md` restates it as module APIs). `PLAN.md` is the earlier, broader design and is superseded where they differ. Risk assessment produces location snapshots; analyst coordination consumes them to produce priorities, recommendations and team work items. Preserve that boundary and update the documented contract when it changes.
