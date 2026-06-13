import openwakeword
from openwakeword.model import Model
import pyaudio
import numpy as np

openwakeword.utils.download_models()
model = Model(wakeword_models=["hey_jarvis"])

p = pyaudio.PyAudio()
stream = p.open(rate=16000, channels=1, format=pyaudio.paInt16,
                input=True, frames_per_buffer=1280)

while True:
    audio = np.frombuffer(stream.read(1280), dtype=np.int16)
    prediction = model.predict(audio)
    if prediction.get("hey_jarvis", 0) > 0.5:
        print("Detected!")
