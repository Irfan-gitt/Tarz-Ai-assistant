import threading

cancel_event = threading.Event()


class TaskCancelled(Exception):
    pass


def check_cancel():
    if cancel_event.is_set():
        raise TaskCancelled()
