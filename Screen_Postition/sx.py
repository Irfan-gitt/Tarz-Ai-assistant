import time
from grid_finder import click as grid_click

target = "green_play_button"

start = time.time()
result = grid_click(target)
elapsed = time.time() - start

print(f"Found: {result}")
print(f"Took: {elapsed:.2f}s")
