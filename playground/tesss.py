import sounddevice as sd
import numpy as np
import time


def detect_clap(threshold=.5517578, duration=1):
    """Detect sharp clap sound"""
    audio = sd.rec(int(duration * 16000),
                   samplerate=16000,
                   channels=1,
                   dtype='float32')
    sd.wait()
    peak = np.max(np.abs(audio))
    print("Peak:", peak)
    return peak > threshold


def wait_for_double_clap():
    print("Waiting for double clap...")
    while True:
        if detect_clap():
            time.sleep(0.3)  # wait for second clap
            if detect_clap():
                print("Double clap detected!")
                return  # activate TARZ


print(sd.query_devices())

wait_for_double_clap()
