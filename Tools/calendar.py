import os
import pickle
import datetime
from langchain_core.tools import tool

try:
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    Request = None
    InstalledAppFlow = None
    build = None

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',          # added
    'https://www.googleapis.com/auth/calendar.events',    # added
]

CREDENTIALS_FILE = os.path.join(
    os.path.dirname(__file__), "gmail_credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "gmail_token.pickle")

CALENDAR_NOT_CONFIGURED = (
    "Calendar is not configured yet. Add Tools/gmail_credentials.json with Calendar API access "
    "and run a calendar task again to connect your Google account."
)

GOOGLE_DEPS_MISSING = (
    "Calendar is not available because Google API packages are not installed yet. "
    "Run install.bat or install the requirements first."
)


class CalendarNotConfigured(Exception):
    pass


def get_calendar_service():
    """Authenticate and return Calendar service (shares token with Gmail)."""
    if build is None or InstalledAppFlow is None or Request is None:
        raise CalendarNotConfigured(GOOGLE_DEPS_MISSING)

    if not os.path.exists(CREDENTIALS_FILE):
        raise CalendarNotConfigured(CALENDAR_NOT_CONFIGURED)

    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'rb') as f:
                creds = pickle.load(f)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        try:
            with open(TOKEN_FILE, 'wb') as f:
                pickle.dump(creds, f)
        except OSError:
            pass

    return build('calendar', 'v3', credentials=creds)


@tool
def create_event(summary: str, start_time: str, end_time: str, description: str = "") -> str:
    """
    Create a Google Calendar event.
    Times must be in ISO format: YYYY-MM-DDTHH:MM:SS (24hr, local time).

    Examples:
    create_event(summary="Team meeting", start_time="2026-06-15T10:00:00", end_time="2026-06-15T11:00:00")
    create_event(summary="Dentist", start_time="2026-06-20T14:30:00", end_time="2026-06-20T15:00:00", description="Checkup")
    """
    try:
        service = get_calendar_service()

        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_time, 'timeZone': 'Asia/Kolkata'},
            'end': {'dateTime': end_time, 'timeZone': 'Asia/Kolkata'},
        }

        created = service.events().insert(calendarId='primary', body=event).execute()
        return f"Event created: {summary} on {start_time}. Link: {created.get('htmlLink')}"

    except CalendarNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Create event failed: {e}"


@tool
def list_events(max_results: int = 5) -> str:
    """
    List upcoming Google Calendar events.
    Use when user asks 'what's on my calendar' or 'my upcoming events'.
    """
    try:
        service = get_calendar_service()
        now = datetime.datetime.utcnow().isoformat() + 'Z'

        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])

        if not events:
            return "No upcoming events found"

        output = f"📅 UPCOMING EVENTS ({len(events)}):\n" + "=" * 40 + "\n\n"

        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            output += f"- {event['summary']} at {start}\n"

        return output

    except CalendarNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"List events failed: {e}"


@tool
def delete_event(summary: str) -> str:
    """
    Delete a Google Calendar event by matching its title.
    Deletes the closest upcoming event whose title matches.

    Example:
    delete_event(summary="Team meeting")
    """
    try:
        service = get_calendar_service()
        now = datetime.datetime.utcnow().isoformat() + 'Z'

        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=20,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])

        for event in events:
            if summary.lower() in event['summary'].lower():
                service.events().delete(calendarId='primary',
                                        eventId=event['id']).execute()
                return f"Deleted event: {event['summary']}"

        return f"No upcoming event found matching '{summary}'"

    except CalendarNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Delete event failed: {e}"
