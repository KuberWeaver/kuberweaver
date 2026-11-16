# Autopilot rules

This repository is worked on unattended, one bounded iteration at a time. These
rules are the contract for every iteration. Breaking one is worse than doing
nothing.

## Scope

1. Work only on the task the state file hands you. Do not start another task, do
   not refactor unrelated code, do not "improve" things you noticed in passing.
2. One task per iteration. If the task is too large for one iteration, split it
   in the state file and do only the first part.
3. The backlog in `STATE.json` is the single source of intent. If you believe a
   task is wrong, mark it `blocked` with a reason instead of silently changing it.

## Safety

4. Never touch `main` directly. `main` moves only by merging a green `develop`.
5. Never force-push, never rewrite published history, never delete a branch you
   did not create in this iteration.
6. Never commit secrets. `data/`, `docs/prompt/`, private keys, credentials and
   vault material stay out of Git. Run the secret scan before every push.
7. Never run destructive commands against infrastructure: no VM deletion, no
   database drop, no vault wipe. Those are product features, implemented behind
   approval, not ad-hoc commands.
8. No network installs that change the host system. Use the project virtualenv.

## Quality

9. Every change ships with tests. A task is not done when the code compiles, it
   is done when a test proves the behaviour, including the refusal cases.
10. The full backend suite must pass, and the frontend must typecheck and build,
    before a commit is merged into `develop`.
11. No comments that narrate the process. Comments explain why a decision was
    made, in the present tense, and nothing else.
12. Public documentation is in English. Commit messages are short and factual.

## Commit and branch protocol

13. Branch name: `feat/<task-id>-<slug>`, created from `develop`.
14. Commit subjects: `feat(<area>): <what>`, `fix(<area>): <what>`,
    `test(<area>): <what>`, `docs(<area>): <what>`, `chore: <what>`.
15. Push the feature branch, then merge into `develop` with `--no-ff` once green,
    then push `develop`.
16. Update `STATE.json` in the same iteration, in its own commit.

## Stopping

17. After three consecutive failures on the same task, mark it `blocked` with the
    reason, leave the tree clean, and stop. Do not thrash.
18. If a test failure is pre-existing and unrelated, record it in `STATE.json`
    under `known_failures` and continue. Do not fix it inside another task.
19. Leave the working tree clean at the end of every iteration. No half-applied
    edits, no stray files.
20. If you cannot finish the task, revert your branch, mark it `partial` with what
    remains, and stop. A clean tree beats a half feature.

## Reporting

21. The final message of an iteration states: task id, what changed, test result,
    commit sha, branch pushed, and what the next task is. Two or three lines, no
    narrative.
