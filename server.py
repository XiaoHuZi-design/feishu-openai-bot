#!/usr/bin/env python3
"""A dependency-free Feishu/Lark bot backed by the OpenAI Responses API."""

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LOG = logging.getLogger("feishu-openai-bot")
FEISHU_BASE = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn/open-apis").rstrip("/")
OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
VERIFY_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "你是一个在飞书中工作的 helpful AI 助手。请用用户使用的语言简洁、准确地回答。")
PORT = int(os.getenv("PORT", "8000"))
MAX_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))

_token = {"value": "", "expires": 0.0}
_token_lock = threading.Lock()
_history = defaultdict(lambda: deque(maxlen=MAX_TURNS * 2))
_history_lock = threading.Lock()
_seen = {}
_seen_lock = threading.Lock()


def request_json(method, url, payload=None, headers=None, timeout=60):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError("HTTP %s from %s: %s" % (exc.code, url, body)) from exc


def tenant_token():
    with _token_lock:
        if _token["value"] and time.time() < _token["expires"]:
            return _token["value"]
        result = request_json("POST", FEISHU_BASE + "/auth/v3/tenant_access_token/internal", {
            "app_id": APP_ID, "app_secret": APP_SECRET,
        })
        if result.get("code", 0) != 0:
            raise RuntimeError("Failed to get Feishu token: %s" % result)
        _token["value"] = result["tenant_access_token"]
        _token["expires"] = time.time() + int(result.get("expire", 7200)) - 300
        return _token["value"]


def reply(message_id, text):
    result = request_json(
        "POST", FEISHU_BASE + "/im/v1/messages/%s/reply" % urllib.parse.quote(message_id),
        {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        {"Authorization": "Bearer " + tenant_token()},
    )
    if result.get("code", 0) != 0:
        raise RuntimeError("Feishu reply failed: %s" % result)


def ask_openai(conversation_id, text):
    with _history_lock:
        prior = list(_history[conversation_id])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + prior + [{"role": "user", "content": text}]
    result = request_json(
        "POST", OPENAI_BASE + "/responses",
        {"model": MODEL, "input": messages},
        {"Authorization": "Bearer " + OPENAI_KEY}, timeout=120,
    )
    answer = result.get("output_text")
    if not answer:
        chunks = []
        for item in result.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    chunks.append(content.get("text", ""))
        answer = "\n".join(chunks).strip()
    if not answer:
        raise RuntimeError("OpenAI returned no text output")
    with _history_lock:
        _history[conversation_id].append({"role": "user", "content": text})
        _history[conversation_id].append({"role": "assistant", "content": answer})
    return answer


def is_duplicate(event_id):
    now = time.time()
    with _seen_lock:
        for key, timestamp in list(_seen.items()):
            if now - timestamp > 3600:
                del _seen[key]
        if event_id in _seen:
            return True
        _seen[event_id] = now
        return False


def handle_event(payload):
    header = payload.get("header", {})
    event_id = header.get("event_id", "")
    if event_id and is_duplicate(event_id):
        return
    event = payload.get("event", {})
    message = event.get("message", {})
    if message.get("message_type") != "text":
        return
    try:
        content = json.loads(message.get("content", "{}"))
        text = content.get("text", "").strip()
        for mention in message.get("mentions") or []:
            text = text.replace(mention.get("key", ""), "").strip()
        if not text:
            return
        chat_type = message.get("chat_type")
        if chat_type == "group" and not message.get("mentions"):
            return
        conversation_id = message.get("chat_id") or event.get("sender", {}).get("sender_id", {}).get("open_id", "default")
        answer = ask_openai(conversation_id, text)
        reply(message["message_id"], answer[:28000])
    except Exception:
        LOG.exception("Failed to process Feishu event")
        try:
            reply(message.get("message_id", ""), "抱歉，处理消息时出错了，请稍后重试。")
        except Exception:
            LOG.exception("Failed to send error reply")


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/feishu/events":
            self.send_json(404, {"error": "not found"})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        timestamp = self.headers.get("X-Lark-Request-Timestamp", "")
        nonce = self.headers.get("X-Lark-Request-Nonce", "")
        signature = self.headers.get("X-Lark-Signature", "")
        if ENCRYPT_KEY and signature:
            expected = hashlib.sha256((timestamp + nonce + ENCRYPT_KEY).encode() + raw).hexdigest()
            if not __import__("hmac").compare_digest(signature, expected):
                self.send_json(401, {"error": "invalid signature"})
                return
        try:
            payload = json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "invalid json"})
            return
        if "encrypt" in payload:
            self.send_json(400, {"error": "encrypted payload is not supported; use signature verification mode"})
            return
        supplied_token = payload.get("token") or payload.get("header", {}).get("token", "")
        if VERIFY_TOKEN and supplied_token != VERIFY_TOKEN:
            self.send_json(401, {"error": "invalid verification token"})
            return
        if payload.get("type") == "url_verification":
            self.send_json(200, {"challenge": payload.get("challenge", "")})
            return
        if payload.get("header", {}).get("event_type") == "im.message.receive_v1":
            threading.Thread(target=handle_event, args=(payload,), daemon=True).start()
        self.send_json(200, {"code": 0})

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)


def validate_config():
    missing = [name for name, value in {
        "FEISHU_APP_ID": APP_ID, "FEISHU_APP_SECRET": APP_SECRET,
        "FEISHU_VERIFICATION_TOKEN": VERIFY_TOKEN, "OPENAI_API_KEY": OPENAI_KEY,
    }.items() if not value]
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    validate_config()
    LOG.info("Listening on http://0.0.0.0:%s", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
