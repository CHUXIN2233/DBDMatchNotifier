from __future__ import annotations

import json
import os
import queue
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbd_notification import MatchEvent, NotificationSink
from dbd_runtime import safe_print
from dbd_runtime import APP_DATA_DIR


BASE_URL = "https://ilinkai.weixin.qq.com"
APP_ID = "bot"
CHANNEL_VERSION = "2.4.9"
BOT_AGENT = "DBDMatchNotifier/0.1"
DEFAULT_STATE = APP_DATA_DIR / "wechat_state.json"
DEFAULT_LOG = APP_DATA_DIR / "dbd_wechat.log"


def log(path: Path, message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    safe_print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class WeChatApiError(RuntimeError):
    def __init__(self, operation: str, response: dict[str, Any] | None = None, detail: str = ""):
        self.operation = operation
        self.response = response or {}
        ret = self.response.get("ret", self.response.get("errcode", ""))
        super().__init__(f"{operation} failed ret={ret} errmsg={self.response.get('errmsg', detail)}")


class ILinkClient:
    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token or ""

    def _headers(self) -> dict[str, str]:
        # Mirrors Tencent/openclaw-weixin/src/api/api.ts buildHeaders().
        uin = secrets.token_bytes(4)
        import base64
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": base64.b64encode(str(int.from_bytes(uin, "big")).encode()).decode(),
            "iLink-App-Id": APP_ID,
            "iLink-App-ClientVersion": str((2 << 16) | (4 << 8) | 9),
            **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
        }

    def post(self, endpoint: str, body: dict[str, Any], timeout: float = 35.0) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/{endpoint.lstrip('/')}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except Exception:
                detail = {"errmsg": raw[:300]}
            raise WeChatApiError(endpoint, detail) from exc
        except Exception as exc:
            raise WeChatApiError(endpoint, detail=str(exc)) from exc
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise WeChatApiError(endpoint, detail=f"invalid JSON: {raw[:200]}") from exc
        return data


def base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT}


def login_qr(state_path: Path, log_path: Path) -> int:
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    tokens = [state["token"]] if state.get("token") else []
    client = ILinkClient(BASE_URL)
    try:
        qr = client.post("ilink/bot/get_bot_qrcode?bot_type=3", {"local_token_list": tokens}, timeout=15)
        qrcode = qr["qrcode"]
        qr_url = qr.get("qrcode_img_content", "")
        safe_print("请用手机微信扫描二维码并确认授权。", flush=True)
        safe_print(qr_url, flush=True)
        if qr_url:
            try:
                webbrowser.open(qr_url)
            except Exception:
                pass
        deadline = time.time() + 480
        verify_code = None
        while time.time() < deadline:
            endpoint = "ilink/bot/get_qrcode_status?qrcode=" + urllib.parse.quote(qrcode, safe="")
            if verify_code:
                endpoint += "&verify_code=" + urllib.parse.quote(verify_code, safe="")
            status = ILinkClient(BASE_URL).post(endpoint, {}, timeout=40) if False else None
            req = urllib.request.Request(f"{BASE_URL}/{endpoint}", headers=client._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=40) as response:
                    status = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                log(log_path, f"QR status poll failed; retrying: {exc}")
                time.sleep(1)
                continue
            name = status.get("status")
            if name == "confirmed":
                new_state = {
                    **state,
                    "token": status.get("bot_token", ""),
                    "bot_id": status.get("ilink_bot_id", ""),
                    "base_url": status.get("baseurl") or BASE_URL,
                    "user_id": status.get("ilink_user_id", ""),
                    "context_token": "",
                    "get_updates_buf": "",
                }
                atomic_json_write(state_path, new_state)
                log(log_path, f"WeChat QR login confirmed; credentials saved to {state_path}")
                return 0
            if name == "need_verifycode":
                verify_code = input("请输入微信显示的验证码后回车：").strip()
            elif name in {"expired", "verify_code_blocked"}:
                log(log_path, f"QR login ended with status={name}; please run login again")
                return 2
            time.sleep(1)
        log(log_path, "QR login timed out; please run login again")
        return 2
    except Exception as exc:
        log(log_path, f"QR login failed: {exc}")
        return 2


