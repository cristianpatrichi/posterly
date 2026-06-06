"""Transactional email via Resend (OTP sign-in codes).

If no RESEND_API_KEY is configured, sending is skipped and the caller relies on
OTP_DEV_EXPOSE for local testing. Note: with an unverified Resend domain you can
only deliver to your own Resend-account email — verify a domain for real users.
"""

from __future__ import annotations

import html
import logging

from . import config

log = logging.getLogger("collage.email")


def send_otp_email(to: str, code: str) -> bool:
    api_key = config.resend_api_key()
    if not api_key:
        log.warning("OTP email not sent: RESEND_API_KEY is not set")
        return False
    try:
        import resend

        resend.api_key = api_key
        resend.Emails.send(
            {
                "from": config.resend_from(),
                "to": [to],
                "subject": f"Your {config.brand_name()} sign-in code",
                "html": _html(code),
            }
        )
        # Keep recipient addresses out of logs; provider-side metrics can monitor
        # delivery volume without copying personal data into application logs.
        log.info("OTP email sent")
        return True
    except Exception as exc:
        # Provider exceptions can embed request data. Log only the exception type
        # so secrets and recipient addresses cannot be copied into log storage.
        log.error("Resend OTP send failed (%s)", type(exc).__name__)
        return False


def _html(code: str) -> str:
    brand = html.escape(config.brand_name())
    safe_code = html.escape(code)
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#1f2125">
  <h2 style="margin:0 0 8px">{brand}</h2>
  <p style="color:#555;margin:0 0 20px">Use this code to sign in. It expires in 10 minutes.</p>
  <div style="font-size:34px;font-weight:700;letter-spacing:8px;background:#f4efe6;border-radius:10px;padding:16px;text-align:center">{safe_code}</div>
  <p style="color:#888;font-size:12px;margin:20px 0 0">If you didn't request this, you can ignore this email.</p>
</div>"""
