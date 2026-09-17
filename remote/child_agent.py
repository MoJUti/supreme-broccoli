"""One-shot read-only server agent. Receives one JSON request on stdin."""

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone


MAX_REQUEST_BYTES = 8192
SERVICE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$")


def collect_memory() -> dict[str, int]:
    values: dict[str, int] = {}
    with Path("/proc/meminfo").open(encoding="ascii") as stream:
        for line in stream:
            match = re.match(r"^(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+(\d+)\s+kB", line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
    return values


def execute(request: object) -> dict[str, object]:
    if not isinstance(request, dict) or request.get("version") != 1:
        raise ValueError("unsupported_request")
    action = request.get("action")
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("invalid_params")
    if action == "identity":
        if params:
            raise ValueError("unexpected_params")
        data: dict[str, object] = {"hostname": socket.gethostname(),
                                   "uid": os.getuid() if hasattr(os, "getuid") else None}
    elif action == "system_snapshot":
        if params:
            raise ValueError("unexpected_params")
        if sys.platform != "linux":
            raise RuntimeError("linux_only")
        disk = os.statvfs("/")
        data = {
            "disk_root": {"total_bytes": disk.f_blocks * disk.f_frsize,
                          "available_bytes": disk.f_bavail * disk.f_frsize},
            "memory": collect_memory(),
        }
    elif action == "service_status":
        if set(params) != {"name"} or not isinstance(params["name"], str):
            raise ValueError("invalid_service_name")
        name = params["name"]
        if not SERVICE_NAME.fullmatch(name) or name.startswith("-"):
            raise ValueError("invalid_service_name")
        result = subprocess.run(
            ["systemctl", "show", "--no-pager", "--property=LoadState,ActiveState,SubState", name],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("service_check_failed")
        data = {key: value for key, value in
                (line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)}
    else:
        raise ValueError("action_not_allowed")
    return {"version": 1, "action": action, "status": "ok",
            "host": socket.gethostname(),
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": data}


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        response = execute(json.loads(raw))
    except json.JSONDecodeError:
        response = {"version": 1, "status": "error", "error_code": "invalid_json"}
    except (ValueError, RuntimeError) as error:
        response = {"version": 1, "status": "error", "error_code": str(error)}
    except (OSError, subprocess.TimeoutExpired):
        response = {"version": 1, "status": "error", "error_code": "system_error"}
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
