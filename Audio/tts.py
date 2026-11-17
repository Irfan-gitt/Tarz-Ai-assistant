import io
import base64
import re
import subprocess
import sounddevice as sd
import soundfile as sf
import numpy as np
from groq import Groq
from cartesia import Cartesia
from config_settings.llm_gateway import get_gateway, AllModelsFailed
from config_settings.config import TTS_MODELS

_MARKDOWN_STRIP = re.compile(r'[*_`#]')
_URL_STRIP = re.compile(r'https?://\S+')


def _clean_for_speech(text: str) -> str:
    text = _URL_STRIP.sub("", text)
    text = _MARKDOWN_STRIP.sub("", text)
    return text.strip()


def _speak_groq(text: str, cfg: dict, key: str, output_device=None, speed=1.0, volume=1.0):
    groq_client = Groq(api_key=key)
    response = groq_client.audio.speech.create(
        model=cfg["model"], voice=cfg["voice"], input=text, response_format="wav",
    )
    data, samplerate = sf.read(io.BytesIO(response.read()))
    if speed > 0 and speed != 1.0:
        indices = np.arange(0, len(data), speed)
        data = np.take(data, indices[indices < len(data)].astype(int), axis=0)
    data = np.clip(data * volume, -1.0, 1.0)
    sd.play(data, samplerate, device=output_device)
    sd.wait()


def _speak_cartesia(text: str, cfg: dict, key: str, output_device=None, speed=1.0, volume=1.0):
    cartesia_client = Cartesia(api_key=key)
    try:
        with cartesia_client.tts.websocket_connect() as connection:
            ctx = connection.context(
                model_id=cfg["model"],
                voice={"mode": "id", "id": cfg["voice"],
                       "speed": speed, "pitch": 1.0},
                output_format={"container": "raw",
                               "encoding": "pcm_f32le", "sample_rate": 44100},
                language="en",
            )
            ctx.push(text)
            ctx.no_more_inputs()
            for response in ctx.receive():
                if response.type == "chunk" and response.audio:
                    samples = np.frombuffer(response.audio, dtype=np.float32)
                    sd.play(np.clip(samples * volume, -1.0, 1.0), 44100, device=output_device)
                    sd.wait()
    finally:
        cartesia_client.close()


def _speak_windows(text: str, output_device=None, speed=1.0, volume=1.0):
    rate = round(max(-10, min(10, (speed - 1.0) * 10)))
    level = round(max(0.0, min(1.0, volume)) * 100)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate={rate}; $s.Volume={level}; "
        "$m=New-Object IO.MemoryStream; $s.SetOutputToWaveStream($m); "
        "$s.Speak([Console]::In.ReadToEnd()); "
        "[Console]::Out.Write([Convert]::ToBase64String($m.ToArray()))"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], input=text,
                            text=True, check=True, capture_output=True)
    audio, samplerate = sf.read(io.BytesIO(base64.b64decode(result.stdout.strip())))
    sd.play(audio, samplerate, device=output_device)
    sd.wait()


def speak(text: str, provider: str = "auto", output_device=None, speed: float = 1.0, volume: float = 1.0):
    text = _clean_for_speech(text)
    if not text:
        return

    if provider == "windows":
        _speak_windows(text, output_device, speed, volume)
        return

    def call(name, cfg, key):
        if name == "tts_groq":
            return _speak_groq(text, cfg, key, output_device, speed, volume)
        return _speak_cartesia(text, cfg, key, output_device, speed, volume)

    try:
        models = {"groq": ["tts_groq"], "cartesia": ["tts_cartesia"]}.get(provider, TTS_MODELS)
        get_gateway().run(models, call)
    except AllModelsFailed as e:
        print(f"[TTS] All speech models failed: {e}")
