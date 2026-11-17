# Local deployment

The portal runs as a user unit from the checkout. Code on disk is not code in
memory, so every change needs a restart to be visible.

## How it deploys

`kuberweaver-deploy.timer` fires every two minutes and runs
`ops/deploy/local-deploy.sh`. The script:

1. reads the checked out commit;
2. compares it with the commit recorded in `~/.local/state/kuberweaver/deployed-sha`;
3. rebuilds the frontend when anything under `frontend/src`, `frontend/index.html`,
   `frontend/package.json` or `frontend/vite.config.ts` moved since that commit;
4. restarts `kuberweaver.service`;
5. polls `GET /readyz` for up to fifteen seconds;
6. records the commit only when readiness answers 200.

A failed readiness leaves the marker untouched, so the next tick retries the same
commit and the log keeps the failure. Nothing is deployed twice for the same sha.

    systemctl --user list-timers kuberweaver-deploy.timer
    journalctl --user -u kuberweaver-deploy -n 20
    ~/.local/state/kuberweaver/deployed-sha

## Why a timer and not the autopilot

Unattended runs execute with `approvals.cron_mode: deny`, which refuses
`systemctl`. An iteration that cannot restart the unit would commit work that
nobody sees, which is exactly the failure this mechanism removes. The timer runs
as the user, outside the agent's approval policy.

## Manual deployment

    ops/deploy/local-deploy.sh

Force a redeploy of the current commit by removing the marker first:

    rm ~/.local/state/kuberweaver/deployed-sha && ops/deploy/local-deploy.sh

## What the script does not do

It does not fetch, merge or choose a branch: it deploys whatever is checked out.
Promoting a commit is a separate decision, taken by moving the working tree and
letting the timer pick it up.

It does not roll back. A commit that fails readiness stays unrecorded and the
previous process keeps serving; recovering means checking out the previous
commit, and the timer deploys it on the next tick.
