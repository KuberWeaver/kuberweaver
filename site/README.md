# KuberWeaver static site

Static showcase page: plain HTML, CSS and a few lines of vanilla JavaScript.

## Serve locally

From the repository root run `python3 -m http.server 8000`, then open
`http://127.0.0.1:8000/site/`. Opening `site/index.html` directly in a browser
also works.

Links to repository documents are absolute GitHub URLs, not relative paths. That
is deliberate: deploying `/site` at a domain root (GitHub Pages) would resolve a
relative link against the published path and produce a 404. There is no local
asset outside `site/`, so serving from anywhere works.

## Deploy on GitHub Pages

Two steps, both human: enable Pages in repository Settings > Pages (GitHub Pages is
free on a public repository, and requires a paid plan for a private one), then run
the workflow by hand from Actions > "Deploy showcase site" > Run workflow. The
workflow is manual on purpose: on a private repository without Pages enabled,
`actions/configure-pages` fails on every push that touches `site/`.

There is no build step and no runtime dependency; the published path is `/site`.

## Notes

No CDN, web fonts, tracker or analytics. The only external references are two
links to the project repository: the `CONTRIBUTING.md` link and the footer
repository link. Content is in English and the boundary table is copied from the
project README.
