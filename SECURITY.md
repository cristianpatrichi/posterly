# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Instead, report privately using GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
(the **"Report a vulnerability"** button on the repository's *Security* tab), or
by opening a draft security advisory.

When reporting, please include:

- A description of the issue and its impact
- Steps to reproduce (proof of concept if possible)
- Affected version / commit and your environment

We aim to acknowledge reports within a few days and will keep you updated on the
fix. Please give us a reasonable window to release a fix before public disclosure.

## Scope

Posterly is **self-hosted software** — each operator runs their own instance and
is responsible for their deployment (TLS, secrets, allow-list, reverse proxy).
Reports about the code in this repository are in scope; issues with a specific
third-party deployment you do not control are not.

## Hardening reminders for operators

- Set a strong, unique `SESSION_SECRET` and keep `.env` out of version control.
- Keep `OTP_DEV_EXPOSE` and `ENABLE_DOCS` **off** in production.
- Terminate TLS upstream so session cookies are sent with `Secure`.
- Restrict sign-in via `data/allowed_emails.txt`; consider enabling Cloudflare
  Turnstile (`TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET`).
- Keep dependencies up to date (`requirements.txt` is hash-locked).
