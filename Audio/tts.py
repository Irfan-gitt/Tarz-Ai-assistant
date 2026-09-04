import random
import os
import io
import re
import sounddevice as sd
import soundfile as sf
import pyaudio
from groq import Groq
from cartesia import Cartesia
from dotenv import load_dotenv

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
cartesia_client = Cartesia(api_key=os.getenv("CARTESIA_API_KEY"))

GROQ_MODEL = "canopylabs/orpheus-v1-english"
GROQ_VOICE = "troy"
CARTESIA_VOICE_ID = "86e30c1d-714b-4074-a1f2-1cb6b552fb49"

_MARKDOWN_STRIP = re.compile(r'[*_`#]')
_URL_STRIP = re.compile(r'https?://\S+')


def _clean_for_speech(text: str) -> str:
    text = _URL_STRIP.sub("", text)
    text = _MARKDOWN_STRIP.sub("", text)
    return text.strip()


def _speak_groq(text: str):
    response = groq_client.audio.speech.create(
        model=GROQ_MODEL, voice=GROQ_VOICE, input=text, response_format="wav",
    )
    data, samplerate = sf.read(io.BytesIO(response.read()))
    sd.play(data, samplerate)
    sd.wait()


def _speak_cartesia(text: str):
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paFloat32,
                    channels=1, rate=44100, output=True)
    try:
        with cartesia_client.tts.websocket_connect() as connection:
            ctx = connection.context(
                model_id="sonic-3",
                voice={"mode": "id", "id": CARTESIA_VOICE_ID,
                       "speed": 0.65, "pitch": 1.0},
                output_format={"container": "raw",
                               "encoding": "pcm_f32le", "sample_rate": 44100},
                language="en",
            )
            ctx.push(text)
            ctx.no_more_inputs()
            for response in ctx.receive():
                if response.type == "chunk" and response.audio:
                    stream.write(response.audio)
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


def speak(text: str):
    text = _clean_for_speech(text)
    if not text:
        return
    try:
        _speak_groq(text)
    except Exception as e:
        print(f"[TTS] Groq failed ({e}), falling back to Cartesia")
        try:

            _speak_cartesia(text)
        except Exception as e2:
            print(f"[TTS] Cartesia also failed: {e2}")
