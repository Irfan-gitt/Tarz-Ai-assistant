"""Tiny NVIDIA chat tester: no memory, no tools, just talk to the model.

Put this next to config.py and run:  python chat_test.py
Every message is sent on its own, so the model never sees earlier turns.
"""

from config import NVIDIA_ALL_KEYS, NVIDIA_MODEL, MODEL_CONFIGS, make_llm

if not NVIDIA_ALL_KEYS:
    raise SystemExit("No NVIDIA key found. Set NVIDIA_API_KEY in your .env")

# config.py only defines tool_nvidia / classify_nvidia, so derive a chat
# config from tool_nvidia with a friendlier temperature. The name starts
# with "plain_", so make_llm respects NVIDIA_THINKING from your .env.
MODEL_CONFIGS["plain_nvidia"] = {
    **MODEL_CONFIGS["tool_nvidia"], "temperature": 0.6}

llm = make_llm("plain_nvidia", NVIDIA_ALL_KEYS[0])

print(f"\nModel: {NVIDIA_MODEL}")
print("Type a message. 'exit' or Ctrl+C to quit. No memory between messages.\n")

while True:
    try:
        text = input("you > ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not text:
        continue
    if text.lower() in ("exit", "quit"):
        break

    print("ai  > ", end="", flush=True)
    try:
        for chunk in llm.stream(text):  # single message, no history
            print(chunk.content, end="", flush=True)
    except Exception as e:
        print(f"\n[error] {type(e).__name__}: {e}", end="")
    print("\n")
