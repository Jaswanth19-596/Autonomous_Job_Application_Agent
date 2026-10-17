"""Timed terminal input and application intervention fallback policy."""

import asyncio
import math
import select
import sys
import time

HUMAN_INPUT_TIMEOUT_SECONDS = 60
TIMEOUT_RESPONSE = {"human_input_timed_out": True}


async def timed_human_input(prompt, timeout_seconds=HUMAN_INPUT_TIMEOUT_SECONDS,
                            *, stream=None, output=None, clock=time.monotonic):
    """Read without a background input thread that could consume the next reply."""
    stream = sys.stdin if stream is None else stream
    output = sys.stdout if output is None else output
    print(prompt, file=output, flush=True)
    deadline = clock() + timeout_seconds
    seconds = math.ceil(timeout_seconds)
    # Terminal echo owns the response line. Redrawing a countdown with \r
    # moves the cursor over the user's unfinished input and corrupts its display.
    print(f"Time to respond: {seconds // 60}:{seconds % 60:02d}",
          file=output, flush=True)
    print("> ", end="", file=output, flush=True)
    try:
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                print("\nTime expired — continuing automatically.", file=output, flush=True)
                return dict(TIMEOUT_RESPONSE)
            if select.select([stream], [], [], 0)[0]:
                reply = stream.readline()
                if clock() >= deadline or not reply:
                    return dict(TIMEOUT_RESPONSE)
                if reply.strip():
                    return reply.rstrip("\r\n")
            await asyncio.sleep(min(0.1, max(0, deadline - clock())))
    finally:
        # Discard an unfinished terminal line so it cannot answer a later job.
        if stream.isatty():
            import termios
            termios.tcflush(stream, termios.TCIFLUSH)


def timeout_update(state):
    """Try a question once per job; repeated/unresolvable requests skip the job."""
    origin = state.get("human_intervention_origin", "")
    question = state.get("human_intervention_prompt", "")
    key = f"{state.get('job_url', '')}|{origin}|{question.strip().casefold()}"
    attempted = list(state.get("timed_out_questions") or [])
    result = {
        "requires_human": False,
        "human_intervention_reason": "",
        "human_intervention_prompt": "",
    }
    if (state.get("human_intervention_reason") == "QUESTION"
            and origin in {"submit_job_application", "perform_login", "resolve_application_url"}
            and key not in attempted):
        result.update({
            "human_intervention_origin": origin,
            "timed_out_questions": [*attempted, key],
            f"{origin}_messages": [{"role": "user", "content": (
                f"No human response arrived within 60 seconds for: {question}\n"
                "Make your best reasonable inference using the profile, resume, saved answers, "
                "and job context, then continue the application. This is an automatic fallback, "
                "not a user-confirmed answer. Do not invent credentials or personal facts. "
                "If a required answer cannot be reasonably inferred, report the application "
                "as NOT_APPLIED with requires_human=false and explain the blocker so the "
                "workflow moves to the next job. Do not ask this question again."
            )}],
        })
    else:
        applied = state.get("is_applied") or state.get("job_application_phase") == "APPLIED"
        result.update({
            "human_intervention_origin": "update_job_status",
            "application_status": "Applied" if applied else "Not Applied",
            "is_applied": bool(applied),
            "reason_for_not_applying": f"Human input timed out after 60 seconds; moving to next job. {question}",
        })
    return result
