# Audio/wake_word.py
import asyncio
import threading
import os
from livekit.wakeword import WakeWordModel, WakeWordListener

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "output", "hey_tarz", "hey_tarz.onnx"
)

model = WakeWordModel(models=[MODEL_PATH])
_callback = None


async def _listen_loop():
    async with WakeWordListener(model, threshold=0.1, debounce=2.0) as listener:
        print("[WakeWord] Listening for 'Hey TARZ'...")
        while True:
            detection = await listener.wait_for_detection()
            print(f"[WakeWord] ✓ Detected! ({detection.confidence:.3f})")
            if _callback:
                _callback()


def start(on_wake):
    global _callback
    _callback = on_wake
    threading.Thread(
        target=asyncio.run,
        args=(_listen_loop(),),
        daemon=True
    ).start()


def stop():
    pass
