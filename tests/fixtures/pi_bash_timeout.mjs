import { appendFileSync } from "node:fs";
import { performance } from "node:perf_hooks";

export const MAX_BASH_TIMEOUT_SECONDS = 1800;

function timeoutDecision(requested) {
  if (typeof requested !== "number" || !Number.isFinite(requested) || requested <= 0) {
    return {
      seconds: MAX_BASH_TIMEOUT_SECONDS,
      normalization: requested === undefined ? "defaulted" : "invalid_defaulted",
    };
  }
  if (requested > MAX_BASH_TIMEOUT_SECONDS) {
    return { seconds: MAX_BASH_TIMEOUT_SECONDS, normalization: "capped" };
  }
  return { seconds: requested, normalization: "preserved" };
}

export function effectiveBashTimeout(requested) {
  return timeoutDecision(requested).seconds;
}

function appendAudit(record) {
  const path = process.env.D_TIMEOUT_AUDIT_PATH;
  if (!path) throw new Error("D timeout audit path is required");
  appendFileSync(path, `${JSON.stringify({ at: new Date().toISOString(), ...record })}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
}

function timeoutErrorReported(content, seconds) {
  if (!Array.isArray(content)) return false;
  const expected = `Command timed out after ${seconds} seconds`;
  return content.some((item) => item?.type === "text" && typeof item.text === "string" && item.text.includes(expected));
}

export default function timeoutExtension(pi) {
  const pending = new Map();

  pi.on("tool_call", async (event) => {
    if (event.toolName !== "bash" || !event.input || typeof event.input !== "object") return;

    const requested = event.input.timeout;
    const decision = timeoutDecision(requested);
    event.input.timeout = decision.seconds;
    const toolCallId = event.toolCallId;
    if (typeof toolCallId === "string") {
      pending.set(toolCallId, { seconds: decision.seconds, startedAt: performance.now() });
    }
    appendAudit({
      event: "bash_timeout_enforced",
      tool_call_id: typeof toolCallId === "string" ? toolCallId : null,
      requested_timeout_seconds: typeof requested === "number" && Number.isFinite(requested) ? requested : null,
      effective_timeout_seconds: decision.seconds,
      normalization: decision.normalization,
    });
  });

  pi.on("tool_result", async (event) => {
    if (event.toolName !== "bash" || typeof event.toolCallId !== "string") return;
    const request = pending.get(event.toolCallId);
    if (!request) return;
    const timedOut = event.isError === true && timeoutErrorReported(event.content, request.seconds);
    pending.delete(event.toolCallId);
    appendAudit({
      event: "bash_timeout_result",
      tool_call_id: event.toolCallId,
      is_error: event.isError === true,
      timed_out: timedOut,
      elapsed_ms: Math.max(0, Math.round(performance.now() - request.startedAt)),
    });
  });
}
