# Navigation and page states

The portal declares its pages once, in `frontend/src/navigation.ts`. The sidebar,
the breadcrumb, the page heading and the role filter all read that registry, so a
route, a label or a heading is never duplicated.

## Groups

The sidebar is grouped by what the operator is doing:

| Group | Pages |
| --- | --- |
| Build | Canvas, Catalogue |
| Operate | Deployments, Infrastructure, Connections, Keys |
| Govern | Governance, FinOps, Diagnostics, Audit |
| Administer | Integrations, Operations, Agent access |

Group captions replace the former separator class, and the decorative workspace
switcher was removed because it did not switch anything.

## Roles

`navigationFor(role)` returns the groups visible to a role. Administrators see
all four groups. Operators and viewers see Build, Operate, Govern and the
Integrations status page; `Operations` and `Agent access` are administration
pages and are absent from their sidebar. A direct hash still resolves to the
page, and the page keeps its own refusal for non-administrators. Hiding a link
is navigation hygiene, not the authorization boundary.

## Page registry

Each `PageDefinition` carries `id`, `label`, `group`, `adminOnly`, `eyebrow`,
`title` and `description`. `pageHeading(id)` returns the three copy fields for
`PageTitle`, which keeps the heading and the breadcrumb in step.

To add a page:

1. add the id to `pageIds` and a `PageDefinition` to `pages`;
2. map the id to an icon in `pageIcons` in `App.tsx`;
3. add a render branch in `Workspace`;
4. run the tests and accept the registry snapshot on purpose.

`navigation.test.ts` snapshots the complete registry and the grouped navigation
for the three roles, checks that every id is registered once and that pages are
ordered by group, and covers unknown addresses. `English.test.ts` scans the
registry copy, so a French or prototype string fails the build.

## Routing

The hash is the route. An empty hash opens `canvas`, an unknown hash shows a
`Page not found` empty state with a link back to the canvas, and the sidebar
marks the current page with `aria-current="page"`.

## Loading and empty states

`ResourceBoundary` owns the two transient states:

- loading renders `LoadingState`, a `role="status"` region with `aria-busy`,
  the `Loading…` label and three skeleton rows;
- failure renders `ErrorNotice` with a retry button.

`Empty` takes a title, an explanation, an optional icon and an optional action.
Every page that lists data has an empty state: deployments link back to the
canvas, keys explain how to generate the first key, agent access explains that
the secret is shown once, and diagnostics states that an analysis which has not
run does not prove the absence of findings.
