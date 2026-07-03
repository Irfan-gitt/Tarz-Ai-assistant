import os
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
            if not os.path.exists(CREDENTIALS_FILE):
                raise GmailNotConfigured(GMAIL_NOT_CONFIGURED)
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

        raw = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

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
def read_emails(max_results: int = 5, query: str = "") -> str:
    """
    Read recent emails from Gmail inbox.

    Examples:
    read_emails(max_results=5)
    read_emails(query="from:boss@company.com")
    read_emails(query="subject:invoice")
    """
    try:
        service = get_gmail_service()

        results = service.users().messages().list(
            userId='me',
            maxResults=max_results,
            q=query if query else "in:inbox"
        ).execute()

        messages = results.get('messages', [])

        if not messages:
            return "No emails found"

        output = f"📧 EMAILS ({len(messages)} found):\n"
        output += "=" * 40 + "\n\n"

        for msg in messages:
            # Get full message
            full_msg = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='full'
            ).execute()

            headers = full_msg['payload']['headers']
            subject = next(
                (h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value']
                          for h in headers if h['name'] == 'From'), 'Unknown')
            date = next((h['value']
                        for h in headers if h['name'] == 'Date'), '')

            body = ""
            if 'parts' in full_msg['payload']:
                for part in full_msg['payload']['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body'].get('data', '')
                        if data:
                            body = base64.urlsafe_b64decode(
                                data).decode('utf-8', errors='ignore')
                            body = body[:200] + \
                                "..." if len(body) > 200 else body
                            break

            output += f"From: {sender}\n"
            output += f"Subject: {subject}\n"
            output += f"Date: {date}\n"
            output += f"Preview: {body}\n"
            output += "-" * 30 + "\n\n"

        return output

    except GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Read emails failed: {e}"


@tool
def search_emails(query: str, max_results: int = 5) -> str:
    """
    Search emails by keyword, sender, subject etc.

    Examples:
    search_emails(query="from:amazon")
    search_emails(query="subject:invoice last month")
    search_emails(query="has:attachment")
    """
    return read_emails.invoke({"max_results": max_results, "query": query})
