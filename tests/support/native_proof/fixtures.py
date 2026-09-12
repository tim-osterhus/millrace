"""Scripted external HTTP only; Core/native product composition is unchanged."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from cli.test_cli_bounded_execution_unit import _ready_state_for_plan, _runtime
from millrace.adapters.cli.daemon_control import _open
from support.kernel_ping import compile_kernel_ping, task_artifact_payload
from support.run_controls import request_for

CORE = Path(__file__).resolve().parents[3]


def model_profile(port):
    from millforge import (
        AuthenticationPolicy,
        EndpointConfig,
        ResolvedModelProfile,
        SecretRef,
    )
    from millforge.model_backend import (
        CapabilityDeclarations,
        CapabilitySupport,
        RequestOptionAllowlist,
    )

    secret = SecretRef(secret_id="native-proof-local", env_var="CORE_NATIVE_PROOF_KEY")
    profile = ResolvedModelProfile(
        profile_id="native-proof-local",
        provider_id="scripted-external-local",
        model_id="native-proof-local",
        endpoint=EndpointConfig(
            base_url=f"http://127.0.0.1:{port}/v1", allow_insecure_local=True
        ),
        authentication=AuthenticationPolicy(scheme="bearer", secret_ref=secret),
        capabilities=CapabilityDeclarations(
            support={
                name: CapabilitySupport.SUPPORTED
                for name in ("tool_calls", "system_messages", "tool_result_messages")
            }
        ),
        request_options=RequestOptionAllowlist(
            allowed_options=("parallel_tool_calls",)
        ),
        source_digest="local-script-v1",
    )
    return profile, secret


def compile_native_plan():
    """Explicitly compile proof authority for the current native descriptor."""
    from millforge.base.identity import describe_millforge_base

    from millrace.workflows import kernel_ping

    source = kernel_ping.workflow_source()
    for binding in source["runner_bindings"]:
        pin = binding["component_pin"]
        descriptor = describe_millforge_base(
            legal_terminal_results=tuple(pin["legal_terminal_result_ids"])
        )
        pin["provider_version"] = descriptor.package_version
        pin["descriptor_sha256"] = descriptor.descriptor_sha256
    return compile_kernel_ping(source)


class ModelServer:
    def __init__(self, delay=1.0, unsupported=None):
        self.count = 0
        self.delay = delay
        self.unsupported = unsupported
        self.started = threading.Event()
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                owner.count += 1
                index = 1 + sum(
                    len(message.get("tool_calls", [])) for message in body["messages"]
                )
                prefix = "b-" if "independent-B" in json.dumps(body["messages"]) else ""
                if index == 3:
                    owner.started.set()
                    time.sleep(owner.delay)
                calls = [
                    ("write", {"path": prefix + "before.txt", "content": "before"}),
                    ("read", {"path": prefix + "before.txt"}),
                    ("write", {"path": prefix + "after.txt", "content": "after"}),
                    (
                        "terminal_task_complete",
                        {
                            "terminal_result": "TASK_COMPLETE",
                            "summary": "native work complete",
                            "candidate": task_artifact_payload(),
                        },
                    ),
                ]
                if owner.unsupported is not None and index == 3:
                    name, args = owner.unsupported
                else:
                    name, args = calls[min(index - 1, 3)]
                response = {
                    "model": body["model"],
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "local scripted response",
                                "tool_calls": [
                                    {
                                        "id": f"call-{index}",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": json.dumps(args),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                }
                raw = json.dumps(response).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(2)


class LiveCase:
    def __init__(
        self,
        label,
        delay=1.0,
        timeout=30,
        unsupported=None,
        tool_lock=0,
        deadline_trap=False,
        admission_trap=False,
    ):
        self.label = label
        self.root = Path(os.environ["MILLRACE_TEST_SOCKET_ROOT"]) / (
            "e" + uuid4().hex[:5]
        )
        self.root.mkdir(parents=True)
        self.server = ModelServer(delay, unsupported)
        plan, fingerprint = compile_native_plan()
        state, _ = _ready_state_for_plan(plan, fingerprint)
        runtime = _runtime(self.root, state)
        self.paths = runtime.paths
        runtime.close()
        profile, secret = model_profile(self.server.http.server_port)
        profile_payload = profile.model_dump(mode="json")
        profile_payload.pop("configured_headers", None)
        self.config = self.root / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "millforge": {
                        "adapter_id": "millforge",
                        "workspace_root": str(self.paths.workspace_path),
                        "timeout_seconds": timeout,
                        "model_profile": profile_payload,
                        "secret_ref": secret.model_dump(mode="json"),
                        "redaction_policy": {
                            "policy_id": "native-proof-local",
                            "secret_tokens": [],
                        },
                    }
                }
            )
        )
        (self.root / "case.json").write_text(
            json.dumps(
                {
                    "label": label,
                    "plan_fingerprint": fingerprint,
                    "delay_seconds": delay,
                    "native_timeout_seconds": timeout,
                    "stock_tool_lock_seconds": tool_lock,
                    "deadline_trap": deadline_trap,
                    "admission_trap": admission_trap,
                    "unsupported_effect": unsupported,
                    "external_responses": "locally scripted",
                    "evidence_level": (
                        "actual public CLI native owner; scripted model HTTP"
                    ),
                },
                indent=2,
            )
        )
        env = dict(os.environ)
        env["CORE_NATIVE_PROOF_KEY"] = "local-script-only"
        env["CORE_NATIVE_TOOL_LOCK_SECONDS"] = str(tool_lock)
        env["CORE_NATIVE_DEADLINE_TRAP"] = "1" if deadline_trap else "0"
        env["CORE_NATIVE_ADMISSION_TRAP"] = "1" if admission_trap else "0"
        launcher = (
            [sys.executable, str(Path(__file__).with_name("process_case.py"))]
            if tool_lock or deadline_trap or admission_trap
            else [
                sys.executable,
                "-c",
                "from millrace.adapters.cli.main import main; raise SystemExit(main())",
            ]
        )
        self.child = subprocess.Popen(
            [
                *launcher,
                "--json",
                "--workspace",
                str(self.paths.workspace_path),
                "run",
                "daemon",
                "--idle-sleep",
                "0.05",
                "--max-invocations",
                "1",
                "--budget-id",
                "native-proof-epoch",
                "--adapter-config-json",
                str(self.config),
                "--launch-correlation-id",
                label,
            ],
            cwd=CORE,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def runtime(self):
        return _open(self.paths)

    def state(self):
        runtime = self.runtime()
        try:
            return runtime.store.load_runtime_state(runtime.cas_store)
        finally:
            runtime.close()

    def request(self, **changes):
        runtime = self.runtime()
        try:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            run = next(iter(state.runs.values()))
            from millforge.pause_control import PROFILE, PROFILE_DIGEST

            return request_for(
                runtime,
                run,
                profile={**PROFILE, "profile_digest": PROFILE_DIGEST},
                **changes,
            )
        finally:
            runtime.close()

    def invoke(self, request):
        started = time.monotonic()
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from millrace.adapters.cli.main import main; raise SystemExit(main())",
                "--json",
                "--workspace",
                str(self.paths.workspace_path),
                "runs",
                request.payload["action"].split(".")[1],
                "--request-json",
                request.canonical,
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
            timeout=6,
        )
        with (self.root / "client-timings.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "action": request.payload["action"],
                        "operation_id": request.payload["operation_id"],
                        "elapsed_seconds": time.monotonic() - started,
                        "exit_code": result.returncode,
                    }
                )
                + "\n"
            )
        return result

    def wait(self, predicate, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            if self.child.poll() is not None:
                out, err = self.child.communicate()
                raise AssertionError((self.child.returncode, out, err, self.root))
            time.sleep(0.02)
        raise AssertionError(("wait expired", self.root))

    def close(self):
        if self.child.poll() is None:
            self.child.terminate()
        try:
            out, err = self.child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.child.kill()
            out, err = self.child.communicate(timeout=2)
        (self.root / "scripted-requests.json").write_text(
            json.dumps(self.server.requests, indent=2)
        )
        (self.root / "daemon.stdout").write_text(out)
        (self.root / "daemon.stderr").write_text(err)
        self.server.close()
