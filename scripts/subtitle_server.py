"""Local subtitle overlay server for OBS (Mojicast-style).

Serves an overlay page at http://localhost:<port>/ suitable for an OBS
browser source (transparent background), and streams partial/final subtitle
events over SSE at /events. Designed to be driven by realtime_transcribe.py.
"""
import http.server
import json
import queue
import threading

OVERLAY_HTML = """<!doctype html>
<meta charset="utf-8">
<style>
  html, body { margin: 0; background: transparent; overflow: hidden; }
  #box {
    position: absolute; left: 0; right: 0; bottom: 4vh;
    text-align: center; font-family: "Yu Gothic UI", "Meiryo", sans-serif;
    font-size: 5vh; line-height: 1.4; color: #fff;
    text-shadow: 0 0 8px #000, 0 0 4px #000, 2px 2px 2px #000;
  }
  #partial { opacity: 0.75; font-style: italic; }
</style>
<div id="box"><span id="final"></span> <span id="partial"></span></div>
<script>
  const fin = document.getElementById("final");
  const par = document.getElementById("partial");
  let clearTimer = null;
  const es = new EventSource("/events");
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (clearTimer) { clearTimeout(clearTimer); clearTimer = null; }
    if (ev.type === "partial") {
      par.textContent = ev.text;
    } else if (ev.type === "final") {
      fin.textContent = ev.text;
      par.textContent = "";
      clearTimer = setTimeout(() => { fin.textContent = ""; }, 6000);
    }
  };
</script>
"""


class SubtitleServer:
    """Fan-out of subtitle events to any number of SSE clients."""

    def __init__(self, port: int = 8765):
        self.port = port
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._httpd = None

    def publish(self, event: dict):
        data = json.dumps(event, ensure_ascii=False)
        with self._lock:
            for q in self._clients:
                q.put(data)

    def partial(self, text: str):
        self.publish({"type": "partial", "text": text})

    def final(self, text: str, lang: str = ""):
        self.publish({"type": "final", "text": text, "lang": lang})

    def start(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the console clean for subtitles
                pass

            def do_GET(self):
                if self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    q: queue.Queue = queue.Queue()
                    with server._lock:
                        server._clients.append(q)
                    try:
                        while True:
                            data = q.get()
                            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                            self.wfile.flush()
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                        pass
                    finally:
                        with server._lock:
                            if q in server._clients:
                                server._clients.remove(q)
                else:
                    body = OVERLAY_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self
