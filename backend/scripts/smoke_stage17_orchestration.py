"""Opt-in Stage 17 -> real Video Composer smoke for a prepared gap-free workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--orchestrator-url", default="http://localhost:8011")
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--output", type=Path, default=Path("stage17-smoke.mp4"))
    args = parser.parse_args()
    base_url = args.orchestrator_url.rstrip("/")
    started = time.monotonic()
    try:
        with httpx.Client(base_url=base_url, timeout=30) as client:
            workflow = client.get(f"/workflow/{args.workflow_id}")
            workflow.raise_for_status()
            state = workflow.json()
            if state.get("production_status") in {None, "blocked", "cancelled"}:
                resumed = client.post(f"/workflow/{args.workflow_id}/resume-production")
                resumed.raise_for_status()
            deadline = time.monotonic() + args.timeout_seconds
            while time.monotonic() < deadline:
                state_response = client.get(f"/workflow/{args.workflow_id}")
                state_response.raise_for_status()
                state = state_response.json()
                status = state.get("production_status")
                if status in {"blocked", "failed", "cancelled"}:
                    raise RuntimeError(
                        f"production {status}: {state.get('production_error_category')} "
                        f"{state.get('production_error_message')}"
                    )
                if status == "completed":
                    break
                time.sleep(2)
            else:
                raise TimeoutError("Stage 17 production did not complete before timeout")

            digest = hashlib.sha256()
            byte_size = 0
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with client.stream("GET", f"/workflow/{args.workflow_id}/video") as response:
                response.raise_for_status()
                if response.headers.get("content-type", "").split(";", 1)[0] != "video/mp4":
                    raise RuntimeError("final endpoint did not return video/mp4")
                with args.output.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
                        digest.update(chunk)
                        byte_size += len(chunk)
            if byte_size <= 0:
                raise RuntimeError("final video is empty")
            render = client.get(f"/workflow/{args.workflow_id}/render")
            render.raise_for_status()
            report = {
                "smoke": "STAGE17_ORCHESTRATION_RENDER_SMOKE",
                "status": "PASS",
                "workflow_id": args.workflow_id,
                "render": render.json(),
                "output": str(args.output.resolve()),
                "byte_size": byte_size,
                "sha256": digest.hexdigest(),
                "wall_seconds": round(time.monotonic() - started, 3),
            }
            print(json.dumps(report, indent=2))
            return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "smoke": "STAGE17_ORCHESTRATION_RENDER_SMOKE",
                    "status": "FAIL",
                    "reason": str(exc),
                },
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
