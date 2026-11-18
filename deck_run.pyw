"""Keeps Control Deck running: starts server.py and restarts it if it ever stops."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
quick = 0
while True:
    t = time.time()
    subprocess.call([sys.executable, os.path.join(HERE, "server.py")], cwd=HERE)
    quick = quick + 1 if time.time() - t < 20 else 0
    with open(os.path.join(HERE, "deck.log"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S") + "  deck stopped, restarting\n")
    time.sleep(60 if quick >= 5 else 3)
