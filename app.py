from __future__ import annotations

import atexit
import json
import queue
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from agent_worker import AgentSessionWorker

REPO_ROOT = Path(__file__).resolve().parent
WORKER = AgentSessionWorker(repo_root=REPO_ROOT)


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config["AGENT_WORKER"] = WORKER

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/state")
    def state() -> Response:
        return jsonify(WORKER.get_state_snapshot())

    @app.post("/api/session/new")
    def new_session() -> Response:
        snapshot = WORKER.new_session()
        return jsonify({"ok": True, "state": snapshot})

    @app.post("/api/message")
    def message() -> Response:
        payload = request.get_json(silent=True) or {}
        result = WORKER.send_message(str(payload.get("message", "")))
        return jsonify(result), result["status"]

    @app.get("/api/stream")
    def stream() -> Response:
        subscriber = WORKER.subscribe()

        def generate() -> str:
            try:
                while True:
                    try:
                        event = subscriber.get(timeout=15)
                    except queue.Empty:
                        yield "event: ping\ndata: {}\n\n"
                        continue

                    yield (
                        "event: update\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
            finally:
                WORKER.unsubscribe(subscriber)

        response = Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Connection"] = "keep-alive"
        return response

    return app


app = create_app()
atexit.register(WORKER.close)


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        threaded=True,
        debug=True,
        use_reloader=False,
    )
