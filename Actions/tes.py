
import time
import execute_action
# Actions/tes.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

execute_action._current_app = "spotify"

print("--- first call ---")
start = time.time()
result = execute_action.click.invoke({"element": "play button"})
print(result, f"({time.time()-start:.2f}s)")

print("--- second call, same target ---")
start = time.time()
result = execute_action.click.invoke({"element": "play button"})
print(result, f"({time.time()-start:.2f}s)")
