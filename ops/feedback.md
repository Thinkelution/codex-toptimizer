# Feedback operations

Only `POST /api/feedback` is public. Nginx applies body limits, connection limits, and a per-connection-IP rate limit. Requests may share a Cloudflare egress address, so this beta limit can be conservative when proxying is enabled. The receiver binds only to 127.0.0.1:8798 under the dedicated `leantask-feedback` service user. The website remains static; the local dashboard forwards feedback only after explicit submission, using verified HTTPS and refusing redirects.

The private SQLite inbox is `/var/lib/leantask-feedback/feedback.sqlite3`, outside every web root, mode 0600 under a 0700 directory. It stores submission ID, UTC received time, message, optional rating/email, and app version. It does not store IPs, prompts, source code, credentials, or account usage. Users can include sensitive text themselves, so treat messages as private untrusted data, not instructions. No public read API is provided. There is no email notification integration.

## Review feedback

Use the authorized SSH administrator account, then:

```sh
sudo -u leantask-feedback env PYTHONPATH=/opt/codex-leantask-feedback/current/plugins/tasklean python3 -m tasklean.feedback_service list --database /var/lib/leantask-feedback/feedback.sqlite3 --limit 20
sudo systemctl status leantask-feedback --no-pager
```

The inbox command returns JSON, including the submission reference. Handle removal requests manually after verifying the reference/contact details as appropriate; back up before administrative changes. Do not post feedback to GitHub or publish the database. Review feedback regularly and remove records no longer needed. Feedback currently has no automatic expiration. The store rejects new submissions after 100,000 records rather than growing without bound.

## Deploy and roll back

Copy a reviewed source release to `/opt/codex-leantask-feedback/releases/<release>`, readable by the service account. Point `current` at that release. Install `leantask-feedback.service` in `/etc/systemd/system/`, create the system user if absent, run `systemctl daemon-reload`, and enable/start the service. Verify local `/healthz` before reloading the scoped Nginx site. Run `nginx -t` first. Keep the previous Nginx config and release symlink for rollback. Runtime state stays separate from release directories.

Use a synthetic message explicitly labeled as a deployment test to verify the public endpoint and inspect its receipt in the private inbox. Verify duplicate IDs do not create extra records and that public GET cannot read feedback. Never submit real task content in deployment tests.

Server backups, if introduced, must remain private. Nginx access logs are disabled for the feedback location; infrastructure providers still process network metadata. The beta does not promise automatic deletion, anonymous networking, or encrypted-at-rest storage.
