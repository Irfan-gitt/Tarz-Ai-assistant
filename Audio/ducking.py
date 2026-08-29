# ducking_live.py
import os
from pycaw.utils import AudioUtilities


def disable_ducking_for_this_process() -> bool:
    """
    Live, per-process opt-out from Windows' communications ducking —
    takes effect immediately, no registry, no service restart, no
    reboot, no admin rights. Scoped only to TARZ's own audio session,
    so it never touches anything system-wide or affects other users.

    Must be called AFTER the mic stream has been opened at least once
    (even briefly) — Windows only creates an audio session for your
    process once it's actually captured audio, and this needs that
    session to already exist to target it.
    """
    pid = os.getpid()
    for session in AudioUtilities.GetAllSessions():
        if session.ProcessId == pid:
            # pycaw's typo'd binding — real method, works
            session._ctl.SetDuckingPreferences(True)
            print(
                f"[Audio] Ducking opt-out set for this session (PID {pid}), live.")
            return True
    print(
        f"[Audio] No audio session found yet for PID {pid} — open the mic stream first, then call this.")
    return False


# ducking.py


def protect_process_from_ducking(process_name: str = "Spotify.exe") -> bool:
    """
    Opts a specific app's audio session OUT of being ducked by Windows'
    communications-ducking behavior. Must target the app that's actually
    PLAYING audio (Spotify), not the one capturing the mic (TARZ) —
    ducking is something that happens TO a playback session, so the
    opt-out has to be set on that session, not on the capturing process.
    """
    found = False
    for session in AudioUtilities.GetAllSessions():
        if session.Process and session.Process.name().lower() == process_name.lower():
            session._ctl.SetDuckingPreferences(True)
            print(f"[Audio] {process_name} opted out of ducking.")
            found = True
    if not found:
        print(
            f"[Audio] No running session found for {process_name} — is it open?")
    return found
