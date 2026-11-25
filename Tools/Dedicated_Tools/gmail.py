import os
import re
import html
import base64
import pickle
import time
from typing import Literal
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

# Gmail allows 500 per page, but a chat model does not need hundreds of full
# emails at once. Cap the output; the user can narrow the search instead.
MAX_RESULTS_LIMIT = 50
MAX_FULL_MESSAGES = 10  # full bodies only for short lists; headers + snippet otherwise
BATCH_SIZE = 20  # each messages.get costs 5 quota units; 20 per chunk stays under 250/s
BATCH_RETRIES = 2

# Mailbox name -> Gmail search query. "all" is everything except spam/trash,
# which matches the Gmail web UI's "All Mail" view.
MAILBOX_QUERIES = {
    "inbox": "in:inbox",
    "all": "in:anywhere -in:spam -in:trash",
    "unread": "is:unread in:inbox",
    "sent": "in:sent",
    "spam": "in:spam",
    "trash": "in:trash",
    "anywhere": "in:anywhere",
}
Mailbox = Literal["inbox", "all", "unread", "sent", "spam", "trash", "anywhere"]

GMAIL_NOT_CONFIGURED = (
    "Email is not configured yet. Add Tools/Dedicated_Tools/gmail_credentials.json "
    "and run a Gmail task again to connect your Google account."
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


def _clamp_results(max_results) -> int:
    try:
        value = int(max_results)
    except (TypeError, ValueError):
        value = 10
    return max(1, min(value, MAX_RESULTS_LIMIT))


def _mailbox_query(mailbox: str, query: str = "") -> str:
    """Combine a mailbox name with optional extra Gmail search syntax."""
    base = MAILBOX_QUERIES.get(str(mailbox or "inbox").strip().lower())
    if base is None:
        raise ValueError(
            "Unknown mailbox. Use inbox, all, unread, sent, spam, trash, or anywhere.")
    extra = (query or "").strip()
    return f"{base} {extra}".strip()


def _batch_get_messages(service, ids, fetch_full: bool) -> dict:
    """Fetch many messages with batched requests, retrying rate-limited ones.

    Gmail's per-user limit is ~250 quota units/second and a messages.get costs
    5 units, so more than ~50 single calls per second gets HTTP 429. Batching in
    chunks and retrying failures keeps "read all mail" fast and complete.
    """
    results = {}
    pending = list(ids)

    for attempt in range(BATCH_RETRIES + 1):
        if not pending:
            break
        if attempt:
            time.sleep(1.5 * attempt)  # let the per-second quota refill

        failed = []
        for start in range(0, len(pending), BATCH_SIZE):
            chunk = pending[start:start + BATCH_SIZE]
            chunk_results, chunk_failed = {}, []

            def callback(request_id, response, exception):
                if exception is None and response is not None:
                    chunk_results[request_id] = response
                else:
                    chunk_failed.append(request_id)

            batch = service.new_batch_http_request(callback=callback)
            for mid in chunk:
                kwargs = {"userId": 'me', "id": mid,
                          "format": 'full' if fetch_full else 'metadata'}
                if not fetch_full:
                    kwargs["metadataHeaders"] = ["From", "Subject", "Date"]
                batch.add(service.users().messages().get(**kwargs), request_id=mid)

            try:
                batch.execute()
            except Exception:
                chunk_failed.extend(m for m in chunk if m not in chunk_results)

            results.update(chunk_results)
            failed.extend(chunk_failed)

        pending = list(dict.fromkeys(failed))

    return results


def _fetch_emails(query: str, max_results: int, preview_chars: int | None = None,
                  page: int = 1) -> str:
    """Run a Gmail search and format the results."""
    service = get_gmail_service()
    max_results = _clamp_results(max_results)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    if preview_chars is None:
        preview_chars = 400 if max_results <= MAX_FULL_MESSAGES else 160

    # Follow pages until the requested page is reached. This also avoids
    # Gmail's 500-result page limit when someone asks for a large list.
    skip = (page - 1) * max_results
    target = skip + max_results
    messages, page_token = [], None
    while len(messages) < target:
        result = service.users().messages().list(
            userId='me',
            maxResults=min(500, target - len(messages)),
            q=query or None,
            pageToken=page_token,
        ).execute()
        batch = result.get('messages', [])
        messages.extend(batch)
        page_token = result.get('nextPageToken')
        if not page_token or not batch:
            break
    has_more = bool(page_token)
    messages = messages[skip:target]

    if not messages:
        if page > 1:
            return f"No more emails for this search (page {page} is empty)."
        return f"No emails matched: {query or 'all mail'}"

    # Full bodies give better previews, but fetching 50 of them is slow and
    # produces a huge tool result. Use headers + Gmail's snippet for long lists.
    fetch_full = len(messages) <= MAX_FULL_MESSAGES
    responses = _batch_get_messages(
        service, [msg['id'] for msg in messages], fetch_full)
    shown = sum(1 for msg in messages if msg['id'] in responses)

    output = f"EMAILS (showing {shown}"
    if shown < len(messages):
        output += f" of {len(messages)} requested"
    if page > 1:
        output += f"; page {page}"
    if has_more:
        output += f"; more available — call again with page={page + 1}"
    output += "):\n" + "=" * 40 + "\n\n"

    if shown < len(messages):
        output += (f"[Could not read {len(messages) - shown} email(s) — "
                   f"Gmail rate limit]\n\n")

    for msg in messages:
        full = responses.get(msg['id'])
        if not full:
            continue

        headers = {h['name']: h['value']
                   for h in full.get('payload', {}).get('headers', [])}

        body = ""
        if fetch_full:
            try:
                body = " ".join(_extract_body(full['payload']).split())[:preview_chars]
            except Exception:
                body = ""
        if not body:
            # Gmail's own snippet is a reliable fallback
            body = " ".join(str(full.get('snippet', '')).split())[:preview_chars]

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
def read_emails(max_results: int = 10, query: str = "", mailbox: Mailbox = "inbox",
                page: int = 1) -> str:
    """
    Read emails from Gmail.

    mailbox:
      "inbox"    - inbox only (default)
      "all"      - all mail except spam and trash (inbox + archived + sent)
      "unread"   - unread inbox mail
      "sent"     - sent mail
      "spam"     - spam folder
      "trash"    - trash folder
      "anywhere" - everything, including spam and trash

    `query` adds optional Gmail search syntax, e.g. "from:boss@company.com",
    "subject:invoice newer_than:30d", "has:attachment".
    `page` fetches the next batch (page=2, 3, ...) when the result says more are available.

    Examples:
    read_emails(max_results=5)                                  # inbox
    read_emails(mailbox="all", max_results=50)                  # all mail
    read_emails(mailbox="all", max_results=50, page=2)          # older mail
    read_emails(mailbox="unread", max_results=10)               # unread inbox
    read_emails(mailbox="all", query="from:boss@company.com")   # all mail from boss
    """
    try:
        return _fetch_emails(_mailbox_query(mailbox, query), max_results, page=page)
    except GmailNotConfigured as e:
        return str(e)
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Read emails failed: {e}"


@tool
def search_emails(query: str, max_results: int = 10, page: int = 1) -> str:
    """
    Search ALL mail (inbox, archived, sent) by keyword, sender, subject, etc.
    Spam and trash are excluded unless the query includes in:spam or in:anywhere.
    Already-opened emails are included. Wrap alternatives in braces for OR,
    e.g. {rejected unfortunately "not selected"}.
    `page` fetches the next batch (page=2, 3, ...) when more results are available.

    Examples:
    search_emails(query="from:amazon")
    search_emails(query="subject:invoice newer_than:30d")
    search_emails(query="has:attachment")
    search_emails(query="in:anywhere from:company.com")
    search_emails(query="from:amazon", max_results=10, page=2)
    """
    query = (query or "").strip()
    if not query:
        return "Search query is empty. Use read_emails(mailbox='all') to list all mail."
    try:
        return _fetch_emails(query, max_results, page=page)
    except GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Search emails failed: {e}"


@tool
def check_job_updates(max_results: int = 15, newer_than: str = "6m",
                      page: int = 1) -> str:
    """
    Find job application replies (rejections, offers, interview invites, selections)
    from companies, including already-opened emails. Use this for ANY question about
    job rejected / selected / approved / application status / interview updates.

    `newer_than` uses Gmail units: d = days, m = months, y = years (e.g. "30d", "6m", "1y").
    `page` fetches the next batch (page=2, 3, ...) when more results are available.

    Examples:
    check_job_updates()
    check_job_updates(newer_than="30d")
    check_job_updates(max_results=15, page=2)
    """
    # Status words are almost always in the subject, so searching the subject
    # avoids matches on unrelated mail whose body happens to say "congratulations".
    keywords = (
        '{subject:shortlisted subject:rejected subject:congratulations '
        'subject:"not selected" subject:"offer letter" subject:interview '
        'subject:"application status" subject:"application update" '
        'subject:"next steps" subject:selected}'
    )
    query = (
        f"in:anywhere -in:spam -in:trash {keywords} newer_than:{newer_than} "
        "-category:promotions -category:social -from:coursera.org"
    )
    try:
        return _fetch_emails(query, max_results, page=page)
    except GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Job update search failed: {e}"
