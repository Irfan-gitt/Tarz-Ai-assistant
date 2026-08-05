# TARZ — Personal Desktop AI Assistant

A Jarvis-style AI assistant for Windows that can see your screen, control your computer, hold conversations, remember things about you, and respond by voice.

---

## Why I Built This

This started purely out of curiosity. I watched a 10-minute LangChain tutorial on YouTube and thought — *could I actually build something like Jarvis? An AI that sees my screen and controls my PC?*

That's it. No roadmap, no grand plan, no prior professional dev background. Just curiosity that snowballed into months of building, breaking, and rebuilding.

I designed the architecture myself — how the tool-calling and conversation brains should split and route, when to bring in vision vs. OCR, how memory should fit into the flow, what TARZ should and shouldn't be able to do. The actual coding I did with AI as my pair-programmer: writing implementations, debugging errors together, and iterating fast on each piece until it matched what I had in mind. I'd decide the what and the why, AI helped me get to the how faster — then I'd test it, find what didn't fit, and tweak it myself until it worked the way I wanted.Ui was made using ai tools compleatly 

Along the way I picked up real concepts — RAG (retrieval-augmented generation), tool-calling, vector embeddings, agent loops — by building with them directly with the help of ai cause iam new to the concepts ,instead of reading about them first. If you're someone who wants to build something like this yourself: you don't need to know every concept upfront. Design the system you want, use AI to move faster on implementation, and tighten the gaps yourself as you learn. I'm currently going deeper into RAG so I can redesign TARZ's memory to be properly context-aware in the next version — this project is as much a learning exercise as it is a tool I use daily.

---

DEMO: SORRY FOR THE LOQ QUALITY MAX GIT FILE SIZE IS 10 MB AND VIDEO ALSO FAST FORWARD TO 3X 🙂





## ⚠️ This Is a Prototype (v1)


https://github.com/user-attachments/assets/9e018ae5-f97f-4a2c-b430-35d2d75c0ebb




TARZ works, and I use it daily, but it is an early, rough version built by one curious person with AI as a coding partner — not a polished commercial product. Specifically:

- **TTS and STT are not fine-tuned.** Piper (voice output) and Whisper (voice input) are used with their default open-source models. They work, but don't expect commercial-assistant-level voice quality or recognition accuracy.
- **Task routing isn't perfect.** Casual phrasing sometimes confuses the classifier that decides "is this a conversation or a task" — e.g. "open chatgpt and talk about me" may get treated as conversation instead of an action. A fix is planned for v2.
- **No background/looping tasks yet.** TARZ can't currently do things like "check this every 5 seconds and respond." Every command is one request → one response, no persistent background loops.
- **Memory (RAG) is functional but basic.** It saves and retrieves tasks, conversations, and preferences, but isn't deeply context-aware yet. This is actively being improved.
- **Expect occasional bugs.** This was shipped intentionally early rather than polished indefinitely — building momentum mattered more than perfection.

If you hit bugs, that's expected at this stage. Feel free to fork it, fix things, and make it your own.
nb : maybe covered by bugs 🌚

---

## Budget-Friendly by Design

TARZ is built to run entirely on **free API tiers** — no subscriptions, no paid plans, no credit card required to get started. Every service used (Groq, Gemini, Cerebras, GitHub Models, OpenWeatherMap) has a generous free tier that covers normal daily use.

This is intentional. The goal was to build something genuinely useful that anyone can run without spending money.

**The trade-off is latency.** Free-tier APIs have rate limits and occasional queuing delays, which means TARZ sometimes pauses a second or two between steps when processing complex tasks. If you want a faster, near-real-time experience:

- Replace the free Groq/Cerebras calls with a paid OpenAI or Anthropic API key (just swap the model in `Main/tarz.py`)
- Use a local model via Ollama (already partially supported — `langchain-ollama` is in the stack)
- Host your own inference endpoint

But for most everyday use — playing music, checking weather, sending messages, quick conversations — the free tier is perfectly fine and the latency is barely noticeable. The point was to prove you don't need to pay to build something like this.

## What TARZ Can Do

- Control your computer — open apps, click UI elements, type, use keyboard shortcuts
- See your screen — uses Gemini vision + a grid-overlay system to locate and click on-screen elements, with OCR as a first pass
- Listen and speak — Whisper for speech-to-text, Piper for text-to-speech, hands-free via `Ctrl+Space`
- Hold real conversations — routes between a "tool-calling" brain (for actions) and a "conversation" brain (for chat)
- Remember things — RAG-based memory for past tasks, conversations, and user preferences, with correction support ("that's wrong, actually...")
- Play music — Spotify search, play, playlist, next/previous/pause
- Browse and search — opens Brave/Chrome, searches YouTube, general web search
- Send messages — WhatsApp search-and-send flow
- Check the weather — for any city, via OpenWeatherMap
- Get a news briefing — RSS + DuckDuckGo search, summarized and read aloud
- Set timers and alarms — with a chime sound
- Translate text — 30+ languages
- Control system volume — up, down, mute
- Run as a background app — system tray icon, desktop GUI window, auto-launch

