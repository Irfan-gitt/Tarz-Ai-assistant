# TARZ — Personal Desktop AI Assistant

A Jarvis-style Semi Autonomous AI assistant for Windows that can see your screen, control your computer, hold conversations, remember things about you, and respond by voice.

---

---

## Demo

Three clips — together they show real multi-app task execution (including honest latency, not hidden), genuine screen understanding, and TARZ describing its own capabilities in its own words.

### Multi-step task: Spotify → WhatsApp

One spoken instruction, two completely different apps: TARZ opens Spotify, plays a song, then sends a WhatsApp message to a contact — recorded in full, including the real wait time between steps (sped up in places rather than cut out, so the latency mentioned in the [Budget-Friendly by Design](#budget-friendly-by-design) section below is visible, not hidden).

<video src="demo/Video Project 2.mp4" controls></video>

### Reading and understanding the screen

Asked what's currently on screen while "Attention Is All You Need" is open — TARZ identifies the paper by name and gives a brief summary of the paragraph, straight from reading the screen.

<video src="demo/Video nnn.mp4" controls></video>

### TARZ explaining itself

Asked directly what it can do — TARZ describes its own capabilities in its own words.

<video src="demo/Video Project 2 (1).mp4" controls></video>

---

## V2: Agent Workflow for Desktop Tasks

TARZ V2 turns one request into a controlled desktop workflow. A LangGraph supervisor hands the request to a single tool-bound agent with the full toolset available, checks the outcome with screen vision, and can retry when a visual action isn't complete. The tray application launches the current V2 agent.

### V2 highlights

- **Flat, single-agent tool routing:** every request goes to one tool-bound agent with the entire toolset available, instead of first classifying into a category. This wasn't the original design — see [Testing & Design Decisions](#testing--design-decisions) below for why it changed.
- **Supervised multi-step execution:** the agent can perform a sequence of actions and report back to the supervisor. Step limits (6 worker steps, 12 total steps) help prevent runaway loops.
- **Visual completion checks:** UI-changing tasks are verified through screen vision before success is reported (a click alone doesn't prove anything — the supervisor checks whether the screen actually shows the result). Information-only and non-visual tasks skip this extra latency.
- **Multi-provider fallback everywhere:** every LLM call — tool-calling, casual chat, and screen vision — has an automatic fallback provider that kicks in on rate limits or outages, not just a single point of failure.
- **Live, streaming voice pipeline:** both speech-to-text and text-to-speech stream over a persistent connection instead of record-then-transcribe, so there's no clipped first word and lower latency.
- **Hands-free follow-ups:** after TARZ finishes speaking, it listens for a follow-up for a short window without needing the wake word again — no separate "detect speech, then transcribe" handoff, so nothing gets cut off.
- **Cancel anytime:** a global hotkey interrupts an in-progress task at the next safe checkpoint, even mid-multi-step-execution.
- **Memory-aware requests:** relevant local ChromaDB memories are retrieved with Two-stage retrieval: Vector Retrieval + Cross-Encoder Reranking before the agent res  
- **Floating voice orb:** a lightweight always-on-top overlay shows mic level and live captions of what's being heard, instead of a console window.
- **Optional tracing:** LangSmith can record requests, agent flow, and tool runs when enabled in `.env`.

> **Prototype status:** V2 is actively developed software, not a finished commercial assistant. It is powerful, but desktop automation and model decisions can still be wrong. Review sensitive actions, especially messages, email, calls, and browser activity.

### Dedicated app tools

Dedicated tools give TARZ repeatable, app-specific workflows instead of relying only on generic clicking. Several are vision-backed, so TARZ confirms the actual on-screen result rather than assuming a click worked.

| App | Supported workflows |
|---|---|
| Spotify | Search and play a song or playlist; media controls handle play/pause and track navigation. |
| WhatsApp | Find a contact, send a message, start a call, and end a call. |
| Discord | Send a message, toggle mute/deafen, answer a call, or decline a call. |
| Telegram | Find a user and send a message. |
| Browser | Open a URL or search query in Brave by default. |

Dedicated tools are more convenient for repeatable tasks, but they are still GUI automation: an app update, changed window layout, missing login, display scaling, or an unexpected popup can cause a failure or a wrong click.

---

## Why I Built This

This started purely out of curiosity. I watched a 10-minute LangChain tutorial on YouTube and thought — *could I actually build something like Jarvis? An AI that sees my screen and controls my PC?*

That's it. No roadmap, no grand plan, no prior professional dev background. Just curiosity that snowballed into months of building, breaking, and rebuilding — and V2 has been a full architectural rewrite on top of that after i learned more .

I designed the architecture myself — how the tool-calling and conversation brains should split and route, when to bring in vision, how memory should fit into the flow, what TARZ should and shouldn't be able to do. The actual coding I did with AI as my pair-programmer: writing implementations, debugging errors together, and iterating fast on each piece until it matched what I had in mind. I'd decide the what and the why, AI helped me get to the how faster — then I'd test it, find what didn't fit, and tweak it myself until it worked the way I wanted. UI elements were built with AI tools too.



To be clear about how AI factored into this: I used it as a tool I directed, not something I depended on to think for me. Every architecture decision, every test, and every debugging session that traced an error back to its real cause was me driving the process — AI wrote code faster than I could have alone, but it didn't decide what to build or whether something was actually working.

---

## Testing & Design Decisions

Not every choice in this architecture was right on the first attempt — a couple of the biggest ones changed after actually testing them against real usage, the way a team would evaluate before shipping, rather than just picking whatever seemed reasonable and moving on.

- **Tool routing: category-based → flat single-agent.** V2 originally split tools into categories (realtime info, productivity, system, media, app control, chat) with a separate classification step choosing which one applied before a worker ran. In testing, this added a second point of failure and — more importantly — caused real hallucination in routing: a request could get classified into the wrong category, silently hiding the exact tool it needed and causing the model to either fail or call something unrelated instead. Re-testing the same tasks against a flat list — every tool exposed to one agent at once, no classification step — gave noticeably better, more consistent results with current tool-calling models. The classifier was removed entirely based on that result, not just simplified for its own sake.
- **Memory retrieval: hybrid over Vector Retrieval + Cross-Encoder Reranking memory retrieval, reranking** I tested hybrid BM25 + vector retrieval with cross-encoder reranking against plain vector-only retrieval for the memory system, evaluated with Hit Rate@5. The hybrid approach performed better, so that's the retrieval method TARZ's memory actually runs on now, not just the first thing that worked.

## Tech Stack

Every stage below has a primary provider and an automatic fallback — if the primary hits a rate limit, quota error, or outage, TARZ rotates to the next one without you noticing.

| Purpose | Primary | Fallback |
|---|---|---|
| Tool-calling & conversation (single agent, no separate router) | Groq — `openai/gpt-oss-120b` | OpenRouter — `openrouter/free` → Gemini — `gemini-3.7-flash` |
| Screen vision (describe screen, verify task completion) | Groq — `qwen/qwen3.8-27b` | OpenRouter — `openrouter/free` (image-capable) |
| GUI click targeting | Moondream `.point()` API | — |
| Speech-to-text (live utterance) | Gemini Live streaming transcription (rotates across up to 4 keys) | Cartesia — `ink-2` (native turn detection, streaming) |
| Wake word detection ("Tarz") | Silero VAD + local `faster-whisper` (`small`, CPU) | — |
| Text-to-speech | Groq — Orpheus (`canopylabs/orpheus-v1-english`) | Cartesia — `sonic-3` (streaming) |
| Memory / RAG | ChromaDB — hybrid BM25 + vector retrieval, cross-encoder reranking | — |
| Orchestration | LangGraph `StateGraph`, `MemorySaver` checkpointer | — |
| Voice overlay | PyQt6 floating orb (mic level + live captions) | — |
| App launching | `os.startfile` (Windows App Paths / PATH resolution) | — |
| Computer control | PyAutoGUI | — |
| System tray | pystray | — |

`openrouter/free` is OpenRouter's free model router — it automatically picks a free model that supports the request's needs (tool calling, structured output, or image understanding), so it covers multiple fallback roles without needing separate provider accounts.

---

## Global Hotkeys

| Hotkey | Action |
|---|---|
| `Ctrl+Space` | Cancel the current in-progress task and return to listening. |
| `Ctrl+Shift+Space` | Skip saying the wake word — starts listening immediately. |

Both work system-wide, not just while a TARZ window has focus.

---

## Budget-Friendly by Design

TARZ is built to run entirely on **free API tiers** — no subscriptions required to get started. Every service used has a free tier that covers normal daily use.

**Heads up on "free" tiers:** some providers (Cerebras, SambaNova) have moved to requiring a card on file even for their free tier — TARZ doesn't depend on either of those anymore for exactly that reason. If a provider you're using starts asking for payment info unexpectedly, that's the provider changing policy, not a TARZ bug — check their dashboard before assuming something's broken.

**The trade-off is latency.** Free-tier APIs have rate limits and occasional queuing delays, which means TARZ sometimes pauses a second or two between steps when processing complex tasks. If you want a faster, near-real-time experience:

- Swap in a paid API key for any stage in the table above
- Use a local model via Ollama for tool-calling or chat
- Host your own inference endpoint

But for most everyday use — playing music, checking weather, sending messages, quick conversations — the free tier is perfectly fine and the latency is barely noticeable.

## What TARZ Can Do

- Control your computer — open apps, click UI elements, type, use keyboard shortcuts
- See your screen — vision-based element finding and task-completion verification
- Listen and speak — live streaming speech-to-text and text-to-speech, hands-free via wake word or `Ctrl+Shift+Space`
- Hold real conversations — the same tool-bound agent handles both actions and casual chat, no separate router deciding which
- Remember things — hybrid BM25 + vector memory with cross-encoder reranking for past tasks, conversations, and user preferences
- Play music — Spotify search, play, playlist, next/previous/pause
- Browse and search — opens Brave/Chrome, searches YouTube, general web search
- Send messages — WhatsApp, Discord, Telegram search-and-send flows
- Check the weather — for any city, via OpenWeatherMap
- Get a news briefing
- Set timers and alarms
- Translate text
- Control system volume
- Cancel any task mid-execution with a hotkey
- Run as a background app — system tray icon, auto-launch on Windows startup

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

## Google Calendar Setup (Optional)

Calendar is optional. If you do not configure Google credentials, TARZ will still run normally; calendar commands will reply that calendar is not configured yet.

1. Go to https://console.cloud.google.com — select your existing project (same one used for Gmail)
2. APIs & Services → Library → search "Google Calendar API" → Enable
3. Reuse existing OAuth credentials — if you already set up Gmail, reuse `gmail_credentials.json` and add the Calendar scope. If not, create new OAuth 2.0 credentials the same way as the Gmail steps above.
4. OAuth consent screen → Test users → Add your Gmail address

TARZ can then read, create, update, delete, and list calendar events.

---

## Project Structure

```
Tarz-ai/
  Audio/
    stt_live.py         ← Gemini Live STT (streaming) with Cartesia fallback
    tts.py               ← Groq Orpheus TTS with Cartesia fallback
    wake_word.py         ← Silero VAD + faster-whisper wake-word listener, hotkey skip
    orb_overlay.py        ← PyQt6 floating voice orb (mic level + captions)
  Actions/
    execute_action.py   ← all generic tool functions exposed to the LLM (click, type, open_app, etc.)
  Tools/
    Dedicated_Tools/     ← Spotify, WhatsApp, Discord, Telegram, browser-specific automation
    cancel_state.py      ← shared cancel-hotkey event/exception
    rag.py                 ← Two-stage retrieval: Vector Retrieval + Cross-Encoder Reranking memory retrieval, reranking
    memory.py             ← context_agent → save_context → save_imp_context pipeline
    gmail.py, calendar_tool.py, real_time_data.py, media_control.py
  Vison/
    vision.py             ← screen description + task-completion verification, multi-provider vision router
  Prompts/
    prompt.py             ← system prompt for the agent
  playground/
    cl.py                   ← the current V2 agent entrypoint: LangGraph graph, supervisor/classify/worker nodes, think()
  tarz_tray.py            ← system tray launcher
  requirements.txt
  .env                      ← you create this, see below
```

> The main agent entrypoint moved during the V2 rewrite — double check the path `tarz_tray.py` actually launches on your machine matches wherever your current agent script lives before setting up autostart below.

---

## Setup

### Prerequisites

- Windows 10/11
- Python 3.11
- An NVIDIA GPU is recommended (the wake-word model runs much faster on CUDA). It will fall back to CPU, just slower.
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

---

## Environment Variables (API Keys)

Create a `.env` file in the project root with the following:

**Fill in every variable below — the code expects a value in each slot.** If you don't have a spare/second key for a given slot (a second vision key, extra Gemini keys, a second OpenRouter key, etc.), just reuse your existing key's value for that variable too instead of leaving it blank. A demo `.env` walkthrough is coming — the block below is a placeholder until then.

```dotenv
# Copy this file to .env and replace the empty values with your own keys.
# Never commit .env or paste real keys into this file.

# Core model providers
OPENROUTER_API_KEY=
OPENROUTER_API_KEY2=
GROQ_API_KEY=
GROQ_VISION_KEY_1=
GROQ_VISION_KEY_2=
GEMINI_KEY_1=
GEMINI_KEY_2=
GEMINI_KEY_3=
GEMINI_KEY_4=
GEMINI_KEY_5=
CEREBRAS_API_KEY=
CEREBRAS_API_KEY2=
SAMBANOVA_API_KEY=
GITHUB_TOKEN=

# Tools and voice services
OPENWEATHER_KEY=
MOONDREAM_API_KEY=
TAVILY_API_KEY=
CARTESIA_API_KEY=

# Optional LangSmith tracing
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=Tarz
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true

# Legacy names used by Main/v0.py and a few older tools. Set these to the
# same values as GROQ_API_KEY and OPENROUTER_API_KEY when using those files.
groq_api=
OPENROUTER_KEY=
GEMINI_API_KEY=
```

| Variable | What it's for | Where to get it |
|---|---|---|
| `GROQ_API_KEY` | Primary tool-calling, classification, chat, and TTS (Orpheus) | [console.groq.com/keys](https://console.groq.com/keys) — free signup |
| `GROQ_VISION_KEY_1`, `GROQ_VISION_KEY_2` | Screen vision (rotates between them on rate limits) | Same console — can be separate keys for more headroom |
| `GEMINI_KEY_1`–`GEMINI_KEY_4` | Live speech-to-text (rotates across all provided keys) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free, no card required |
| `OPENROUTER_API_KEY` | Fallback for tool-calling, classification, chat, and vision | [openrouter.ai/keys](https://openrouter.ai/keys) — free signup, still requires an account/key even for free models |
| `CARTESIA_API_KEY` | Fallback TTS and STT | [play.cartesia.ai](https://play.cartesia.ai) — free tier available (personal/non-commercial use, credit-limited) |
| `OPENWEATHER_KEY` | Weather tool | [openweathermap.org/api](https://openweathermap.org/api) — free tier, generate a key under "API keys" in your account |

### Tip: use multiple Gemini keys for better reliability

Gemini's free tier has per-key rate limits, and TARZ's live STT rotates automatically across every `GEMINI_KEY_*` you provide. For smoother performance, especially under heavy use, create 2–4 separate keys and add them as `GEMINI_KEY_1`, `GEMINI_KEY_2`, etc. — the code already looks for keys in this numbered pattern.

---

## Running TARZ

```bash
python tarz_tray.py
```
IF YOU GOT ANY ERROR RUNNING THROUGH TARZ_TRAY.PY RUN WITH TARZ.PY INSIDE MAIN FILE 

This starts TARZ in your system tray (look for the icon near your clock). Right-click the tray icon for options, including quitting.

Once running, either:
- Say **"Tarz"** to wake it, or
- Press **`Ctrl+Shift+Space`** to skip the wake word and start listening immediately

Press **`Ctrl+Space`** anytime to cancel whatever it's currently doing.

### Run TARZ automatically on Windows startup

**Option A — Startup folder (simplest):**

1. Press `Win+R`, type `shell:startup`, press Enter — this opens your Startup folder.
2. Right-click `tarz_tray.py` (or a `.bat` file that runs `venv\Scripts\python.exe tarz_tray.py` from your project folder) → Create shortcut.
3. Move that shortcut into the Startup folder.
4. TARZ now launches automatically at every login.

**Option B — Task Scheduler (more reliable, runs hidden with no console window):**

1. Open Task Scheduler → Create Task (not "Create Basic Task").
2. **General tab:** name it, check "Run whether user is logged on or not" if you want it hidden, or "Run only when user is logged on" for a normal session.
3. **Triggers tab:** New → "At log on".
4. **Actions tab:** New → Program: `C:\path\to\Tarz-ai\venv\Scripts\pythonw.exe` (use `pythonw.exe`, not `python.exe`, to avoid a console window) → Arguments: `tarz_tray.py` → Start in: `C:\path\to\Tarz-ai`.
5. Save. TARZ now starts silently in the background at every login.

---

## Customizing TARZ

- **System behavior / personality** — edit the `SYSTEM` prompt inside `Prompts/prompt.py`.
- **Add a new tool** — write the logic in `Tools/`, wrap it with `@tool` in `Actions/execute_action.py`, and add it to `ALL_TOOLS`.
- **Add a new app workflow** — build a dedicated tool under `Tools/Dedicated_Tools/`, following the existing examples (Spotify, WhatsApp, Discord, Telegram).
- **Voice** — TTS voice is set via the Cartesia/Groq voice IDs in `Audio/tts.py`.
- **Cancel/wake hotkeys** — change the key combos in the main agent file where `keyboard.add_hotkey(...)` is registered.

---

## Important Notes

- Screen-vision tasks can take a few seconds — that's the verification step working, not a hang. If something's taking unusually long, `Ctrl+Space` to cancel and retry.
- Cursor clicks can be inaccurate on some UI elements — this is an inherent limitation of vision-based GUI automation, not something a restart fixes.
- If you give TARZ a task like playing a specific song, avoid interrupting with keyboard or mouse mid-task — it can slow down or fail the in-progress action.

## Advantages and Current Limitations

### Advantages

- Runs as a Windows desktop assistant with a system tray launcher and hands-free voice control.
- Supervised, multi-step execution with visual verification instead of a single blind action per request.
- Every model call — tool-calling, chat, and vision — has an automatic fallback provider.
- Combines voice, vision, web information, productivity integrations, and desktop control in one workflow.
- Optional Gmail, Calendar, and LangSmith tracing can be enabled without making every feature mandatory.

### Limitations and known bugs ⚠️

- TARZ works, and I use it daily, but it is early, actively-developed software built by one curious person with AI as a coding partner — not a polished commercial product. Specifically:
- GUI automation is inherently brittle. Screen resolution, Windows scaling, application versions, language, focus, permissions, login state, and popups can change the result.
- Vision and LLM providers can be slow, rate-limited, unavailable, or occasionally misunderstand a request. Completion verification adds reliability but can also add noticeable latency.
- Voice recognition depends on microphone hardware, background noise, and provider availability. The wake-word listener can use CPU and may need device-specific tuning.
- Memory is local and useful for recall, but it is not yet a perfect long-term personal knowledge system. Review and correct important information.
- TARZ can send messages, control media, interact with browser pages, and initiate calls. Keep it supervised; do not use it for high-risk, irreversible, financial, medical, legal, or security-sensitive actions.
- **Screen data is not processed locally.** Screen vision (used to describe your screen and to verify a task actually completed) sends a screenshot to a cloud provider (Groq or OpenRouter) for analysis — it is not analyzed on your machine. This matters most for automation that touches private content: if TARZ is verifying a WhatsApp message got sent, or reading your screen while a personal email is open, that screen content is leaving your device and going to a third-party provider. Weigh that before pointing TARZ at anything sensitive, and review each provider's own data-handling policy if this matters to you.
- **Task routing isn't perfect.** Even with the classifier removed, casual phrasing can occasionally lead the agent to pick the wrong tool or miss one it should have used — flat routing tested better than categories, not perfectly.
- **No background/looping tasks yet.** TARZ can't currently do things like "check this every 5 seconds and respond." Every command is one request → one response, no persistent background loops.


---



## Contributing

Contributions are welcome. If you find a bug, please open an issue with your Windows version, Python version, a clear reproduction path, relevant logs, and screenshots only when they do not contain private information. Pull requests are especially useful for improving app-specific tools, reliability across display setups, voice handling, tests, documentation, and safer confirmation flows.

Before submitting a pull request, keep secrets out of commits, update `requirements.txt` if you add an import, and explain how you tested the change. Small, focused improvements are easier to review and integrate.

## What's Next (Roadmap)

- Background/looping task support
- Further tuning of the vision router for consistency
- More dedicated app workflows


---

## LangSmith Tracing

TARZ sends one parent trace per request to LangSmith, with the LangGraph router, LLM calls, and tool executions shown as nested runs. To enable it, install the dependencies and add these values to `.env`:

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_your_key_here
LANGSMITH_PROJECT=Tarz
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
```

Run the application normally and open the `Tarz` project in LangSmith. Inputs and outputs are hidden by default to avoid uploading prompts, retrieved memories, screen text, or tool results; set either `LANGSMITH_HIDE_*` value to `false` if you explicitly want payload capture.

---

## A Closing Note

This is a personal, evolving project — built by someone learning in public with AI as a collaborator, not a finished product from a team. If something breaks, that's part of the deal at this stage. Fork it, break it further, fix it, learn from it — that's exactly how this project came to exist in the first place. 

If 

Seeeyaa 🙂‍↔️👋...