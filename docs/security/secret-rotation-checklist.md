# Secret Rotation Checklist

Use this whenever `.env` may have been copied, synced, shared, screenshotted,
backed up, or otherwise exposed. The local `.env` is git-ignored and not tracked,
but it still lives in a working folder under `Downloads`, so treat it as exposed
if that folder ever leaves your control (S-005).

## Rotate

- [ ] Rotate `RESEND_API_KEY` in the Resend dashboard (revoke the old key).
- [ ] Rotate `TUNNEL_TOKEN` in Cloudflare Zero Trust → Tunnels (recreate / cycle
      the token) if the tunnel token was exposed.
- [ ] Rotate `SESSION_SECRET` (generate a new one:
      `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
      Note: this invalidates ALL existing sessions — everyone re-logs in.
- [ ] Rotate the `GOOGLE_CLIENT_ID` secret only if you use Google sign-in and the
      OAuth client secret was exposed (the client *id* alone is not a secret).

## Apply

- [ ] Replace the values in the local `.env` (keep `.env.example` as the only
      shareable template — never put real values there).
- [ ] Restart the app containers: `docker compose up -d --force-recreate collage`.
- [ ] After registry/secret changes, flush caches if applicable.

## Verify

- [ ] Confirm `.env` is still untracked: `git ls-files .env` prints nothing.
- [ ] Log in via OTP and confirm the email is delivered (Resend key works).
- [ ] Confirm the public URL still loads over the tunnel (token works).
- [ ] Confirm old sessions were invalidated (a previously logged-in browser is
      bounced to the login screen after `SESSION_SECRET` rotation).

## Prevent

- [ ] Keep local `.env` permissions strict (`chmod 600 .env`).
- [ ] Never paste `.env` contents into tickets, chats, screenshots, or repos.
- [ ] Treat `data/` (incl. `allowed_emails.txt`) as private production data.
- [ ] Consider a secret scanner (e.g. gitleaks) in pre-commit / CI.
