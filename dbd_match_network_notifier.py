from __future__ import annotations

import json
import os
import subprocess
import time
import argparse
from pathlib import Path

from dbd_notification import CompositeNotificationSink, LogNotificationSink, MatchEvent
from dbd_wechat_notifier import WeChatNotifier
from dbd_runtime import APP_DATA_DIR, acquire_single_instance, release_single_instance, safe_print


POLL_SECONDS = 1.0
LOG_FILE = APP_DATA_DIR / "dbd_match_network_notifier.log"
MATCH_LOG_FILE = APP_DATA_DIR / "dbd_match_events.log"


def write_log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    safe_print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


PS = r'''
$names = @('DeadByDaylight','DeadByDaylight-Win64-Shipping')
$pids = @(Get-Process -Name $names -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$udp = @(Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object {$_.OwningProcess -in $pids} | ForEach-Object {
  [pscustomobject]@{pid=$_.OwningProcess;local="$($_.LocalAddress):$($_.LocalPort)"}
})
[pscustomobject]@{pids=$pids;udp=$udp} | ConvertTo-Json -Compress -Depth 4
'''


def snapshot() -> dict:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS],
        capture_output=True, text=True, timeout=8,
    )
    if not result.stdout.strip():
        return {"pids": [], "udp": [], "error": result.stderr.strip()}
    value = json.loads(result.stdout)
    if isinstance(value.get("pids"), int):
        value["pids"] = [value["pids"]]
    if isinstance(value.get("udp"), dict):
        value["udp"] = [value["udp"]]
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wechat", action="store_true", help="disable WeChat delivery while keeping local event logs")
    args = parser.parse_args()
    mutex = acquire_single_instance()
    if mutex is None:
        safe_print("DBDMatchNotifier is already running.", flush=True)
        return
    # State machine: baseline is established per DBD process lifetime. Candidate
    # ports are never merged into baseline, so they can identify later sessions.
    state = "WAITING_FOR_DBD"
    baseline: set[str] = set()
    baseline_pids: tuple[int, ...] = ()
    previous: set[str] = set()
    candidate: str | None = None
    candidate_first_seen: float | None = None
    candidate_confirmed_at: float | None = None
    wechat = None if args.no_wechat else WeChatNotifier()
    if wechat:
        wechat.start()
    sinks = [LogNotificationSink(MATCH_LOG_FILE)]
    if wechat:
        sinks.append(wechat)
    sink = CompositeNotificationSink(*sinks)
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_log("started; baseline/candidate UDP state machine; notifications enabled")
    try:
        while True:
            try:
                value = snapshot()
                pids = tuple(sorted(int(pid) for pid in value.get("pids", [])))
                ports = {item["local"] for item in value.get("udp", [])}
                now = time.time()

                if not pids:
                    if state != "WAITING_FOR_DBD":
                        write_log("DBD exited; clearing baseline, candidate, and session state")
                    state = "WAITING_FOR_DBD"
                    baseline = set()
                    baseline_pids = ()
                    previous = set()
                    candidate = None
                    candidate_first_seen = None
                    candidate_confirmed_at = None
                    time.sleep(POLL_SECONDS)
                    continue

                if pids != baseline_pids:
                    baseline_pids = pids
                    baseline = set(ports)
                    previous = set(ports)
                    candidate = None
                    candidate_first_seen = None
                    candidate_confirmed_at = None
                    state = "ARMED"
                    write_log(f"DBD started/restarted; baseline established pids={list(pids)} endpoints={sorted(baseline)}")
                    time.sleep(POLL_SECONDS)
                    continue

                added = ports - previous
                removed = previous - ports
                if added or removed:
                    write_log(f"UDP diff added={sorted(added)} removed={sorted(removed)} state={state}")

                if state == "ARMED":
                    new_ports = sorted(ports - baseline)
                    if candidate is None and new_ports:
                        candidate = new_ports[0]
                        candidate_first_seen = now
                        candidate_confirmed_at = None
                        write_log(f"CANDIDATE first_seen={candidate_first_seen:.3f} port={candidate}")
                        state = "CANDIDATE"

                if state == "CANDIDATE" and candidate is not None:
                    if candidate not in ports:
                        lifetime = now - (candidate_first_seen or now)
                        write_log(f"CANDIDATE disappeared before confirmation port={candidate} lifetime_seconds={lifetime:.1f}")
                        candidate = None
                        candidate_first_seen = None
                        state = "ARMED"
                    elif candidate_first_seen is not None and now - candidate_first_seen >= 2.5:
                        candidate_confirmed_at = now
                        write_log(f"MATCH_DETECTED candidate={candidate} first_seen={candidate_first_seen:.3f} confirmed={candidate_confirmed_at:.3f}")
                        try:
                            sink.notify_match(MatchEvent(now, (candidate,), now - (candidate_first_seen or now)))
                            write_log("notification event dispatched")
                        except Exception as exc:
                            write_log(f"notification dispatch failed: {type(exc).__name__}: {exc}")
                        state = "MATCHED"

                if state == "MATCHED" and candidate is not None and candidate not in ports:
                    lifetime = now - (candidate_first_seen or now)
                    write_log(f"SESSION ENDED candidate={candidate} first_seen={candidate_first_seen:.3f} confirmed={candidate_confirmed_at:.3f} disappeared={now:.3f} lifetime_seconds={lifetime:.1f}")
                    candidate = None
                    candidate_first_seen = None
                    candidate_confirmed_at = None
                    state = "ARMED"

                previous = set(ports)
                time.sleep(POLL_SECONDS)
            except KeyboardInterrupt:
                write_log("stopped")
                return
            except Exception as exc:
                write_log(f"observer error (recovering): {type(exc).__name__}: {exc}")
                time.sleep(POLL_SECONDS)
    finally:
        if wechat:
            wechat.stop()
        release_single_instance(mutex)


if __name__ == "__main__":
    main()