---


## Gmail Setup (Optional)

Gmail is optional. If you do not add `Tools/gmail_credentials.json`, TARZ will still run normally; Gmail commands will simply reply that email is not configured yet.

1. Go to https://console.cloud.google.com
2. Create new project
3. Enable Gmail API:
   - APIs & Services → Library → Search "Gmail API" → Enable
4. Create credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
   - Application type: Desktop app
   - Download JSON → rename to `gmail_credentials.json`
   - Place inside the **Tools/** folder
5. Add yourself as test user:
   - OAuth consent screen → Test users → Add your Gmail
6. First run opens browser automatically for login
7. After login, token saved — no login needed again


## Google Calendar Setup for TARZ (Optional)

Calendar is optional. If you do not configure Google credentials, TARZ will still run normally; calendar commands will reply that calendar is not configured yet.

 1. Enable Calendar API

 2. Go to https://console.cloud.google.com
    Select your existing project (same one used for Gmail)
    APIs & Services → Library → search "Google Calendar API" → Enable

3. Reuse existing OAuth credentials
If you already set up Gmail, you can reuse the same gmail_credentials.json — just add the Calendar scope (Step 4 below). If not:

4. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
Application type: Desktop app
Download JSON → rename to gmail_credentials.json
Place inside the Tools/ folder

5. Add yourself as test user
OAuth consent screen → Test users → Add your Gmail address

TARZ can now:

- 📅 Read calendar events
- ➕ Create events
- ✏️ Update events
- ❌ Delete events
- 📆 List upcoming schedules




## Tech Stack

| Purpose | Tool |
|---|---|
| Tool-calling / agent brain | Cerebras (gpt-oss-120b), GPT-4o-mini (via GitHub Models), Gemini 2.5 Flash — with automatic fallback between them |
| Conversation brain | Groq (llama-3.3-70b-versatile) |
| Screen vision | Gemini 2.5 Flash (grid-overlay element finder) + EasyOCR |
| Speech-to-text | faster-whisper (large-v3-turbo) |
| Text-to-speech | Piper (local, offline) |
| Memory / RAG | ChromaDB + SentenceTransformers |
| Desktop GUI | pywebview (HTML/CSS/JS frontend, Python backend) |
| System tray | pystray |
| Computer control | PyAutoGUI |
| Orchestration | LangChain |

---

## Project Structure

```
Tarz-ai/
  Main/
    tarz.py            ← core brain: think(), LLM routing, memory
    gui_backend.py      ← pywebview backend for the desktop UI
    gui.html             ← the desktop UI itself
  Actions/
    execute_action.py  ← all tool functions exposed to the LLM
  Audio/
    tts.py              ← Piper text-to-speech
    stt.py               ← Whisper speech-to-text
  Grid_Finder/
    grid_finder.py     ← Gemini vision + grid overlay element finder
  Screen_Postition/
    get_coordinates.py ← OCR-first, grid-vision-fallback element finder
  Tools/
    rag.py               ← vector memory (tasks, conversations, preferences)
    weather.py           ← OpenWeatherMap
    timer.py              ← timers and alarms
    translator.py        ← language translation
    news.py                ← news briefing
  Prompts/
    prompt.py            ← system prompt for the conversation brain
  Vison/
    vision.py             ← screen description
  tarz_tray.py           ← system tray launcher
  requirements.txt
  .env                     ← you create this, see below
```

---

## Setup

### Prerequisites

- Windows 10/11
- Python 3.11
- An NVIDIA GPU is strongly recommended (Whisper + EasyOCR run much faster on CUDA). It will fall back to CPU, just slower.
- A working microphone

### 1. Clone the repo

```bash
git clone https://github.com/Irfan-gitt/Tarz-Ai-assistant.git
cd Tarz-Ai-assistant
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. (Recommended) Install PyTorch with CUDA support

The default `torch` install from `requirements.txt` is CPU-only. For GPU acceleration:

```bash
pip uninstall -y torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

### 4. Download the voice model

TARZ uses Piper for text-to-speech. The model files are not included in this repo (GitHub's 100MB file limit), so download them yourself:

```bash
pip install piper-tts
python -m piper.download_voices en_US-ryan-high
```

This downloads the default male voice (Ryan).

**Want a female voice instead?** Piper has several good ones. To switch:

```bash
python -m piper.download_voices en_US-amy-medium
```

Other female voices worth trying: `en_US-hfc_female-medium`, `en_US-kathleen-low`. Browse and preview all available voices at the [Piper voice samples page](https://rhasspy.github.io/piper-samples/).

Once downloaded, open `Audio/tts.py` and change this line to match the voice file you downloaded:

```python
VOICE_MODEL = "en_US-ryan-high.onnx"   # change to en_US-amy-medium.onnx etc.
```

---

## Environment Variables (API Keys)

Create a `.env` file in the project root with the following:

```dotenv
OPENROUTER_KEY=
groq_api=
GROQ_VISION_KEY_1=
GEMINI_KEY_1=
GEMINI_KEY_2=
CEREBRAS_API_KEY=
OPENWEATHER_KEY=
GITHUB_TOKEN=
```

All of these have generous free tiers. Here's where to get each one:

| Variable | What it's for | Where to get it |
|---|---|---|
| `groq_api` | Conversation LLM (Llama 3.3) | [console.groq.com/keys](https://console.groq.com/keys) — free signup |
| `GROQ_VISION_KEY_1` | Screen description / vision tasks via Groq | [console.groq.com/keys](https://console.groq.com/keys) — same console, can be the same key or a separate one |
| `GEMINI_KEY_1`, `GEMINI_KEY_2` | Screen element finding (grid vision), fallback tool LLM | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free, no card required |
| `CEREBRAS_API_KEY` | Primary tool-calling LLM | [cloud.cerebras.ai](https://cloud.cerebras.ai/) — free signup |
| `OPENWEATHER_KEY` | Weather tool | [openweathermap.org/api](https://openweathermap.org/api) — free tier, sign up then generate a key under "API keys" in your account |
| `OPENROUTER_KEY` | Optional extra LLM routing/fallback | [openrouter.ai/keys](https://openrouter.ai/keys) — free signup |
| `GITHUB_TOKEN` | Used to access GPT-4o-mini for free via GitHub Models | see detailed steps below |

### Getting your GitHub Token (step by step)

TARZ uses GitHub Models, which gives free access to GPT-4o-mini using a GitHub personal access token instead of an OpenAI key.

1. Make sure you're logged into GitHub, then go directly to: **[github.com/settings/tokens/new](https://github.com/settings/tokens/new)**
2. Under **Note**, name it something like `TARZ`
3. Under **Expiration**, choose `No expiration` (or 90 days if you prefer to rotate it)
4. Under **Select scopes**, tick the top-level **`repo`** checkbox
5. Scroll to the bottom and click **Generate token**
6. **Copy the token immediately** — it starts with `ghp_` and you will not be able to see it again after leaving the page
7. Paste it into your `.env` file:
   ```
   GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

### Tip: use multiple Gemini keys for better reliability

Gemini's free tier has per-key rate limits. TARZ is built to rotate between multiple Gemini keys automatically when one hits a rate limit (used in the grid vision finder and as a tool-LLM fallback). For smoother performance, especially if you use TARZ heavily, create 2–3 separate Gemini API keys (just sign up with different Google accounts, or check if AI Studio allows multiple keys per account) and add them as `GEMINI_KEY_1`, `GEMINI_KEY_2`, `GEMINI_KEY_3`, etc. The code already looks for keys in this numbered pattern.

---

## Running TARZ

```bash
python tarz_tray.py
```

This starts TARZ in your system tray (look for the icon near your clock) and automatically opens the desktop chat window. From there:

- Type a message and press `Enter`, or
- Press `Ctrl+Space` anywhere in the window to talk
- Toggle the `VOICE` switch in the top right to turn spoken replies on/off

Right-click the tray icon for **Open TARZ** (reopen the window) or **Quit**.

---

## Customizing TARZ

- **System behavior / personality** — edit the `SYSTEM` prompt inside `Main/tarz.py`. This is where TARZ's identity, tool-use rules, and app-specific workflows (Spotify, WhatsApp, YouTube, etc.) are defined.
- **Add a new tool** — write the logic in `Tools/`, wrap it with `@tool` in `Actions/execute_action.py`, and add it to the `TOOLS` list in `tarz.py`.
- **Add a new app workflow** — add a step-by-step flow to the `SYSTEM` prompt following the existing examples (Spotify, WhatsApp, etc.).
- **App shortcuts** — defined in `app_shortcut.py` as a dictionary of app → action → keybind.
- **Voice** — see the voice model section above to swap TTS voices.
- **GUI look** — `Main/gui.html` is a single self-contained file (HTML/CSS/JS), no build step needed. Edit and reload.

---

## Important message

- It may take sometime to execute, especially to find screen position and clicking with curser, if its taking too long restart the app
- Cursor clicks can be inaccurate when clicking on ui elements sometimes 
- If you give Tarz a task like play a specific song of spotify or any thing , dont interrupt with keyborad or hovering mouse it can make the ai to take more time or fail to execute

## What's Next (Roadmap)

- Smarter, properly-tuned RAG memory (in progress — I'm learning this properly now)
- Better task-vs-conversation classification
- Background/looping task support
- Calendar and reminders integration
- System info tool (CPU/RAM/battery)
- Voice fine-tuning for more natural speech

---

## A Closing Note

This is a personal, evolving project — built by someone learning in public with AI as a collaborator, not a finished product from a team. If something breaks, that's part of the deal at this stage. Fork it, break it further, fix it, learn from it — that's exactly how this project came to exist in the first place.

If there is any errors during the cloning or try to run Tarz I know you are going to fix it cause you are a dev ,, 

Seeeyaa 🙂‍↔️👋...
