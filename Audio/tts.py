import edge_tts
import asyncio
import pygame
import os

os.makedirs("temp", exist_ok=True)


async def speak_async(text: str):
    text = text.replace("TARZ", "Tarz").replace("tarz", "Tarz")

    communicate = edge_tts.Communicate(
        text,
        voice="en-US-GuyNeural",
        rate="+8%",    # slightly faster — GenZ energy
        pitch="-1Hz",  # tiny bit deeper — more confident
        volume="+4%"
    )

    with open("temp/output.mp3", "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])

    pygame.mixer.init()
    pygame.mixer.music.load("temp/output.mp3")
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)
    pygame.mixer.quit()


def speak(text: str):
    asyncio.run(speak_async(text))
