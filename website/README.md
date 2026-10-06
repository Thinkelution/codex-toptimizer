# Codex LeanTask public beta website

Static HTML, CSS, and JavaScript. No website runtime dependencies, analytics, forms or task execution endpoints. A separate write-only feedback service accepts explicit submissions from the local dashboard; see [feedback operations](../ops/feedback.md). Installation links point to the public repository's main branch. The workspace preview is illustrative content, not a model run.

Preview locally:

```bash
python3 -m http.server 8792 --bind 127.0.0.1 --directory website
```

Production: https://thinkelution.github.io/codex-toptimizer/ (GitHub Pages, no custom domain).

## Deployment

GitHub Pages serves this directory. `.github/workflows/pages.yml` publishes `website/` on every push to `main` that touches it (or from **Run workflow**); there is no build step. Roll back by reverting the commit. The site lives under `/codex-toptimizer/`, so links and asset paths must be relative (no leading `/`).

Pages can't send response headers, so each page carries its Content-Security-Policy and referrer policy in `<meta>` tags. Add both to any new page. Meta CSP can't set `frame-ancestors`, so framing protection isn't available on Pages.

The feedback receiver runs separately on AWS; see [feedback operations](../ops/feedback.md).

After publishing, verify the public HTTPS address, static assets, the alternate redirect, and an expected 404 for `/api/tasks`. The site has no connection to local/private tasks.

## Published case study

`/case-studies/pocket-ledger/` reports the September 21, 2026 app-creation comparison. The homepage navigation and evidence card link to it. It is one observed task pair, not a customer deployment, a causal attribution to compression, or a general savings claim.

The page includes a curated `results.json`, the exact task prompt, and a ZIP of the original starter with its 23 fixed tests and license files. Full execution transcripts and private task state are not public assets. Keep the retry, verification, caching, accounting and single-pair limitations adjacent to any usage-reduction claim. The original local experiment recorded source commit `7cb9ca66de09a79b33b89a5920e920cccbd88410` (LeanTask 0.3.0b6).

The page is static and has no script or runtime dependencies. Its responsive styling extends the existing brand through `assets/case-study.css`; the homepage uses `assets/site.css`. Merging to `main` publishes the complete `website/` directory.

The `#without-sandbox-retries` section adds an accounting-only subtotal: excluding two identified browser-retry requests (41,586 + 41,870 tokens) from the normal run leaves 379,221 tokens. All 13 per-request usage records reconcile with the measured final total. The selected records are included in `results.json`. This is not a measured failure-free run: it retains the first browser attempt, fallback tests, patch retry and later recorded context. Keep those assumptions alongside the estimate and its 61.02% numerical comparison; do not replace the original measured totals.

The homepage and case study describe recurring work on the same task as an opportunity for greater savings when tools avoid redundant context. This is an expected use case, not a measured finding from this pair or a guarantee. Preserve the qualifications about ordinary Codex continuation, growing history and tool overhead.
