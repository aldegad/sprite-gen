# SPDX-License-Identifier: Apache-2.0
"""When a provider request that got an answer is sent again, and how long it waits first — one rule
for every direct provider call: the Anthropic transport of `pixel-draw`, the OpenAI and Grok image
calls of `gen`, the facing check, and the clip requests and polls of `video`.

A POST that was answered is sent again only when its provider documents that answer as a refusal
of the request before it was taken (`UNTAKEN`): the request made nothing, so asking again buys
nothing twice. Every other answer — a 4xx, a 5xx the provider does not document so (it may have
been worked on and billed), an answer that asks the caller to act first (a spent credit balance or
spend limit, `ACT_FIRST`) — is returned to the caller as it came. A request that got no answer it
could read (the connection broke, the answer broke on the way) is never sent again here: the
server may have taken it.

What the providers say, read 2026-10-13:

- Anthropic (platform.claude.com/docs/en/api/errors, /api/rate-limits): 429 `rate_limit_error`
  "Your organization has hit a rate limit", with a `retry-after` header "indicating how long to
  wait"; 529 `overloaded_error` "The API is temporarily overloaded"; 500 `api_error` "An
  unexpected error has occurred internal to Anthropic's systems. Retry the request with
  exponential backoff". Not sent again: 504 `timeout_error` "The request timed out while
  processing" (it was worked on), and the spend-cap 429 (`error.details.error_code`
  `enforced_spend_limit_reached`, no `retry-after`): "Retrying ... fails until access resumes".
- OpenAI (developers.openai.com/api/docs/guides/error-codes, /guides/rate-limits): 429 "Rate
  limit reached for requests" and `slow_down`, and 503 `server_is_overloaded` "the requested model
  does not have enough capacity to process your request at the moment" — the two its SDKs retry
  ("Each official OpenAI SDK automatically retries eligible 429 and 503 responses"). Not sent
  again: 500 "The server had an error while processing your request", and the billing 429s
  (`credit_balance_exhausted`, `organization_spend_limit_exceeded`,
  `project_spend_limit_exceeded`, `organization_usage_limit_exceeded`, type `insufficient_quota`):
  "Don't retry quota, billing, or other errors that require you to take action."
- xAI (docs.x.ai/developers/debugging, /developers/rate-limits): 429 "You are sending requests
  too frequently and have reached the rate limit" — "Implement exponential backoff". It
  documents no 5xx.

The wait: the `retry-after` the answer carries, when it is one this run will wait for
(`RETRY_AFTER_MAX_SECONDS`; a longer one is not waited out early — the request is not sent
again, as OpenAI's rate-limit guide asks), else `backoff` — the doubling the `video` polls and
downloads wait by, from START_SECONDS up to MAX_SECONDS. A POST is sent again ASKS_AGAIN times at
most, each said on stderr in one line.

The waits are inside the time limit of the call the request belongs to (`ask(deadline=)`): the gen
image calls and the facing check count theirs from the first send, the clip request its poll
timeout, a Message Batch's reads `--batch-wait`. A request is sent again only when its wait ends
before that limit; one whose wait would reach it is returned as it came, and its stderr line says
why — so a caller whose own ceiling sits above the limit hears the refusal, not a kill. One
judgment reads the limit, `too_late`, before the wait and again after it, right before the send: a
sleep may wake later than it was asked to, and one that woke at the limit or past it sends nothing
either — the refusal is returned as it came, its line saying the time limit came during the wait.
A send at the limit is not made: it would have no time left to be answered in.

A GET buys nothing, so one answered with any of GET_AGAIN is asked again; the `video` poll keeps
asking for as long as its poll timeout lets it, the Anthropic batch reads ASKS_AGAIN times.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable

from . import address

START_SECONDS = 4.0
MAX_SECONDS = 60.0
RETRY_AFTER_MAX_SECONDS = 120.0
ASKS_AGAIN = 2

UNTAKEN: dict[str, frozenset[int]] = {
    address.ANTHROPIC: frozenset({429, 500, 529}),
    address.OPENAI: frozenset({429, 503}),
    address.XAI: frozenset({429}),
}
ACT_FIRST: dict[str, frozenset[str]] = {
    address.ANTHROPIC: frozenset({"enforced_spend_limit_reached"}),
    address.OPENAI: frozenset({"credit_balance_exhausted", "organization_spend_limit_exceeded",
                         "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "insufficient_quota"}),
    address.XAI: frozenset(),
}
GET_AGAIN = frozenset({429, 500, 502, 503, 504, 529})

Sleep = Callable[[float], None]
Send = Callable[[], tuple[int, Any, Any]]


def backoff(failures: int) -> float:
    """The wait after `failures` failed asks in a row: 8 s, 16 s, 32 s, then MAX_SECONDS."""
    return min(START_SECONDS * 2 ** failures, MAX_SECONDS)


def retry_after(headers: Any) -> float | None:
    """The answer's `retry-after` in seconds, or None when it sent none this can read."""
    value = headers.get("retry-after") if headers is not None else None
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None


