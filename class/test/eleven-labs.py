import pygame
from elevenlabs.client import ElevenLabs
import os
elevenlabs = ElevenLabs(
    api_key="0595d1ca88f9493284b0c4e7dbf50015f293c815b4497de002d19607138acfe0")

audio = elevenlabs.text_to_speech.convert(
    text=s supposed to be just another smart tool. Nothing more.",
    voice_id="ErXwobaYiN019PkySvjV",
    model_id="eleven_multilingual_v2",

)

# Save instead of play
with open("output.mp3", "wb") as f:
    for chunk in audio:
        f.write(chunk)

# Play with pygame (no ffmpeg needed)
pygame.mixer.init()
pygame.mixer.music.load("output.mp3")
pygame.mixer.music.play()

while pygame.mixer.music.get_busy():
    pygame.time.Clock().tick(10)