class WeChatNotifier(NotificationSink):
    """Non-blocking WeChat iLink notifier with a background receive/send worker."""

    def __init__(self, state_path: Path = DEFAULT_STATE, log_path: Path = DEFAULT_LOG):
        self.state_path = state_path
        self.log_path = log_path
        self.state: dict[str, Any] = {}
        self.events: queue.Queue[MatchEvent] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, name="wechat-ilink", daemon=True)

    def start(self) -> None:
        self._load()
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def notify_match(self, event: MatchEvent) -> None:
        # Queue-only: never perform network I/O on the DBD detection thread.
        try:
            self.events.put_nowait(event)
        except Exception as exc:
            log(self.log_path, f"WeChat event enqueue failed: {exc}")

    def _load(self) -> None:
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        except Exception as exc:
            self.state = {}
            log(self.log_path, f"WeChat state load failed: {exc}; re-authentication may be required")

    def _save(self) -> None:
        atomic_json_write(self.state_path, self.state)

    def _worker(self) -> None:
        if not self.state.get("token"):
            log(self.log_path, "WeChat is not authenticated; run: python dbd_wechat_notifier.py login")
        receiver = threading.Thread(target=self._receive_loop, name="wechat-getupdates", daemon=True)
        receiver.start()
        while not self.stop_event.is_set():
            try:
                event = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._send_match(event)
            except Exception as exc:
                log(self.log_path, f"WeChat send failed (DBD detector unaffected): {exc}")

    def _receive_loop(self) -> None:
        while not self.stop_event.is_set():
            token = self.state.get("token")
            if not token:
                time.sleep(5)
                continue
            try:
                client = ILinkClient(self.state.get("base_url") or BASE_URL, token)
                response = client.post("ilink/bot/getupdates", {
                    "get_updates_buf": self.state.get("get_updates_buf", ""),
                    "base_info": base_info(),
                }, timeout=40)
                if response.get("errcode") in {-14, -4} or (response.get("ret") not in (None, 0)):
                    raise WeChatApiError("getupdates", response)
                if response.get("get_updates_buf"):
                    self.state["get_updates_buf"] = response["get_updates_buf"]
                for msg in response.get("msgs") or []:
                    user_id = msg.get("from_user_id")
                    context = msg.get("context_token")
                    if user_id and context:
                        self.state["user_id"] = user_id
                        self.state["context_token"] = context
                        self._save()
                        log(self.log_path, f"WeChat inbound session saved for user_id={user_id}")
                self._save()
            except Exception as exc:
                log(self.log_path, f"WeChat getupdates failed; re-authentication may be required: {exc}")
                time.sleep(5)

    def _send_match(self, event: MatchEvent) -> None:
        token = self.state.get("token")
        user_id = self.state.get("user_id")
        context = self.state.get("context_token")
        if not token or not user_id:
            raise RuntimeError("missing token/user_id; run QR login and send a message to the ClawBot")
        client_id = secrets.token_hex(16)
        client = ILinkClient(self.state.get("base_url") or BASE_URL, token)
        detected_at = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
        waited_total_seconds = max(0, int(event.waited_seconds))
        waited_hours, remainder = divmod(waited_total_seconds, 3600)
        waited_minutes, waited_seconds = divmod(remainder, 60)
        waited_text = f"{waited_hours:02d}:{waited_minutes:02d}:{waited_seconds:02d}"
        message_text = (
            "🎮 黎明杀机匹配到了，快回来！\n"
            f"检测时间：{detected_at}\n"
            f"已等待：{waited_text}"
        )
        response = client.post("ilink/bot/sendmessage", {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "context_token": context or None,
                "item_list": [{"type": 1, "text_item": {"text": message_text}}],
            },
            "base_info": base_info(),
        }, timeout=15)
        if response.get("ret") not in (None, 0):
            raise WeChatApiError("sendmessage", response)
        log(self.log_path, f"WeChat MATCH DETECTED sent to user_id={user_id}")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["login"], nargs="?")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()
    if args.command == "login":
        return login_qr(args.state, args.log)
    safe_print(f"State: {args.state}")
    safe_print("Run `python dbd_wechat_notifier.py login` to authenticate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
