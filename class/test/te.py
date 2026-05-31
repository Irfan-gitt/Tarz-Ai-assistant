import os
import webbrowser

while True:

    command = input("Enter command: ").lower()

    # =========================
    # OPEN APPS
    # =========================

    if "chrome" in command:
        os.system("start chrome")

    elif "vscode" in command:
        os.system("code")

    elif "notepad" in command:
        os.system("notepad")

    elif "calculator" in command:
        os.system("calc")

    elif "spotify" in command:
        os.system("start spotify")

    # =========================
    # OPEN FOLDERS
    # =========================

    elif "downloads" in command:
        os.startfile(os.path.expanduser("~/Downloads"))

    elif "desktop" in command:
        os.startfile(os.path.expanduser("~/Desktop"))

    # =========================
    # WEB COMMANDS
    # =========================

    elif "youtube" in command:
        webbrowser.open("https://youtube.com")

    elif "google" in command:
        webbrowser.open("https://google.com")

    # =========================
    # SEARCH GOOGLE
    # =========================

    elif "search" in command:

        query = command.replace("search", "")

        url = f"https://www.google.com/search?q={query}"

        webbrowser.open(url)

    # =========================
    # EXIT
    # =========================

    elif "exit" in command:
        break

    else:
        print("Command not recognized")
