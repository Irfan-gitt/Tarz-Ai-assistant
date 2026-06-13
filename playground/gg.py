from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
creds = None

if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file(
        "token.json",
        SCOPES
    )

if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(
            CREDENTIALS_FILE,
            SCOPES
        )
        creds = flow.run_local_server(port=0)

    with open("token.json", "w") as token:
        token.write(creds.to_json())

service = build("gmail", "v1", credentials=creds)

results = service.users().messages().list(
    userId="me",
    maxResults=5
).execute()

messages = results.get("messages", [])

print("\nLatest Emails:\n")

for msg in messages:
    message = service.users().messages().get(
        userId="me",
        id=msg["id"]
    ).execute()

    headers = message["payload"]["headers"]

    subject = "No Subject"

    for h in headers:
        if h["name"] == "Subject":
            subject = h["value"]
            break

    print("-", subject)
