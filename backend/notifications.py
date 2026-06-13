"""
Email notifications for Memora — stdlib only (smtplib + email.mime).
No new pip dependencies.

Named notifications.py rather than email.py to avoid shadowing Python's
built-in `email` package, which this module uses internally.
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_job_complete_email(job, user, confluence_url: str) -> None:
    """
    Send a job-completion notification to the meeting host.

    Args:
        job:           Job ORM instance.  Needs .id, .filename, .result_json.
        user:          User ORM instance.  Needs .email, .display_name.
        confluence_url: The published Confluence page URL (must be non-empty).

    Raises:
        RuntimeError: If SMTP is not configured or the SMTP handshake fails.
        smtplib.SMTPException: On protocol-level send errors.
    """
    if not settings.smtp_host:
        raise RuntimeError("SMTP_HOST is not configured — cannot send email.")

    title         = _extract_title(job)
    recipient     = user.email
    display_name  = user.display_name or recipient.split("@")[0]
    base          = settings.memora_base_url.rstrip("/")
    settings_url  = f"{base}/settings"
    dashboard_url = f"{base}/dashboard"

    subject = f'Your Confluence page for "{title}" is ready'

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = settings.smtp_from
    msg["To"]      = recipient

    msg.attach(MIMEText(
        _plain(display_name, title, confluence_url, settings_url, dashboard_url),
        "plain", "utf-8",
    ))
    msg.attach(MIMEText(
        _html(display_name, title, confluence_url, settings_url, dashboard_url),
        "html", "utf-8",
    ))

    _smtp_send(msg, recipient)
    logger.info("Sent completion email to %s (job=%s)", recipient, job.id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_title(job) -> str:
    """Return the AI-extracted title from result_json, falling back to filename."""
    if job.result_json:
        try:
            title = (json.loads(job.result_json).get("title") or "").strip()
            if title:
                return title
        except (json.JSONDecodeError, AttributeError):
            pass
    return job.filename or "your meeting"


def _esc(s: str) -> str:
    """Minimal HTML escaping for user-supplied strings."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _plain(
    name: str,
    title: str,
    confluence_url: str,
    settings_url: str,
    dashboard_url: str,
) -> str:
    return f"""\
Hi {name},

Your Confluence page for "{title}" has been published and is ready to view.

View page:
{confluence_url}

──────────────────────────────────────
Is this in the wrong space? Set your preference here:
{settings_url}

See all your past generations:
{dashboard_url}
──────────────────────────────────────

— Memora
"""


def _html(
    name: str,
    title: str,
    confluence_url: str,
    settings_url: str,
    dashboard_url: str,
) -> str:
    e_name  = _esc(name)
    e_title = _esc(title)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1.0" />
  <title>Your Confluence page is ready</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
             Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#f3f4f6;padding:40px 16px;">
    <tr>
      <td align="center">
        <table width="100%" style="max-width:560px;background:#ffffff;
               border-radius:12px;overflow:hidden;
               box-shadow:0 1px 3px rgba(0,0,0,.08);">

          <!-- Header -->
          <tr>
            <td style="background:#0d9488;padding:24px 32px;">
              <p style="margin:0;font-size:20px;font-weight:700;
                        color:#ffffff;letter-spacing:-0.3px;">&#127908; Memora</p>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:32px 32px 24px;">
              <p style="margin:0 0 6px;font-size:14px;color:#6b7280;">
                Hi {e_name},
              </p>
              <h1 style="margin:0 0 16px;font-size:20px;font-weight:700;
                         color:#111827;line-height:1.3;">
                Your Confluence page is ready
              </h1>
              <p style="margin:0 0 24px;font-size:15px;color:#374151;
                        line-height:1.6;">
                <strong>{e_title}</strong> has been documented and published
                to Confluence.
              </p>

              <!-- Primary CTA -->
              <table cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
                <tr>
                  <td style="background:#0d9488;border-radius:8px;">
                    <a href="{confluence_url}"
                       style="display:inline-block;padding:12px 24px;
                              font-size:15px;font-weight:600;color:#ffffff;
                              text-decoration:none;letter-spacing:-0.1px;">
                      View Confluence page &#8594;
                    </a>
                  </td>
                </tr>
              </table>

              <!-- Secondary links -->
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="border-top:1px solid #f3f4f6;padding-top:20px;">
                <tr>
                  <td style="padding:8px 0;">
                    <a href="{settings_url}"
                       style="font-size:13px;color:#0d9488;text-decoration:none;">
                      Is this in the wrong space? Set your preference here
                    </a>
                  </td>
                </tr>
                <tr>
                  <td style="padding:8px 0;">
                    <a href="{dashboard_url}"
                       style="font-size:13px;color:#0d9488;text-decoration:none;">
                      See all your past generations
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:16px 32px;
                       border-top:1px solid #f3f4f6;">
              <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.5;">
                Sent by Memora &mdash; your meeting-to-documentation pipeline.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# SMTP transport
# ---------------------------------------------------------------------------

def _smtp_send(msg: MIMEMultipart, recipient: str) -> None:
    """
    Open an SMTP connection and deliver *msg*.

    Port 465  → SMTP_SSL (implicit TLS from the start).
    Port 587  → STARTTLS (explicit TLS upgrade after EHLO).
    Port 25   → plain SMTP (no TLS, suitable for local relay / MailHog).
    Other     → treated as STARTTLS like 587.
    """
    host     = settings.smtp_host
    port     = settings.smtp_port
    user     = settings.smtp_user
    password = settings.smtp_password
    sender   = settings.smtp_from
    context  = ssl.create_default_context()

    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as smtp:
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(sender, [recipient], msg.as_bytes())
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            if port != 25:
                smtp.starttls(context=context)
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(sender, [recipient], msg.as_bytes())
