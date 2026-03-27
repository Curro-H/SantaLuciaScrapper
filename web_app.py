"""
Flask web backend for SantaLucia Scraper.

Endpoints:
  POST /api/scrape/start          { username, password } -> { job_id }
  GET  /api/scrape/stream/<id>    SSE stream of scraping events
  POST /api/scrape/stop/<id>      signal stop to running job
"""

import json
import os
import threading
import uuid
from queue import Empty, Queue

from flask import Flask, Response, jsonify, render_template, request

import scraper

app = Flask(__name__)

# In-memory job store (single-worker process, safe)
jobs: dict[str, dict] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scrape/start", methods=["POST"])
def start_scrape():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    job_id = str(uuid.uuid4())
    queue: Queue = Queue()
    stop_event = threading.Event()
    jobs[job_id] = {"queue": queue, "stop_event": stop_event}

    def on_row(row):
        queue.put({"type": "row", "data": row})

    def on_progress(msg):
        queue.put({"type": "log", "msg": msg})

    def run():
        try:
            scraper.scrape(username, password, on_progress, on_row, stop_event)
        except Exception as exc:
            queue.put({"type": "error", "msg": str(exc)})
        finally:
            queue.put(None)  # sentinel — signals done

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/scrape/stream/<job_id>")
def stream_scrape(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        q = job["queue"]
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                except Empty:
                    # Heartbeat keeps the connection alive through proxies
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue

                if event is None:
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    jobs.pop(job_id, None)
                    break

                yield f"data: {json.dumps(event)}\n\n"
        except GeneratorExit:
            pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


@app.route("/api/scrape/stop/<job_id>", methods=["POST"])
def stop_scrape(job_id):
    job = jobs.get(job_id)
    if job:
        job["stop_event"].set()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
