"""
email_service.py – Immediate Email Notification on Lead Capture
Sends a rich HTML email to the sales team the moment a lead is qualified.
"""

import smtplib
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

EMAIL_SENDER   = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_SALES    = os.getenv("EMAIL_SALES_TEAM", "")
SMTP_HOST      = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))


def _is_configured() -> bool:
    placeholders = {"your_app_password_here", "your_gemini_api_key_here",
                    "sales@yourcompany.com", "your_email@gmail.com", ""}
    return (
        EMAIL_SENDER not in placeholders and
        EMAIL_PASSWORD not in placeholders and
        EMAIL_SALES not in placeholders
    )


def _html_email(lead: dict, summary: str, tags: list[str]) -> str:
    tag_html = "".join(
        f'<span style="background:#10B981;color:#fff;padding:3px 12px;border-radius:999px;font-size:12px;margin-right:6px;">{t}</span>'
        for t in tags
    )
    rows = [
        ("Company Name",       lead.get("company_name",     "—")),
        ("Contact Person",     lead.get("contact_name",     "—")),
        ("Designation / Role", lead.get("designation",      "—")),
        ("Territory / Region", lead.get("territory",        "—")),
        ("Product Interest",   lead.get("product_interest", "—")),
        ("Expected Volume",    lead.get("expected_volume",  "—")),
        ("Email",              lead.get("email",            "—")),
        ("Phone",              lead.get("phone",            "—")),
    ]
    table_rows = "".join(f"""
        <tr style="border-bottom:1px solid #E5E7EB;">
          <td style="padding:10px 16px;font-weight:600;color:#374151;background:#F9FAFB;width:180px;">{label}</td>
          <td style="padding:10px 16px;color:#1F2937;">{value}</td>
        </tr>""" for label, value in rows)

    summary_escaped = summary.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:'Segoe UI',Arial,sans-serif;background:#F3F4F6;margin:0;padding:0;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 16px;">
<tr><td>
<table width="600" cellpadding="0" cellspacing="0" align="center"
  style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08);">

  <tr>
    <td style="background:linear-gradient(135deg,#059669,#0284c7);padding:28px 32px;">
      <h1 style="margin:0;color:#fff;font-size:22px;">🏥 New Qualified Lead – HealthBot</h1>
      <p style="margin:6px 0 0;color:rgba(255,255,255,.8);font-size:14px;">
        {datetime.now().strftime("%d %b %Y, %I:%M %p")}
      </p>
    </td>
  </tr>

  <tr>
    <td style="padding:20px 32px 8px;">
      <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;">Detected Intent</p>
      {tag_html or '<em style="color:#9CA3AF;font-size:13px;">General Enquiry</em>'}
    </td>
  </tr>

  <tr>
    <td style="padding:16px 32px 8px;">
      <p style="margin:0 0 12px;font-size:12px;font-weight:600;color:#6B7280;text-transform:uppercase;">Lead Details</p>
      <table width="100%" cellpadding="0" cellspacing="0"
        style="border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;font-size:14px;">
        {table_rows}
      </table>
    </td>
  </tr>
</table>
</td></tr></table>
</body></html>"""


def send_lead_email(lead: dict, conversation_summary: str, intent_tags: list[str]) -> bool:
    """
    Send lead email IMMEDIATELY when called.
    Returns True on success, False on failure (with detailed logging).
    """
    if not _is_configured():
        logger.warning(
            "⚠️  Email NOT sent – credentials not configured in .env\n"
            f"   EMAIL_SENDER={EMAIL_SENDER!r}\n"
            f"   EMAIL_SALES_TEAM={EMAIL_SALES!r}\n"
            "   → Fill in EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_SALES_TEAM in .env and restart."
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = (
            f"🏥 New Lead: {lead.get('company_name', 'Unknown')} — "
            f"{', '.join(intent_tags[:2]) or 'General Enquiry'}"
        )
        msg["From"] = f"HealthBot Lead Agent <{EMAIL_SENDER}>"
        msg["To"]   = EMAIL_SALES

        msg.attach(MIMEText(
            _html_email(lead, conversation_summary, intent_tags),
            "html"
        ))

        logger.info(f"Connecting to SMTP {SMTP_HOST}:{SMTP_PORT} …")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_SALES, msg.as_string())

        logger.info(f"✅ Lead email sent successfully to {EMAIL_SALES}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(
            f"❌ Email auth failed – wrong EMAIL_PASSWORD\n"
            f"   Gmail users: use an App Password, not your real password.\n"
            f"   Get one at: myaccount.google.com → Security → App passwords\n"
            f"   Error: {e}"
        )
    except smtplib.SMTPException as e:
        logger.error(f"❌ SMTP error: {e}")
    except Exception as e:
        logger.error(f"❌ Email send failed: {e}")

    return False


def test_email() -> dict:
    """
    Send a test email to verify configuration.
    Called via /api/test-email endpoint.
    """
    test_lead = {
        "company_name": "Test Company Ltd",
        "contact_name": "Test User",
        "designation":  "Sales Manager",
        "territory":    "South India",
        "product_interest": "Surgical Consumables",
        "expected_volume":  "500 units/month",
        "email":  EMAIL_SALES or "test@example.com",
        "phone":  "+91 9999999999",
        "intent_tags": "Dealership Interest, Bulk Purchase",
    }
    success = send_lead_email(test_lead, "This is a test email from HealthBot.", ["Dealership Interest", "Bulk Purchase"])
    return {
        "success":      success,
        "sender":       EMAIL_SENDER,
        "recipient":    EMAIL_SALES,
        "smtp_host":    SMTP_HOST,
        "smtp_port":    SMTP_PORT,
        "configured":   _is_configured(),
        "message":      "✅ Test email sent!" if success else "❌ Failed – check server logs for details.",
    }
