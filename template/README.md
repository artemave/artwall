# Your artwall gallery

This repo *is* `~/.local/share/artwall` — clone it there (or move an existing
one's contents in) and [artwall](https://github.com/artemave/artwall)
uses it as-is. `.trash/` is gitignored, so unstarring a painting never
gets pushed; everything else — `stars.json`, `images/`, `stars.html` and the
published `public/` site — does.

One-time setup, after creating this repo from the template:

1. Clone it to `~/.local/share/artwall`.
2. In the repo's Settings → Pages, set Source to "GitHub Actions".

From then on, clicking **⇪ Sync** on the gallery page commits and pushes
whatever changed, and the included workflow deploys `public/` to GitHub
Pages — no terminal needed after the initial clone.
