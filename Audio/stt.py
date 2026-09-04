# Audio/stt.py
import numpy as np
from Audio.orb_overlay import get_orb
import os
import asyncio
import sounddevice as sd
from google import genai
from google.genai import types
from cartesia import AsyncCartesia
from dotenv import load_dotenv
from Audio.wake_word import get_best_input_device, play_wake_beep
load_dotenv()

GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),
    os.getenv("GEMINI_KEY_4"),
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

if not GEMINI_KEYS:
    raise RuntimeError("No GEMINI_KEY_* environment variables found")

current_key_idx = 0


MODEL = "gemini-3.5-transcribe-live"

CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
CARTESIA_MODEL = "ink-2"

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_MS = 100
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
SPEECH_RMS_THRESHOLD = 0.01


async def _wait_for_final_or_silence(
    receive_task: asyncio.Task, last_speech: dict[str, float], timeout: float | None
) -> bool:
    """Wait for a final transcript, using ``timeout`` as a silence limit."""
    if timeout is None:
        await receive_task
        return True

    loop = asyncio.get_running_loop()
    while not receive_task.done():
        remaining = timeout - (loop.time() - last_speech["at"])
        if remaining <= 0:
            return False
        await asyncio.wait({receive_task}, timeout=min(remaining, 0.25))

    await receive_task
    return True


def _get_client():
    return genai.Client(api_key=GEMINI_KEYS[current_key_idx])


async def _stream_one_utterance(timeout: float | None = None) -> str:
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
                loop = asyncio.get_running_loop()
                last_speech = {"at": loop.time()}

                def note_speech():
                    last_speech["at"] = loop.time()

                async def receive():
                    async for response in session.receive():
                        server_content = response.server_content
                        if not server_content:
                            continue
                        interim = server_content.interim_input_transcription
                        if interim:
                            print(f"\r🎤 {interim.text}", end="", flush=True)
                            get_orb().update_caption(interim.text)
                        final = server_content.input_transcription
                        if final:
                            print(f"\n✅ FINAL: {final.text}")
                            get_orb().update_caption(final.text)
                            result["text"] = final.text
                            return

                async def send_mic():
                    queue = asyncio.Queue()

                    def callback(indata, frames, time, status):
                        if status:
                            print("Audio:", status)
                        loop.call_soon_threadsafe(
                            queue.put_nowait, bytes(indata))

                        samples = np.frombuffer(indata, dtype=np.int16).astype(
                            np.float32) / 32768.0
                        rms = float(np.sqrt(np.mean(samples ** 2)))
                        if rms >= SPEECH_RMS_THRESHOLD:
                            loop.call_soon_threadsafe(note_speech)
                        get_orb().update_level(min(rms * 4, 1.0))

                    device, hostapi_name = get_best_input_device()
                    play_wake_beep()
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

                try:
                    if not await _wait_for_final_or_silence(recv_task, last_speech, timeout):
                        print("🔇 No follow-up — back to requiring 'Tarz'.")
                        recv_task.cancel()
                finally:
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
            else:
                print(
                    f"[STT] Unexpected error on key #{current_key_idx + 1} ({e}), rotating...")
            current_key_idx = (current_key_idx + 1) % len(GEMINI_KEYS)
            continue

    raise RuntimeError(f"All Gemini keys failed for live STT: {last_error}")


async def _stream_one_utterance_cartesia(timeout: float | None = None) -> str:
    client = AsyncCartesia(api_key=CARTESIA_API_KEY)
    result = {"text": None}

    try:
        async with client.stt.auto_finalize.websocket(
            model=CARTESIA_MODEL,
            language="en",
            encoding="pcm_s16le",
            sample_rate=SAMPLE_RATE,
        ) as ws:
            loop = asyncio.get_running_loop()
            last_speech = {"at": loop.time()}

            def note_speech():
                last_speech["at"] = loop.time()

            async def receive():
                async for response in ws.receive():
                    if response.type == "transcript":
                        if not getattr(response, "is_final", False):
                            get_orb().update_caption(response.text)
                        else:
                            get_orb().update_caption(response.text)
                            result["text"] = response.text
                            return
                    if response.type == "turn.end":
                        text = getattr(response, "text", "") or ""
                        get_orb().update_caption(text)
                        result["text"] = text
                        return

            async def send_mic():
                queue = asyncio.Queue()

                def callback(indata, frames, time, status):
                    if status:
                        print("Audio:", status)
                    loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

                    samples = np.frombuffer(indata, dtype=np.int16).astype(
                        np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(samples ** 2)))
                    if rms >= SPEECH_RMS_THRESHOLD:
                        loop.call_soon_threadsafe(note_speech)
                    get_orb().update_level(min(rms * 4, 1.0))

                device, hostapi_name = get_best_input_device()
                play_wake_beep()
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

            try:
                if not await _wait_for_final_or_silence(recv_task, last_speech, timeout):
                    print("🔇 No follow-up — back to requiring 'Tarz'.")
                    recv_task.cancel()
            finally:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass

            return result["text"] or ""
    finally:
        await client.close()


def live_listen(timeout: float | None = None) -> str:
    """Drop-in sync replacement for input('You:') — blocks until one spoken
    utterance is transcribed (or timeout elapses, returning ''). Tries Gemini
    first (rotating keys), falls back to Cartesia Ink-2 if every Gemini key fails."""
    try:
        return asyncio.run(_stream_one_utterance(timeout=timeout))
    except RuntimeError as e:
        print(f"[STT] Gemini exhausted ({e}), falling back to Cartesia")
        return asyncio.run(_stream_one_utterance_cartesia(timeout=timeout))
