"""Record the engine's SSE events with audio-relative timestamps.

Runs the real pipeline on demo_audio.wav (real-time paced, so wall time ==
audio time) and writes every SSE event to events.json with a t_rel field.
The Remotion composition replays this timeline verbatim -- the demo video
shows real engine output, not mockups.
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import subprocess
import sys
import threading
import time
import urllib.request

PORT = 8799
events = []
t0 = None
done = threading.Event()


def listen():
    global t0
    req = urllib.request.urlopen(f"http://localhost:{PORT}/events", timeout=300)
    for raw in req:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        now = time.perf_counter()
        if ev.get("type") == "session_start":
            t0 = now
            continue
        if t0 is None:
            continue
        ev["t"] = round(now - t0, 3)
        events.append(ev)
        print(f"{ev['t']:7.2f}s {ev['type']:12} {str(ev.get('text',''))[:50]}")


proc = subprocess.Popen(
    [r".venv\Scripts\python", "scripts/realtime_transcribe.py",
     "--wav", "demo/demo_audio.wav", "--serve", str(PORT),
     "--speakers", "--translate", "en"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.0)  # let the HTTP server come up (models load after)
th = threading.Thread(target=listen, daemon=True)
th.start()
proc.wait()
time.sleep(1.0)
json.dump(events, open("demo/events.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"\nwrote demo/events.json ({len(events)} events)")
