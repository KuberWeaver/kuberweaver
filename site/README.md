# KuberWeaver static site

Static showcase page: plain HTML, CSS and a few lines of vanilla JavaScript.

## Serve locally

From the repository root run `python3 -m http.server 8000`, then open
`http://127.0.0.1:8000/site/`. Opening `site/index.html` directly in a browser
also works. Links to repository documents are relative (`../CONTRIBUTING.md`),
so serve from the repository root rather than from inside `site/`.

## Deploy on GitHub Pages

Commit `site/`, then in repository Settings > Pages choose "Deploy from a
branch", branch `main`, folder `/site`. No build step and no dependencies.

## Notes

No CDN, web fonts, tracker or analytics. The single absolute URL is the
repository link in the footer. Content is in English and the boundary table
is copied from the project README.
