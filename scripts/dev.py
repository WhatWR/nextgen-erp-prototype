from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apps" / "order-intake-api"
WEB = ROOT / "vendor" / "erpclaw-web"


def main() -> int:
    node = shutil.which("node")
    vite = WEB / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite.exists():
        print("ERPClaw Web dependencies are missing.")
        print(f"Run: cd '{WEB}' && npm ci")
        return 1

    env = os.environ.copy()
    env.setdefault("VITE_ORDER_INTAKE_API", "http://127.0.0.1:8200")
    env.setdefault("ORDER_INTAKE_AUTO_SEED", "1")

    commands = [
        (
            "Order Intake API",
            [sys.executable, str(API / "run.py"), "--host", "127.0.0.1", "--port", "8200"],
            API,
        ),
        (
            "NextGen ERP Web",
            [node, str(vite), "dev", "--host", "127.0.0.1", "--port", "5173"],
            WEB,
        ),
    ]

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command, cwd in commands:
            processes.append((name, subprocess.Popen(command, cwd=cwd, env=env)))
        print("\nPrototype is starting:")
        print("  Review workspace: http://127.0.0.1:5173/order-intake")
        print("  Order Intake API: http://127.0.0.1:8200/health")
        print("\nPress Ctrl+C to stop both services.\n")
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"{name} stopped with exit code {code}")
                    return code
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
