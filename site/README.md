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

Commit `site/`, then in repository Settings > Pages choose "Deploy from a
branch", branch `main`, folder `/site`. No build step and no dependencies.

## Notes

No CDN, web fonts, tracker or analytics. The only external references are two
links to the project repository: the `CONTRIBUTING.md` link and the footer
repository link. Content is in English and the boundary table is copied from the
project README.
