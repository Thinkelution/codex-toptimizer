# Codex LeanTask public beta website

Static HTML, CSS, and JavaScript. No website runtime dependencies, analytics, forms or task execution endpoints. A separate write-only feedback service accepts explicit submissions from the local dashboard; see [feedback operations](../ops/feedback.md). Installation links point to the public repository's main branch. The workspace preview is illustrative content, not a model run.

Preview locally:

```bash
python3 -m http.server 8792 --bind 127.0.0.1 --directory website
```

Production: https://codex-lean-task.thinkelution.com/ . The alternate https://codex-toptimizer.thinkelution.com/ redirects to it.

## Deployment

Cloudflare can remain proxied. Nginx serves only this directory from `/var/www/tasklean-site/current`, a symlink into versioned `releases/` directories. Private app releases and task state stay under the administrator's private home directory and are not web roots. Do not expose `tasklean ui` through this virtual host.

The host-specific configuration is in `ops/tasklean-site.nginx.conf`. Test with `sudo nginx -t` before reloading. Copy only public site assets to a new release, then replace the current symlink atomically. Roll back by pointing the symlink at the previous release and verifying responses. No app process restart is needed for static changes.

The existing Certbot account issued a certificate covering both hostnames using HTTP webroot validation at `/var/www/tasklean-acme`. The port 80 challenge exception remains available while normal HTTP redirects to HTTPS. `certbot.timer` is enabled, and `ops/renew-tasklean-tls.sh` reloads Nginx after renewal of this certificate. Cloudflare proxying does not need to be disabled for this setup.

Verify the origin with `curl --resolve codex-lean-task.thinkelution.com:443:127.0.0.1 https://codex-lean-task.thinkelution.com/` on the server, then verify the normal public HTTPS address, static assets, alternate redirect and an expected 404 for `/api/tasks`. The site has a restrictive CSP and no connection to local/private tasks. Only POST /api/feedback is proxied to the private feedback receiver.

## Published case study

`/case-studies/pocket-ledger/` reports the September 21, 2026 app-creation comparison. The homepage navigation and evidence card link to it. It is one observed task pair, not a customer deployment, a causal attribution to compression, or a general savings claim.

The page includes a curated `results.json`, the exact task prompt, and a ZIP of the original starter with its 23 fixed tests and license files. Full execution transcripts and private task state are not public assets. Keep the retry, verification, caching, accounting and single-pair limitations adjacent to any usage-reduction claim. The original local experiment recorded source commit `7cb9ca66de09a79b33b89a5920e920cccbd88410` (LeanTask 0.3.0b6).

The page is static and has no script or runtime dependencies. Its responsive styling extends the existing brand through `assets/case-study.css`; the homepage uses `assets/site.css`. Deploy the complete `website/` directory into a fresh versioned release and switch the existing `current` symlink only after verifying the files.
