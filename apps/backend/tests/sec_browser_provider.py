"""Deterministic OpenAI-compatible HTTP Provider for the real SEC browser journey."""

from __future__ import annotations

import argparse
import json
import threading
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

MAX_REQUEST_BYTES = 1_000_000


class SecBrowserProviderHandler(BaseHTTPRequestHandler):
    server_version = "SecBrowserProvider/1"
    protocol_version = "HTTP/1.1"
    counts: ClassVar[Counter[str]] = Counter()
    counter_lock: ClassVar[threading.Lock] = threading.Lock()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if self.path == "/state":
            with self.counter_lock:
                counts = dict(sorted(self.counts.items()))
            self._json(HTTPStatus.OK, {"schema_version": 1, "decisions": counts})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if self.headers.get("Authorization") != "Bearer sec-browser-controlled-key":
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise ValueError
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict) or body.get("stream") is not False:
                raise ValueError
            if body.get("model") != "sec-browser-model":
                raise ValueError
            messages = body.get("messages")
            if not isinstance(messages, list):
                raise ValueError
            contents: list[str] = []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    contents.append(content)
            joined = "\n".join(contents)
            output, decision_kind = _decision(joined)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return

        with self.counter_lock:
            self.counts[decision_kind] += 1
            ordinal = sum(self.counts.values())
        self._json(
            HTTPStatus.OK,
            {
                "id": f"sec-browser-{ordinal}",
                "object": "chat.completion",
                "model": "sec-browser-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": output},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 25,
                    "total_tokens": 65,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def _decision(messages: str) -> tuple[str, str]:
    if "sec-monitor:" in messages:
        return (
            json.dumps(
                {
                    "decision": {
                        "schema_version": 1,
                        "kind": "final",
                        "content_markdown": (
                            "## 审查结论\n\nApple 2023 财年净销售额为 3832.85 亿美元 [S1]。"
                            "经人工批准，SEC Monitor 已创建；本结论仅用于受控链路验证。"  # noqa: RUF001
                        ),
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "final",
        )
    if '"retrieval_profile_version"' in messages:
        cik, knowledge_base_id = _locked_context(messages)
        return (
            json.dumps(
                {
                    "decision": {
                        "schema_version": 1,
                        "kind": "tool_call",
                        "name": "sec.monitor.subscribe",
                        "version": "v1",
                        "arguments": {
                            "cik": cik,
                            "knowledge_base_id": knowledge_base_id,
                            "allowed_forms": ["10-K"],
                            "cron_expression": "0 3 * * *",
                            "timezone_name": "Asia/Shanghai",
                            "rules": [
                                {
                                    "kind": "new_filing",
                                    "section_query": "Financial Statements and Supplementary Data",
                                    "taxonomy": None,
                                    "concept": None,
                                    "unit": None,
                                    "threshold": None,
                                    "comparator": None,
                                }
                            ],
                        },
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "monitor_subscription",
        )
    return (
        json.dumps(
            {
                "decision": {
                    "schema_version": 1,
                    "kind": "tool_call",
                    "name": "sec.search_filing",
                    "version": "v1",
                    "arguments": {"query": "net sales"},
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "filing_search",
    )


def _locked_context(messages: str) -> tuple[str, str]:
    marker = "Server-locked Financial Scope."
    for block in messages.split("\n"):
        if not block.startswith("{") or "knowledge_base_ids" not in block:
            continue
        value = json.loads(block)
        if not isinstance(value, dict):
            continue
        financial_scope = value.get("financial_scope")
        knowledge_base_ids = value.get("knowledge_base_ids")
        if not isinstance(financial_scope, dict) or not isinstance(knowledge_base_ids, list):
            continue
        cik = financial_scope.get("cik")
        knowledge_base_id = knowledge_base_ids[0] if knowledge_base_ids else None
        if isinstance(cik, str) and isinstance(knowledge_base_id, str):
            return cik, knowledge_base_id
    raise ValueError(f"{marker} context is missing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18081, type=int)
    arguments = parser.parse_args()
    server = ThreadingHTTPServer((arguments.host, arguments.port), SecBrowserProviderHandler)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
