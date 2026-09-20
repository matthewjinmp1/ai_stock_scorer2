"""Streaming transport with separate response-header and content-inactivity deadlines."""
import json
import queue
import socket
import threading
import time
import urllib.request
import urllib.error


class ConnectionDeadline(TimeoutError):
    pass


def read_completion(response, on_activity=None):
    headers = getattr(response, "headers", None) or {}
    if "text/event-stream" not in str(headers.get("Content-Type", "")):
        return json.loads(response.read().decode("utf-8"))
    payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": None}]}
    choice = payload["choices"][0]
    fields = []
    finished = False
    while True:
        line = response.readline()
        if not line:
            break
        line = line.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            fields.append(line[5:].lstrip(" "))
        elif not line and fields:
            data = "\n".join(fields)
            fields = []
            if data == "[DONE]":
                finished = True
                break
            chunk = json.loads(data)
            for key in ("id", "model", "provider", "created", "usage", "error"):
                if key in chunk:
                    payload[key] = chunk[key]
            if chunk.get("error"):
                return payload
            for item in chunk.get("choices", []):
                if item.get("index", 0) != 0:
                    continue
                for key in ("finish_reason", "native_finish_reason"):
                    if item.get(key) is not None:
                        choice[key] = item[key]
                delta = item.get("delta") or {}
                for key in ("content", "reasoning", "reasoning_content"):
                    if isinstance(delta.get(key), str):
                        choice["message"][key] = choice["message"].get(key, "") + delta[key]
                if on_activity and (
                    any(isinstance(delta.get(key), str) and delta[key]
                        for key in ("content", "reasoning", "reasoning_content"))
                    or delta.get("reasoning_details")
                ):
                    on_activity()
                if delta.get("reasoning_details"):
                    choice["message"].setdefault("reasoning_details", []).extend(delta["reasoning_details"])
    if not finished or not choice["finish_reason"]:
        raise ValueError("Provider stream ended before the response completed.")
    return payload


def fetch_completion(request, response_timeout, timing, connection_timeout=10):
    """Connection includes DNS/TLS and waiting for response headers, not just TCP."""
    events = queue.Queue()
    expired = threading.Event()
    responses = []
    started = time.monotonic()
    last_content = [None]
    activity_lock = threading.Lock()

    def fetch():
        try:
            with urllib.request.urlopen(request, timeout=connection_timeout) as response:
                responses.append(response)
                if expired.is_set():
                    return
                connected = time.monotonic()
                # urllib's connection timeout would otherwise also limit every body read.
                try:
                    response.fp.raw._sock.settimeout(response_timeout)
                except AttributeError:
                    pass
                events.put(("connected", (connected, response.status, getattr(response, "headers", None))))
                callback = getattr(request, "progress_callback", None)
                def report(event):
                    if event == "activity" and not expired.is_set():
                        with activity_lock:
                            last_content[0] = time.monotonic()
                    if callback and not expired.is_set():
                        callback(event)
                report("connected")
                events.put(("done", (read_completion(response, lambda: report("activity")), time.monotonic())))
        except Exception as exc:
            events.put(("error", exc))

    def abort():
        expired.set()
        def close():
            for response in responses:
                try:
                    response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    response.close()
                except Exception:
                    pass
        threading.Thread(target=close, daemon=True).start()

    threading.Thread(target=fetch, daemon=True).start()
    try:
        kind, value = events.get(timeout=connection_timeout)
    except queue.Empty:
        timing["connection_ms"] = round((time.monotonic() - started) * 1000)
        abort()
        raise ConnectionDeadline(f"No response headers within {connection_timeout:g} seconds; trying another provider.")
    if kind == "error":
        timing["connection_ms"] = round((time.monotonic() - started) * 1000)
        if isinstance(value, (TimeoutError, urllib.error.URLError)) and (
            isinstance(value, TimeoutError) or isinstance(value.reason, TimeoutError)
        ):
            raise ConnectionDeadline(f"Connection timed out after {connection_timeout:g} seconds.") from value
        raise value
    connected, status, headers = value
    timing["connection_ms"] = round((connected - started) * 1000)
    timing["response_inactivity_timeout_seconds"] = response_timeout
    while True:
        with activity_lock:
            last = last_content[0] or connected
        remaining = response_timeout - (time.monotonic() - last)
        try:
            kind, value = events.get(timeout=max(0, remaining))
            break
        except queue.Empty:
            with activity_lock:
                last = last_content[0] or connected
            if time.monotonic() - last < response_timeout:
                continue
            timing["response_ms"] = round((time.monotonic() - connected) * 1000)
            timing["content_idle_ms"] = round((time.monotonic() - last) * 1000)
            timing["received_content"] = last_content[0] is not None
            abort()
            raise TimeoutError(f"Provider sent no new answer or reasoning content for {response_timeout:g} seconds.")
    timing["response_ms"] = round((time.monotonic() - connected) * 1000)
    if kind == "error":
        raise value
    payload, finished = value
    timing["response_ms"] = round((finished - connected) * 1000)
    return payload, status, headers
