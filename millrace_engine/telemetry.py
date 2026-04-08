"""Runner telemetry helpers."""

from __future__ import annotations

import contextlib
import json
import math
import os
import selectors
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator

from .contracts import CodexUsageSummary, ContractModel

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_NO_USAGE = 4
EXIT_MALFORMED = 5
DEFAULT_USAGE_CACHE_MAX_AGE_SECS = 900
USAGE_ENV_CANDIDATES = {
    "orchestrate": (
        "USAGE_SAMPLER_ORCH_CURRENT",
        "ORCH_WEEKLY_USAGE_CURRENT",
        "WEEKLY_USAGE_CURRENT",
    ),
    "research": (
        "USAGE_SAMPLER_RESEARCH_CURRENT",
        "RESEARCH_WEEKLY_USAGE_CURRENT",
        "WEEKLY_USAGE_CURRENT",
    ),
}


class UsageSamplingError(RuntimeError):
    """Raised when a usage sampler cannot produce a usable current value."""


class WeeklyUsageSample(ContractModel):
    """One normalized weekly-usage sample or fallback result."""

    ok: bool
    loop: Literal["orchestrate", "research"]
    provider: Literal["codex", "env", "command"]
    source: str
    current: Decimal | None = None
    sampled_at: datetime | None = None
    warnings: tuple[str, ...] = ()
    reason: str | None = None

    @field_validator("source", "reason")
    @classmethod
    def normalize_optional_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("current", mode="before")
    @classmethod
    def normalize_current(cls, value: Decimal | str | None) -> Decimal | None:
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            current = value
        else:
            try:
                current = Decimal(str(value).strip())
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid decimal value: {value!r}") from exc
        if current.is_nan() or current.is_infinite() or current < 0:
            raise ValueError("current must be a finite non-negative decimal")
        return current

    @field_validator("sampled_at", mode="before")
    @classmethod
    def normalize_sampled_at(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            moment = value
        else:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @field_validator("warnings", mode="before")
    @classmethod
    def normalize_warnings(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item).strip().split())
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_decimal_text(raw: str) -> str:
    try:
        value = Decimal((raw or "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise UsageSamplingError(f"invalid numeric value: {raw!r}") from exc
    if value.is_nan() or value.is_infinite() or value < 0:
        raise UsageSamplingError(f"invalid non-negative value: {raw!r}")
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"", "-0"}:
        normalized = "0"
    if normalized.startswith("-0."):
        normalized = normalized[1:]
    return normalized


def _parse_iso_utc(raw: str | None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def _cached_age_secs(sampled_at: str | None) -> int | None:
    sampled_dt = _parse_iso_utc(sampled_at)
    if sampled_dt is None:
        return None
    return max(0, int((_utc_now() - sampled_dt).total_seconds()))


def _cached_is_fresh(sampled_at: str | None, max_age_secs: int) -> tuple[bool, int | None]:
    if max_age_secs <= 0:
        return False, None
    age = _cached_age_secs(sampled_at)
    if age is None:
        return False, None
    return age <= max_age_secs, age


def _trusted_cache_source(raw_source: str | None) -> bool:
    source = (raw_source or "").strip().lower()
    return source.startswith("env:") or source.startswith("command:") or source.startswith("codex:")


def _usage_state_file(runtime_dir: Path) -> Path:
    return runtime_dir / "usage_state.json"


def _load_usage_state(path: Path) -> tuple[dict[str, object], list[str]]:
    warnings: list[str] = []
    payload: dict[str, object] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
            else:
                warnings.append("usage state file is not a JSON object; resetting state")
        except Exception:
            warnings.append("usage state file is unreadable JSON; resetting state")
    loops = payload.get("loops")
    if not isinstance(loops, dict):
        loops = {}
    payload["schema_version"] = "1.1"
    payload["loops"] = loops
    return payload, warnings


def _read_cached_entry(
    state: dict[str, object],
    loop: Literal["orchestrate", "research"],
) -> tuple[str | None, str | None, str | None]:
    loops = state.get("loops")
    if not isinstance(loops, dict):
        return None, None, None
    entry = loops.get(loop)
    if not isinstance(entry, dict):
        return None, None, None
    value = entry.get("current")
    if value is None:
        return None, None, None
    try:
        current = _normalize_decimal_text(str(value))
    except UsageSamplingError:
        return None, None, None
    sampled_at_raw = entry.get("sampled_at")
    sampled_at = str(sampled_at_raw).strip() if sampled_at_raw is not None else None
    if sampled_at and _parse_iso_utc(sampled_at) is None:
        sampled_at = None
    source_raw = entry.get("source")
    source = str(source_raw).strip() if source_raw is not None else None
    return current, sampled_at, source


def _sample_from_env(loop: Literal["orchestrate", "research"]) -> tuple[str | None, str | None, list[str]]:
    warnings: list[str] = []
    for key in USAGE_ENV_CANDIDATES[loop]:
        raw = os.environ.get(key)
        if raw is None or raw.strip() == "":
            continue
        try:
            return _normalize_decimal_text(raw), f"env:{key}", warnings
        except UsageSamplingError:
            warnings.append(f"ignored invalid env sample from {key}")
    return None, None, warnings


def _sample_from_command(command: str | None, *, source_label: str) -> tuple[str, str]:
    cmd = (command or "").strip()
    if not cmd:
        raise UsageSamplingError(f"provider=command requires non-empty {source_label}")
    completed = subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr_excerpt = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")
        if len(stderr_excerpt) > 400:
            stderr_excerpt = stderr_excerpt[:400]
        raise UsageSamplingError(
            f"command provider failed ({source_label}, rc={completed.returncode}): {stderr_excerpt or 'no stderr'}"
        )
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise UsageSamplingError(f"command provider returned empty output ({source_label})")
    return _normalize_decimal_text(lines[-1]), f"command:{source_label}"


def _resolve_codex_invocation() -> str | None:
    direct = shutil.which("codex")
    if direct:
        return direct
    hidden_dir = Path("/usr/local/bin")
    if hidden_dir.is_dir():
        for candidate in sorted(hidden_dir.glob(".codex-*")):
            if candidate.exists() and os.access(candidate, os.X_OK):
                return candidate.as_posix()
    js_entry = Path("/usr/local/lib/node_modules/@openai/codex/bin/codex.js")
    if js_entry.is_file() and shutil.which("node"):
        return f"node {js_entry.as_posix()}"
    return None


def _prepare_sampler_codex_home(
    *,
    auth_source_dir: Path | None,
    runtime_home: Path | None,
) -> str:
    base_home = runtime_home or Path("/tmp/millrace-usage-sampler-home")
    runtime_codex_dir = base_home / ".codex"
    base_home.mkdir(parents=True, exist_ok=True)
    runtime_codex_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(base_home, 0o700)
    os.chmod(runtime_codex_dir, 0o700)
    source_codex_dir = auth_source_dir or (Path.home() / ".codex")
    if source_codex_dir.exists() and source_codex_dir.resolve() != runtime_codex_dir.resolve():
        for name in ("auth.json", "config.toml", "credentials.json"):
            src = source_codex_dir / name
            if not src.is_file():
                continue
            for target_dir in (base_home, runtime_codex_dir):
                dst = target_dir / name
                try:
                    dst.write_bytes(src.read_bytes())
                    os.chmod(dst, 0o600)
                except Exception:
                    continue
    return base_home.as_posix()


def _sample_from_codex_app_server(
    *,
    auth_source_dir: Path | None,
    runtime_home: Path | None,
) -> str:
    codex_invocation = _resolve_codex_invocation()
    if codex_invocation is None:
        raise UsageSamplingError("provider=codex requires the codex CLI")
    codex_home = _prepare_sampler_codex_home(
        auth_source_dir=auth_source_dir,
        runtime_home=runtime_home,
    )
    env = os.environ.copy()
    env["HOME"] = codex_home
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CODEX_SESSION_ID", None)
    env.pop("CODEX_CI", None)
    command = [*shlex.split(codex_invocation), "app-server"]
    timeout_secs = 15.0

    def _read_json_lines(
        proc: subprocess.Popen[str],
        timeout_window_secs: float,
        stop_predicate: object | None = None,
    ) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        if proc.stdout is None:
            return messages
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            deadline = time.time() + max(0.0, timeout_window_secs)
            while time.time() < deadline:
                timeout = max(0.0, deadline - time.time())
                events = selector.select(timeout)
                if not events:
                    break
                for key, _ in events:
                    line = key.fileobj.readline()
                    if not line:
                        if proc.poll() is not None:
                            return messages
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        messages.append(obj)
                        if callable(stop_predicate) and stop_predicate(obj):
                            return messages
        finally:
            selector.close()
        return messages

    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )

        def send_message(payload: dict[str, object]) -> None:
            assert proc is not None and proc.stdin is not None
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

        send_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "millrace-usage-sampler", "version": "1.0.0"},
                    "capabilities": {},
                },
            }
        )
        messages = _read_json_lines(proc, min(timeout_secs, 3.0), lambda msg: str(msg.get("id")) == "1")
        init_ok = any(str(msg.get("id")) == "1" and "result" in msg for msg in messages)
        if not init_ok:
            raise UsageSamplingError("codex app-server initialize failed")

        send_message({"jsonrpc": "2.0", "method": "initialized"})
        send_message({"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {"refreshToken": True}})
        messages.extend(_read_json_lines(proc, min(timeout_secs, 5.0), lambda msg: str(msg.get("id")) == "2"))
        send_message({"jsonrpc": "2.0", "id": 3, "method": "account/rateLimits/read"})
        messages.extend(
            _read_json_lines(
                proc,
                max(1.0, timeout_secs - 5.0),
                lambda msg: str(msg.get("id")) == "3" or msg.get("method") == "account/rateLimits/updated",
            )
        )
    except OSError as exc:
        raise UsageSamplingError(f"codex app-server launch failed: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            if proc is not None and proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        with contextlib.suppress(Exception):
            if proc is not None:
                proc.kill()
        with contextlib.suppress(Exception):
            if proc is not None:
                proc.wait(timeout=1)

    response: dict[str, object] | None = None
    updated_notice: dict[str, object] | None = None
    for obj in messages:
        if str(obj.get("id")) == "3" and "result" in obj:
            response = obj
            break
        if obj.get("method") == "account/rateLimits/updated":
            updated_notice = obj
    if response is None and updated_notice is None:
        raise UsageSamplingError("codex app-server rate-limits read failed")
    result = response.get("result") if response is not None else updated_notice.get("params")
    if not isinstance(result, dict):
        raise UsageSamplingError("codex app-server returned an invalid rate-limits payload")
    rate_limits = result.get("rateLimits")
    if not isinstance(rate_limits, dict):
        raise UsageSamplingError("codex app-server response missing rateLimits")
    secondary = rate_limits.get("secondary")
    if not isinstance(secondary, dict):
        raise UsageSamplingError("codex app-server response missing secondary limits")
    used = secondary.get("usedPercent")
    try:
        remaining = max(0, min(100, int(math.floor(100.0 - float(used) + 1e-9))))
    except (TypeError, ValueError) as exc:
        raise UsageSamplingError(f"codex app-server returned invalid usedPercent: {used!r}") from exc
    return str(remaining)


def _sample_codex_current(
    *,
    loop: Literal["orchestrate", "research"],
    state: dict[str, object],
    cache_max_age_secs: int,
    command: str | None,
    auth_source_dir: Path | None,
    runtime_home: Path | None,
) -> WeeklyUsageSample:
    warnings: list[str] = []
    cached_current, cached_sampled_at, cached_source = _read_cached_entry(state, loop)
    cached_trusted = cached_current is not None and _trusted_cache_source(cached_source)
    cached_fresh, cached_age = _cached_is_fresh(cached_sampled_at, cache_max_age_secs)
    if cached_trusted and cached_fresh:
        warnings.append(f"reused cached sample age={cached_age}s max_age={cache_max_age_secs}s")
        return WeeklyUsageSample(
            ok=True,
            loop=loop,
            provider="codex",
            current=cached_current,
            source=cached_source or "state:cached",
            sampled_at=cached_sampled_at,
            warnings=warnings,
        )
    try:
        return WeeklyUsageSample(
            ok=True,
            loop=loop,
            provider="codex",
            current=_sample_from_codex_app_server(
                auth_source_dir=auth_source_dir,
                runtime_home=runtime_home,
            ),
            source="codex:app-server",
            warnings=warnings,
        )
    except UsageSamplingError as exc:
        warnings.append(f"codex app-server probe failed: {exc}")
    if (command or "").strip():
        try:
            value, source = _sample_from_command(command, source_label=f"{loop}_command")
            warnings.append("falling back to command sample after codex provider failure")
            return WeeklyUsageSample(
                ok=True,
                loop=loop,
                provider="codex",
                current=value,
                source=f"{source}:fallback",
                warnings=warnings,
            )
        except UsageSamplingError as exc:
            warnings.append(f"command fallback failed: {exc}")
    env_value, env_source, env_warnings = _sample_from_env(loop)
    warnings.extend(env_warnings)
    if env_value is not None and env_source is not None:
        warnings.append("falling back to env sample after codex provider failure")
        return WeeklyUsageSample(
            ok=True,
            loop=loop,
            provider="codex",
            current=env_value,
            source=f"{env_source}:fallback",
            warnings=warnings,
        )
    return WeeklyUsageSample(
        ok=False,
        loop=loop,
        provider="codex",
        source="codex:unavailable",
        warnings=warnings,
        reason="provider=codex failed and no fallback sample is available",
    )


def _write_usage_state(
    path: Path,
    *,
    loop: Literal["orchestrate", "research"],
    provider: Literal["codex", "env", "command"],
    state: dict[str, object],
    sample: WeeklyUsageSample,
) -> None:
    loops = state.get("loops")
    if not isinstance(loops, dict):
        loops = {}
        state["loops"] = loops
    entry: dict[str, object] = {
        "source": sample.source,
        "sampled_at": (
            sample.sampled_at or _utc_now()
        ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if sample.current is not None:
        entry["current"] = _normalize_decimal_text(str(sample.current))
    if sample.reason is not None:
        entry["reason"] = sample.reason
    if sample.warnings:
        entry["warnings"] = list(sample.warnings)
    loops[loop] = entry
    state["provider"] = provider
    state["updated_at"] = _utc_now().isoformat().replace("+00:00", "Z")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sample_weekly_usage(
    *,
    runtime_dir: Path,
    loop: Literal["orchestrate", "research"],
    provider: Literal["codex", "env", "command"],
    cache_max_age_secs: int = DEFAULT_USAGE_CACHE_MAX_AGE_SECS,
    command: str | None = None,
    auth_source_dir: Path | None = None,
    runtime_home: Path | None = None,
) -> WeeklyUsageSample:
    """Sample the current weekly usage value with provider-specific cache behavior."""

    state_file = _usage_state_file(runtime_dir)
    state, warnings = _load_usage_state(state_file)
    if provider == "codex":
        sample = _sample_codex_current(
            loop=loop,
            state=state,
            cache_max_age_secs=max(int(cache_max_age_secs), 0),
            command=command,
            auth_source_dir=auth_source_dir,
            runtime_home=runtime_home,
        )
    elif provider == "env":
        value, source, env_warnings = _sample_from_env(loop)
        warnings.extend(env_warnings)
        if value is not None and source is not None:
            sample = WeeklyUsageSample(
                ok=True,
                loop=loop,
                provider="env",
                current=value,
                source=source,
            )
        else:
            cached_current, cached_sampled_at, cached_source = _read_cached_entry(state, loop)
            cached_fresh, cached_age = _cached_is_fresh(cached_sampled_at, max(int(cache_max_age_secs), 0))
            if cached_current is not None and _trusted_cache_source(cached_source) and cached_fresh:
                warnings.append(
                    f"reused cached env/command sample age={cached_age}s max_age={cache_max_age_secs}s"
                )
                sample = WeeklyUsageSample(
                    ok=True,
                    loop=loop,
                    provider="env",
                    current=cached_current,
                    source=cached_source or "state:cached",
                    sampled_at=cached_sampled_at,
                )
            else:
                sample = WeeklyUsageSample(
                    ok=True,
                    loop=loop,
                    provider="env",
                    current="0",
                    source="default:zero",
                )
    elif provider == "command":
        try:
            value, source = _sample_from_command(command, source_label=f"{loop}_command")
            sample = WeeklyUsageSample(
                ok=True,
                loop=loop,
                provider="command",
                current=value,
                source=source,
            )
        except UsageSamplingError as exc:
            sample = WeeklyUsageSample(
                ok=False,
                loop=loop,
                provider="command",
                source="command:unavailable",
                reason=str(exc),
            )
    else:
        raise ValueError(f"unsupported usage provider: {provider}")
    if warnings:
        sample = sample.model_copy(update={"warnings": (*warnings, *sample.warnings)})
    _write_usage_state(state_file, loop=loop, provider=provider, state=state, sample=sample)
    return sample


def _failure_payload(source: Path, reason: str, *, helper_exit: int, **metadata: str) -> CodexUsageSummary:
    payload: dict[str, Any] = {
        "ok": False,
        "reason": reason,
        "source": source,
        "helper_exit": helper_exit,
    }
    for key, value in metadata.items():
        normalized = (value or "").strip()
        if normalized:
            payload[key] = normalized
    return CodexUsageSummary.model_validate(payload)


def _parse_nonnegative_int(raw: Any, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{field} must be a non-negative integer")
    if raw < 0:
        raise ValueError(f"{field} must be >= 0")
    return raw


def extract_codex_exec_usage(
    stdout_log: Path,
    *,
    loop: str = "",
    stage: str = "",
    model: str = "",
    runner: str = "codex",
) -> CodexUsageSummary:
    """Extract the last valid Codex usage payload without raising on normal failure modes."""

    source = Path(stdout_log)
    if not source.is_file():
        return _failure_payload(
            source,
            "stdout_log_missing",
            helper_exit=EXIT_IO,
            loop=loop,
            stage=stage,
            model=model,
            runner=runner,
        )

    found_turn_completed = False
    last_success: CodexUsageSummary | None = None

    try:
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "turn.completed":
                    continue

                found_turn_completed = True
                usage = event.get("usage")
                if not isinstance(usage, dict):
                    continue

                try:
                    last_success = CodexUsageSummary.model_validate(
                        {
                            "ok": True,
                            "source": source,
                            "input_tokens": _parse_nonnegative_int(
                                usage.get("input_tokens"),
                                "input_tokens",
                            ),
                            "cached_input_tokens": _parse_nonnegative_int(
                                usage.get("cached_input_tokens"),
                                "cached_input_tokens",
                            ),
                            "output_tokens": _parse_nonnegative_int(
                                usage.get("output_tokens"),
                                "output_tokens",
                            ),
                            "loop": loop or None,
                            "stage": stage or None,
                            "model": model or None,
                            "runner": runner or None,
                            "helper_exit": EXIT_OK,
                        }
                    )
                except ValueError as exc:
                    return _failure_payload(
                        source,
                        "invalid_usage_payload",
                        helper_exit=EXIT_MALFORMED,
                        loop=loop,
                        stage=stage,
                        model=model,
                        runner=runner,
                        detail=str(exc),
                    )
    except OSError:
        return _failure_payload(
            source,
            "stdout_log_unreadable",
            helper_exit=EXIT_IO,
            loop=loop,
            stage=stage,
            model=model,
            runner=runner,
        )

    if last_success is not None:
        return last_success

    return _failure_payload(
        source,
        "missing_usage" if found_turn_completed else "missing_turn_completed",
        helper_exit=EXIT_NO_USAGE,
        loop=loop,
        stage=stage,
        model=model,
        runner=runner,
    )


def format_usage_summary(payload: CodexUsageSummary) -> str:
    """Render the operator-facing token-usage summary line."""

    stage = payload.stage or ""
    runner = payload.runner or "codex"
    model = payload.model or ""
    source = payload.source.as_posix()

    if payload.ok:
        return (
            f"Token usage: stage={stage} runner={runner} model={model} "
            f"input={payload.input_tokens} cached={payload.cached_input_tokens} "
            f"output={payload.output_tokens} stdout={source}"
        )

    reason = payload.reason or "unknown"
    return (
        f"Token usage: stage={stage} runner={runner} model={model} "
        f"unavailable reason={reason} helper_exit={payload.helper_exit} stdout={source}"
    )
