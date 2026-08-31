import os
import io
import re
import sounddevice as sd
import soundfile as sf
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

MODEL = "canopylabs/orpheus-v1-english"
VOICE = "troy"

_MARKDOWN_STRIP = re.compile(r'[*_`#]')
_URL_STRIP = re.compile(r'https?://\S+')


def _clean_for_speech(text: str) -> str:
    """Strip markdown/links before TTS — reading '**Sent**' or a raw URL
    out loud sounds broken even though it looks fine on screen."""
    text = _URL_STRIP.sub("", text)
    text = _MARKDOWN_STRIP.sub("", text)
    return text.strip()


def speak(text: str):
    """Convert text to speech via Groq's Orpheus TTS and play it immediately."""
    text = _clean_for_speech(text)
    if not text:
        return
    try:
        response = client.audio.speech.create(
            model=MODEL,
            voice=VOICE,
            input=text,
            response_format="wav",
        )
        audio_bytes = response.read()
        data, samplerate = sf.read(io.BytesIO(audio_bytes))
        sd.play(data, samplerate)
        sd.wait()   # blocking — see note below on why this matters
    except Exception as e:
        # never crash the whole loop over a TTS hiccup
        print(f"[TTS] Failed: {e}")
