import os
import re
import html
import base64
import pickle
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from langchain_core.tools import tool

try:
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    Request = None
    InstalledAppFlow = None
    build = None

# Scopes - what TARZ can do with Gmail
SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly'
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "gmail_credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "gmail_token.pickle")

GMAIL_NOT_CONFIGURED = (
    "Email is not configured yet. Add Tools/gmail_credentials.json and run a Gmail task again "
    "to connect your Google account."
)

GOOGLE_DEPS_MISSING = (
    "Email is not available because Google API packages are not installed yet. "
    "Run install.bat or install the requirements first."
)


class GmailNotConfigured(Exception):
    pass


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_gmail_service():
    """Authenticate and return Gmail service"""
    if build is None or InstalledAppFlow is None or Request is None:
        raise GmailNotConfigured(GOOGLE_DEPS_MISSING)

    if not os.path.exists(CREDENTIALS_FILE):
        raise GmailNotConfigured(GMAIL_NOT_CONFIGURED)

    creds = None

    # Load existing token
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'rb') as f:
                creds = pickle.load(f)
        except Exception:
            creds = None

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            # First time - opens browser for login
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(
                host='localhost',
                port=8080,
                authorization_prompt_message=(
                    "Gmail authorization opened in your browser. "
                    "Complete Google login, then return to TARZ."
                ),
                success_message=(
                    "Gmail is connected to TARZ. You can close this tab."
                )
            )

        try:
            with open(TOKEN_FILE, 'wb') as f:
                pickle.dump(creds, f)
        except OSError:
            pass

    return build('gmail', 'v1', credentials=creds)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")


def _extract_body(payload) -> str:
    """
    Recursively find the email body.
    Prefers text/plain anywhere in the tree, falls back to stripped text/html.
    """
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data", "")

    if mime == "text/plain" and data:
        return _decode(data)

    # Look through nested parts for plain text first
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text

    # HTML fallback
    if mime == "text/html" and data:
        raw = _decode(data)
        raw = re.sub(r"(?s)<(script|style).*?</\1>", "", raw)
        raw = re.sub(r"<[^>]+>", " ", raw)
        return html.unescape(raw)

    return ""


def _fetch_emails(query: str, max_results: int, preview_chars: int = 400) -> str:
    """Run a Gmail search and format the results."""
    service = get_gmail_service()

    results = service.users().messages().list(
        userId='me',
        maxResults=max_results,
        q=query
    ).execute()

    messages = results.get('messages', [])
    if not messages:
        return "No emails found"

    output = f"EMAILS ({len(messages)} found):\n"
    output += "=" * 40 + "\n\n"

    for msg in messages:
        full = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='full'
        ).execute()

        headers = {h['name']: h['value']
                   for h in full['payload'].get('headers', [])}

        body = " ".join(_extract_body(full['payload']).split())[:preview_chars]
        if not body:
            # Gmail's own snippet is a reliable fallback
            body = full.get('snippet', '')

        output += f"From: {headers.get('From', 'Unknown')}\n"
        output += f"Subject: {headers.get('Subject', 'No Subject')}\n"
        output += f"Date: {headers.get('Date', '')}\n"
        output += f"Preview: {body}\n"
        output += "-" * 30 + "\n\n"

    return output


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email using Gmail.

    Examples:
    send_email(to="friend@gmail.com", subject="Hello", body="Hey how are you?")
    send_email(to="boss@company.com", subject="Report", body="Please find attached...")
    """
    try:
        service = get_gmail_service()

        message = MIMEMultipart()
        message['to'] = to
        message['subject'] = subject
        message.attach(MIMEText(body, 'plain'))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        return f"Email sent to {to} with subject '{subject}'"

    except GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Email failed: {e}"


@tool
def read_emails(max_results: int = 10, query: str = "") -> str:
    """
    Read emails from Gmail. `query` uses Gmail search syntax.
    Read AND unread emails are included. Use "in:anywhere" to also include spam/trash.

    Examples:
    read_emails(max_results=5)
    read_emails(query="from:boss@company.com")
    read_emails(query="in:anywhere newer_than:30d subject:invoice")
    """
    try:
        return _fetch_emails(query if query else "in:inbox", max_results)
    except GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Read emails failed: {e}"


@tool
def search_emails(query: str, max_results: int = 10) -> str:
    """
    Search emails by keyword, sender, subject etc. using Gmail search syntax.
    Includes already-opened emails. Wrap alternatives in braces for OR,
    e.g. {rejected unfortunately "not selected"}.

    Examples:
    search_emails(query="from:amazon")
    search_emails(query="subject:invoice newer_than:30d")
    search_emails(query="has:attachment")
    """
    try:
        return _fetch_emails(query, max_results)
    except GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Search emails failed: {e}"


@tool
def check_job_updates(max_results: int = 15, newer_than: str = "6m") -> str:
    """
    Find job application replies (rejections, offers, interview invites, selections)
    from companies, including already-opened emails. Use this for ANY question about
    job rejected / selected / approved / application status / interview updates.

    `newer_than` uses Gmail units: d = days, m = months, y = years (e.g. "30d", "6m", "1y").

    Examples:
    check_job_updates()
    check_job_updates(newer_than="30d")
    """
    keywords = (
        '{unfortunately "not moving forward" "regret to inform" "not selected" '
        '"other candidates" "unsuccessful" "decided not to proceed" '
        '"not shortlisted" shortlisted congratulations "offer letter" "pleased to offer"}'
    )
    context = "(application OR applied OR candidate OR position OR role OR interview OR hiring)"
    query = (
        f"in:anywhere {keywords} {context} newer_than:{newer_than} "
        "-category:promotions -category:social -from:linkedin.com -from:coursera.org"
    )
    try:
        return _fetch_emails(query, max_results)
    except GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Job update search failed: {e}"
