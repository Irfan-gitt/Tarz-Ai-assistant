import io
import re
import sounddevice as sd
import soundfile as sf
import pyaudio
from groq import Groq
from cartesia import Cartesia
from Main.llm_gateway import get_gateway, AllModelsFailed
from Main.config import TTS_MODELS

_MARKDOWN_STRIP = re.compile(r'[*_`#]')
_URL_STRIP = re.compile(r'https?://\S+')


def _clean_for_speech(text: str) -> str:
    text = _URL_STRIP.sub("", text)
    text = _MARKDOWN_STRIP.sub("", text)
    return text.strip()


def _speak_groq(text: str, cfg: dict, key: str):
    groq_client = Groq(api_key=key)
    response = groq_client.audio.speech.create(
        model=cfg["model"], voice=cfg["voice"], input=text, response_format="wav",
    )
    data, samplerate = sf.read(io.BytesIO(response.read()))
    sd.play(data, samplerate)
    sd.wait()


def _speak_cartesia(text: str, cfg: dict, key: str):
    cartesia_client = Cartesia(api_key=key)
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paFloat32,
                    channels=1, rate=44100, output=True)
    try:
        with cartesia_client.tts.websocket_connect() as connection:
            ctx = connection.context(
                model_id=cfg["model"],
                voice={"mode": "id", "id": cfg["voice"],
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
    def call(name, cfg, key):
        if name == "tts_groq":
            return _speak_groq(text, cfg, key)
        return _speak_cartesia(text, cfg, key)

    try:
        get_gateway().run(TTS_MODELS, call)
    except AllModelsFailed as e:
        print(f"[TTS] All speech models failed: {e}")
