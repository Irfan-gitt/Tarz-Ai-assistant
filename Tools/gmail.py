import os
import base64
import pickle
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from langchain_core.tools import tool

# Scopes - what TARZ can do with Gmail
SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]

CREDENTIALS_FILE = "gmail_credentials.json"
TOKEN_FILE = "gmail_token.pickle"


def get_gmail_service():
    """Authenticate and return Gmail service"""
    creds = None

    # Load existing token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as f:
            creds = pickle.load(f)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # First time - opens browser for login
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save token for next time
        with open(TOKEN_FILE, 'wb') as f:
            pickle.dump(creds, f)

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

        # Create message
        message = MIMEMultipart()
        message['to'] = to
        message['subject'] = subject
        message.attach(MIMEText(body, 'plain'))

        # Encode
        raw = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

        # Send
        service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        return f"Email sent to {to} with subject '{subject}'"

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

        # Search emails
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

            # Extract headers
            headers = full_msg['payload']['headers']
            subject = next(
                (h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value']
                          for h in headers if h['name'] == 'From'), 'Unknown')
            date = next((h['value']
                        for h in headers if h['name'] == 'Date'), '')

            # Extract body
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
