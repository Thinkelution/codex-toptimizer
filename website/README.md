# Codex LeanTask public beta website

Static HTML, CSS, and JavaScript. No runtime dependencies, analytics, forms or task execution endpoints. Installation links point to the public repository's main branch. The workspace preview is illustrative content, not a model run.

Preview locally:

```bash
python3 -m http.server 8792 --bind 127.0.0.1 --directory website
```

Production: https://codex-lean-task.thinkelution.com/ . The alternate https://codex-toptimizer.thinkelution.com/ redirects to it.

## Deployment

Cloudflare can remain proxied. Nginx serves only this directory from `/var/www/tasklean-site/current`, a symlink into versioned `releases/` directories. Private app releases and task state stay under the administrator's private home directory and are not web roots. Do not expose `tasklean ui` through this virtual host.

The host-specific configuration is in `ops/tasklean-site.nginx.conf`. Test with `sudo nginx -t` before reloading. Copy only public site assets to a new release, then replace the current symlink atomically. Roll back by pointing the symlink at the previous release and verifying responses. No app process restart is needed for static changes.

The existing Certbot account issued a certificate covering both hostnames using HTTP webroot validation at `/var/www/tasklean-acme`. The port 80 challenge exception remains available while normal HTTP redirects to HTTPS. `certbot.timer` is enabled, and `ops/renew-tasklean-tls.sh` reloads Nginx after renewal of this certificate. Cloudflare proxying does not need to be disabled for this setup.

Verify the origin with `curl --resolve codex-lean-task.thinkelution.com:443:127.0.0.1 https://codex-lean-task.thinkelution.com/` on the server, then verify the normal public HTTPS address, static assets, alternate redirect and an expected 404 for `/api/tasks`. The site has a restrictive CSP and no connection to local/private tasks.
