# Documentation site

The developer-first documentation is a static GitHub Pages site in `site/`. It
extends the dashboard's Evidence Docket visual language and links to the
repository's detailed source documents. It does not run the API, worker, or
operator dashboard.

## Local preview

From the repository root:

```sh
python .github/scripts/check_site.py
python -m http.server 8125 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8125/`. The checker verifies local files and fragment
links. It does not validate external URLs or certify browser accessibility.

## GitHub Pages deployment

1. In repository Settings → Pages, select **GitHub Actions** as the build and
   deployment source. The repository owner must have Pages available for the
   repository visibility and plan.
2. Merge `site/`, `.github/workflows/pages.yml`, and the checker into `main`.
   The workflow validates pull requests, then deploys the static `site/`
   artifact on a `main` push or an authorized manual run from `main`.
3. The `github-pages` environment records the resulting URL. Verify the live
   home page and anchor navigation after the first deployment.

No credentials, source captures, model files, or operator data belong in
`site/`. The site uses local CSS and system fonts, with no CDN dependency.
Publishing this site does not unblock the separately gated Python/container
release or turn the alpha into a public SaaS.