def _act_first(provider: str, body: Any) -> bool:
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return False
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    said = (error.get("code"), error.get("type"), details.get("error_code"))
    return any(isinstance(word, str) and word in ACT_FIRST[provider] for word in said)


def again_after(provider: str, method: str, status: int, body: Any, headers: Any, failures: int) -> float | None:
    """Seconds to wait before sending the request again after its `failures`-th failed answer in a row,
    or None when it is not sent again (the rule in the module docstring)."""
    if failures > ASKS_AGAIN:
        return None
    if method == "GET":
        if status not in GET_AGAIN:
            return None
    elif status not in UNTAKEN[provider] or _act_first(provider, body):
        return None
    said = retry_after(headers)
    if said is None:
        return backoff(failures)
    return said if said <= RETRY_AFTER_MAX_SECONDS else None


def _refused(label: str, who: str, method: str, status: int, kind: str | None) -> str:
    # The status goes in plain digits: `(HTTP 429)` on this stream reads as a refusal line, which a caller
    # may answer by sending a second request.
    why = ("a read buys nothing" if method == "GET"
           else "its docs say to ask again" if status == 500 else "nothing was made")
    return f"[{label}] {who} refused the request ({status}{' ' + kind if kind else ''}); {why}"


def asking_again(label: str, who: str, method: str, status: int, kind: str | None, wait: float, failures: int) -> str:
    """The stderr line of one ask again."""
    return f"{_refused(label, who, method, status, kind)} — asking again in {wait:g}s ({failures} of {ASKS_AGAIN})"


def out_of_time(label: str, who: str, method: str, status: int, kind: str | None, wait: float, left: float) -> str:
    """The stderr line of a request not sent again because its wait would reach the time limit of its call."""
    return (f"{_refused(label, who, method, status, kind)} — not asking again: "
            f"the {wait:g}s wait would reach the time limit ({max(left, 0.0):.0f}s left)")


def woke_late(label: str, who: str, method: str, status: int, kind: str | None, wait: float) -> str:
    """The stderr line of a request not sent again because the time limit of its call came during its wait:
    the sleep woke at the limit or past it."""
    return f"{_refused(label, who, method, status, kind)} — not asking again: the time limit came during the {wait:g}s wait"


def tail(asks: int) -> str:
    """What a `gen` refusal line says was done after the answer: no retry, or how many times it was sent."""
    return "no retry or provider fallback" if asks == 1 else f"sent {asks} times, no provider fallback"


def too_late(deadline: float | None, wait: float = 0.0) -> bool:
    """Whether a request sent `wait` seconds from now would go at `deadline` or past it — the time limit of the
    call it belongs to, a `time.monotonic()` instant (None: none) — and so is not sent: one sent at the limit
    would have no time left to be answered in. The one reading of a call's time limit before a send: `ask`
    reads it before each wait and again after it, right before the send (a sleep may wake late), and the
    `video` polls and downloads read their poll timeout by it the same way."""
    return deadline is not None and time.monotonic() + wait >= deadline


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def ask(send: Send, *, provider: str, who: str, label: str, method: str = "POST", sleep: Sleep | None = None,
        kind: Callable[[Any], str | None] | None = None, deadline: float | None = None) -> tuple[int, Any, Any, int]:
    """`send()` until an answer is not one to send again: `(status, body, headers, asks)`.

    `send` returns `(status, body, headers)` — `headers` None when the transport keeps none — and
    raises when no answer came; that is never asked again here. `kind` reads the provider's word for
    the refusal off the body for the stderr line. `deadline`, a `time.monotonic()` instant (None:
    none), is the time limit of the call, read by `too_late` before each wait and again after it, right
    before the send: a wait that would reach it is not waited, and a wait that woke at it or past it
    sends nothing — either way the refusal is returned as it came, after one line saying why."""
    asks = 0
    while True:
        status, body, headers = send()
        asks += 1
        wait = None if 200 <= status < 300 else again_after(provider, method, status, body, headers, asks)
        if wait is None:
            return status, body, headers, asks
        said = kind(body) if kind else None
        if too_late(deadline, wait):
            print(out_of_time(label, who, method, status, said, wait, deadline - time.monotonic()), file=sys.stderr)
            return status, body, headers, asks
        print(asking_again(label, who, method, status, said, wait, asks), file=sys.stderr)
        (sleep or _sleep)(wait)
        if too_late(deadline):
            print(woke_late(label, who, method, status, said, wait), file=sys.stderr)
            return status, body, headers, asks
