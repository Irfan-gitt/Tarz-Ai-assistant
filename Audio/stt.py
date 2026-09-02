# Audio/stt_live.py
import numpy as np
from Audio.orb_overlay import get_orb
import os
import asyncio
import sounddevice as sd
from google import genai
from google.genai import types
from cartesia import AsyncCartesia
from dotenv import load_dotenv
from Audio.wake_word import get_best_input_device, _play_wake_beep
load_dotenv()

GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),
    os.getenv("GEMINI_KEY_4"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

if not GEMINI_KEYS:
    raise RuntimeError("No GEMINI_KEY_* environment variables found")

current_key_idx = 0

# Verify against ai.google.dev/gemini-api/docs/live before relying on it —
# not confirmed current, see note above.
MODEL = "gemini-3.5-transcribe-live"

CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
CARTESIA_MODEL = "ink-2"  # native turn detection, mirrors Gemini's final/interim split

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_MS = 100
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000


def _get_client():
    return genai.Client(api_key=GEMINI_KEYS[current_key_idx])


async def _stream_one_utterance() -> str:
    """
    Connects, streams mic audio, returns the first FINAL transcript
    (one complete user utterance = one turn). Rotates across GEMINI_KEYS
    on auth/expiry/quota failures, retrying the connection with the next key.
    """
    global current_key_idx

    config = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
    )

    last_error = None

    for attempt in range(len(GEMINI_KEYS)):
        client = _get_client()
        try:
            async with client.aio.live.connect(model=MODEL, config=config) as session:
                print(f"🟢 Gemini Live connected (key #{current_key_idx + 1})")
                result = {"text": None}

                async def receive():
                    async for response in session.receive():
                        server_content = response.server_content
                        if not server_content:
                            continue
                        interim = server_content.interim_input_transcription
                        if interim:
                            print(f"\r🎤 {interim.text}", end="", flush=True)
                        final = server_content.input_transcription
                        if final:
                            print(f"\n✅ FINAL: {final.text}")
                            result["text"] = final.text
                            return

                async def send_mic():
                    loop = asyncio.get_running_loop()
                    queue = asyncio.Queue()

                    def callback(indata, frames, time, status):
                        if status:
                            print("Audio:", status)
                        loop.call_soon_threadsafe(
                            queue.put_nowait, bytes(indata))

                        samples = np.frombuffer(indata, dtype=np.int16).astype(
                            np.float32) / 32768.0
                        rms = float(np.sqrt(np.mean(samples ** 2)))
                        get_orb().update_level(min(rms * 4, 1.0))

                    device, hostapi_name = get_best_input_device()
                    _play_wake_beep()
                    print("🎤 Speak now...")

                    stream_kwargs = dict(
                        device=device,
                        samplerate=SAMPLE_RATE,
                        blocksize=CHUNK_FRAMES,
                        channels=CHANNELS,
                        dtype="int16",
                        callback=callback,
                    )
                    if hostapi_name == "Windows WASAPI":
                        stream_kwargs["extra_settings"] = sd.WasapiSettings(
                            auto_convert=True)

                    with sd.RawInputStream(**stream_kwargs):
                        while result["text"] is None:
                            audio_chunk = await queue.get()
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=audio_chunk, mime_type="audio/pcm;rate=16000")
                            )
                recv_task = asyncio.create_task(receive())
                send_task = asyncio.create_task(send_mic())

                await recv_task
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass

                return result["text"] or ""

        except Exception as e:
            err = str(e).lower()
            last_error = e
            if any(x in err for x in ("401", "403", "429", "quota", "expired", "invalid", "permission")):
                print(
                    f"[STT] Key #{current_key_idx + 1} failed ({e}), rotating...")
                current_key_idx = (current_key_idx + 1) % len(GEMINI_KEYS)
                continue
            raise

    raise RuntimeError(f"All Gemini keys failed for live STT: {last_error}")


async def _stream_one_utterance_cartesia() -> str:
    """
    Cartesia Ink-2 fallback — same contract as the Gemini version above.
    Ink-2 has native turn detection (turn.start/eager_end/end), so no manual
    VAD or "finalize" call needed, same as Gemini's built-in turn handling.
    NOTE: verify the exact turn/transcript field names against
    docs.cartesia.ai/api-reference/stt — Ink-2's realtime API shipped in SDK
    3.2.0 and public examples are still sparse; not confirmed current.
    """
    client = AsyncCartesia(api_key=CARTESIA_API_KEY)
    result = {"text": None}

    try:
        async with client.stt.auto_finalize.websocket(
            model=CARTESIA_MODEL,
            language="en",
            encoding="pcm_s16le",
            sample_rate=SAMPLE_RATE,
        ) as ws:

            async def receive():
                async for response in ws.receive():
                    if response.type == "transcript" and getattr(response, "is_final", False):
                        result["text"] = response.text
                        return
                    if response.type == "turn.end":
                        result["text"] = getattr(response, "text", "") or ""
                        return

            async def send_mic():
                loop = asyncio.get_running_loop()
                queue = asyncio.Queue()

                def callback(indata, frames, time, status):
                    if status:
                        print("Audio:", status)
                    loop.call_soon_threadsafe(
                        queue.put_nowait, bytes(indata))

                    samples = np.frombuffer(indata, dtype=np.int16).astype(
                        np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(samples ** 2)))
                    get_orb().update_level(min(rms * 4, 1.0))

                device, hostapi_name = get_best_input_device()
                print("🎤 Speak now... (Cartesia fallback)")

                stream_kwargs = dict(
                    device=device,
                    samplerate=SAMPLE_RATE,
                    blocksize=CHUNK_FRAMES,
                    channels=CHANNELS,
                    dtype="int16",
                    callback=callback,
                )
                if hostapi_name == "Windows WASAPI":
                    stream_kwargs["extra_settings"] = sd.WasapiSettings(
                        auto_convert=True)

                with sd.RawInputStream(**stream_kwargs):
                    while result["text"] is None:
                        audio_chunk = await queue.get()
                        await ws.send(audio_chunk)

            recv_task = asyncio.create_task(receive())
            send_task = asyncio.create_task(send_mic())

            await recv_task
            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass

            return result["text"] or ""
    finally:
        await client.close()


def live_listen() -> str:
    """Drop-in sync replacement for input('You:') — blocks until one spoken
    utterance is transcribed. Tries Gemini first (rotating keys), falls back
    to Cartesia Ink-2 if every Gemini key fails."""
    try:
        return asyncio.run(_stream_one_utterance())
    except RuntimeError as e:
        print(f"[STT] Gemini exhausted ({e}), falling back to Cartesia")
        return asyncio.run(_stream_one_utterance_cartesia())
