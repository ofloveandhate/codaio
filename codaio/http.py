"""
HTTP plumbing shared by every request the client makes.

Kept separate from the client so the rules that protect the API token are in one
small, auditable place rather than spread through the call sites.

Two of those rules are worth stating up front, because both exist to stop the
bearer token reaching somewhere it should not:

* :func:`assert_same_origin` guards links taken from a *response body*. `requests`
  strips the Authorization header when a redirect crosses hosts, but a paginated
  response hands us a URL that we then fetch directly, which sidesteps that
  protection entirely.
* :func:`fetch_untrusted` is the inverse rule for content links -- attachments and
  export downloads live on a CDN, not the API. It attaches no credentials under
  any circumstance, and takes no credential argument, so there is no code path by
  which one could be supplied.
"""

from __future__ import annotations

import email.utils
import enum
import random
import time
from typing import Callable, Dict, FrozenSet, Tuple
from urllib.parse import urlsplit

import attr
import requests

from codaio import err

_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Schemes a content link may use. Anything else could downgrade the transport.
_SAFE_SCHEMES = frozenset({"https"})

#: Published rate limits, as documented by the API, for callers that want to pace
#: themselves. codaio does not enforce these: limits apply per user across every
#: session sharing a token, so a limiter inside one process would be pacing
#: against incomplete information and giving false assurance. Reacting to the
#: server's own 429 is the honest mechanism.
RATE_LIMITS = {
    "read": (100, 6.0),
    "write": (10, 6.0),
    "write_doc_content": (5, 10.0),
    "list_docs": (4, 6.0),
}


def _origin(url: str) -> Tuple[str, str, int]:
    """(scheme, host, port) for `url`, with the scheme's default port filled in."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    return (scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme))


def assert_same_origin(url: str, expected: str) -> None:
    """
    Refuse to send the API token somewhere the API didn't come from.

    `requests` strips the Authorization header when a *redirect* crosses
    hosts, but a paginated response hands us a `nextPageLink` from the
    response body and we fetch it directly, which sidesteps that protection.
    A hostile or buggy link would otherwise receive the bearer token.

    >>> assert_same_origin(
    ...     "https://coda.io/apis/v1/docs?pageToken=x", "https://coda.io/apis/v1"
    ... )
    >>> assert_same_origin("https://evil.example/steal", "https://coda.io/apis/v1")
    Traceback (most recent call last):
        ...
    codaio.err.UntrustedHost: refusing to send the API token to 'evil.example', ...
    """
    if _origin(url) != _origin(expected):
        raise err.UntrustedHost(
            f"refusing to send the API token to {_origin(url)[1] or url!r}, "
            f"which is not the API host ({_origin(expected)[1]}). This link "
            f"came from the API response body, not from a redirect."
        )


# --------------------------------------------------------------------------
# Turning a response into an exception
# --------------------------------------------------------------------------

_STATUS_ERRORS = {
    400: err.BadRequest,
    401: err.Unauthorized,
    403: err.Forbidden,
    404: err.NotFound,
    410: err.Gone,
    422: err.UnprocessableEntity,
    429: err.RateLimited,
}


def error_for_status(response, *, method: str = None, url: str = None) -> err.HTTPError:
    """
    Build the exception for a non-2xx response, without raising it.

    Deliberately tolerant of the body. The previous implementation did
    ``response.json()["message"]`` unconditionally, so an HTML error page from an
    intermediary raised ``JSONDecodeError`` and a JSON body without that key
    raised ``KeyError`` -- in both cases losing the status code, which is the one
    piece of information the caller most needs.
    """
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    status = response.status_code
    message = body.get("message") or (response.text or "").strip()[:500] or response.reason or ""
    status_message = body.get("statusMessage")

    if status in _STATUS_ERRORS:
        cls = _STATUS_ERRORS[status]
    elif 500 <= status <= 599:
        cls = err.ServerError
    else:
        cls = err.HTTPError

    kwargs = dict(
        status_code=status,
        status_message=status_message,
        method=method,
        url=url,
        request_id=body.get("requestId"),
    )
    if cls is err.RateLimited:
        kwargs["retry_after"] = retry_after_seconds(response)

    return cls(f"Status code: {status}. Message: {message}", **kwargs)


def raise_for_status(response, *, method: str = None, url: str = None) -> None:
    """Raise the mapped exception if `response` is not a success."""
    if 200 <= response.status_code <= 299:
        return
    raise error_for_status(response, method=method, url=url)


def retry_after_seconds(response) -> float:
    """
    `Retry-After` in seconds, or None.

    The header is defined as either a number of seconds or an HTTP-date; both
    appear in practice, so both are handled.

    >>> import requests
    >>> response = requests.Response()
    >>> response.headers["Retry-After"] = "30"
    >>> retry_after_seconds(response)
    30.0
    >>> retry_after_seconds(requests.Response()) is None
    True
    """
    raw = response.headers.get("Retry-After") if response is not None else None
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (when - now).total_seconds())


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------


class Idempotency(enum.Enum):
    """
    Whether a request may be sent again after an inconclusive outcome.

    This is a property of the request, not a preference: it depends on the verb
    *and on the arguments*. Appending page content is not replayable while
    replacing it is; updating a row by id is replayable while updating one by
    name is not, because the API picks an arbitrary match. Callers cannot
    reasonably be expected to work that out, so codaio decides it and never
    exposes it as a setting.
    """

    #: Reads. No state changes, always replayable.
    SAFE = "safe"
    #: Writes that converge: replaying reaches the same end state.
    IDEMPOTENT = "idempotent"
    #: Writes that may duplicate or misdirect an effect if replayed.
    UNSAFE = "unsafe"


@attr.s(auto_attribs=True, frozen=True)
class RetryPolicy:
    """
    How persistently to retry. Purely about persistence -- *whether* a request may
    be replayed at all is decided by :class:`Idempotency` and is not adjustable.

    `sleep` and `clock` are injectable so tests never actually wait.
    """

    attempts: int = 5
    backoff: float = 0.5
    multiplier: float = 2.0
    max_backoff: float = 30.0
    jitter: bool = True
    respect_retry_after: bool = True
    #: Refuse to sleep longer than this on the server's say-so; raise instead.
    max_retry_after: float = 120.0
    retry_on_status: FrozenSet[int] = frozenset({429, 500, 502, 503, 504})
    timeout: float = 30.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def delay_for(self, attempt: int) -> float:
        """
        Backoff before retry number `attempt` (1-based).

        >>> policy = RetryPolicy(jitter=False)
        >>> [policy.delay_for(n) for n in (1, 2, 3)]
        [0.5, 1.0, 2.0]

        Jitter is on by default, so real delays vary within that shape. Tests
        pass `sleep` and `clock` instead of waiting:

        .. code-block:: python

            coda = Coda(retry=RetryPolicy(attempts=8, max_backoff=60))
            patient = Coda(retry=RetryPolicy(attempts=20))
            never = Coda(retry=None)
        """
        raw = min(self.backoff * (self.multiplier ** (attempt - 1)), self.max_backoff)
        if self.jitter:
            raw *= 0.5 + random.random() * 0.5
        return raw


DEFAULT_RETRY_POLICY = RetryPolicy()


def _retry_allowed(status: int, idempotency: Idempotency) -> bool:
    """
    Whether a failed *response* may be replayed.

    A 429 is special: it means the request was rejected rather than processed, so
    replaying it is safe no matter what it would have done. Every other retryable
    status is a genuine server-side failure of unknown effect, so only reads and
    converging writes may be replayed.

    >>> _retry_allowed(429, Idempotency.UNSAFE)
    True
    >>> _retry_allowed(500, Idempotency.UNSAFE)
    False
    >>> _retry_allowed(500, Idempotency.SAFE)
    True
    """
    if status == 429:
        return True
    return idempotency in (Idempotency.SAFE, Idempotency.IDEMPOTENT)


def run_with_retry(
    send: Callable[[], "requests.Response"],
    *,
    idempotency: Idempotency,
    policy: RetryPolicy = None,
    method: str = None,
    url: str = None,
):
    """
    Perform one request via `send`, retrying per `policy`, and return the response.

    `send` must perform exactly one round trip. A successful response is returned
    unexamined; turning a non-2xx into an exception is :func:`raise_for_status`'s
    job, and happens only once retries are done with.

    `policy=None` means one attempt, with no retry and no swallowing.
    """
    if policy is None:
        return send()

    last_error = None
    retried = False

    for attempt in range(1, policy.attempts + 1):
        delay = None
        try:
            response = send()
        except requests.exceptions.RequestException as exc:
            last_error = _transport_error(exc, idempotency, method=method, url=url)
            if not isinstance(last_error, _Retryable):
                raise last_error from exc
            last_error = last_error.error
        else:
            if response.status_code not in policy.retry_on_status:
                return response
            if not _retry_allowed(response.status_code, idempotency):
                return response
            last_error = error_for_status(response, method=method, url=url)
            if policy.respect_retry_after and isinstance(last_error, err.RateLimited):
                delay = last_error.retry_after
                if delay is not None and delay > policy.max_retry_after:
                    raise last_error

        if attempt == policy.attempts:
            break
        if delay is None:
            delay = policy.delay_for(attempt)
        retried = True
        policy.sleep(delay)

    if not retried:
        raise last_error
    raise err.RetryExhausted(
        f"gave up after {policy.attempts} attempts: {last_error}",
        attempts=policy.attempts,
        last_error=last_error,
        method=method,
        url=url,
    ) from last_error


@attr.s(auto_attribs=True)
class _Retryable:
    """Marker: this transport failure may be tried again."""

    error: Exception


def _transport_error(exc, idempotency: Idempotency, *, method: str, url: str):
    """
    Classify a transport-level failure.

    The distinction that matters is whether the request could already have taken
    effect. A connect timeout never reached the server, so anything may be
    replayed. A read timeout means the request *was* sent and the reply was lost,
    so an unsafe write is genuinely ambiguous and must not be repeated.

    Other connection errors are treated as ambiguous for unsafe writes too. A DNS
    failure certainly sent nothing, but a reset mid-request may not have, and
    `requests` does not reliably distinguish them. Erring toward "check your
    data" is better than erring toward a duplicated row.
    """
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return _Retryable(exc)
    if idempotency in (Idempotency.SAFE, Idempotency.IDEMPOTENT):
        return _Retryable(exc)
    return err.AmbiguousWrite(
        f"{method or 'request'} to {url or 'the API'} failed after the request was "
        f"sent ({type(exc).__name__}: {exc}). The edit may or may not have been "
        f"applied. It cannot be checked afterwards, because a requestId is only "
        f"returned in a response that never arrived -- re-read the affected "
        f"objects before retrying.",
        method=method,
        url=url,
    )


# --------------------------------------------------------------------------
# Fetching content links
# --------------------------------------------------------------------------


def fetch_untrusted(url: str, *, timeout: float = 30.0, stream: bool = False):
    """
    GET a URL that came out of an API response body, with **no credentials**.

    Attachment URLs and export download links point at a content host rather than
    the API. The bearer token must never reach them, so this function builds the
    request itself and attaches no Authorization header under any circumstance --
    it takes no credential argument, so there is no code path by which one could
    be supplied. That is a stronger guarantee than comparing origins, and it is
    checkable by reading these few lines.

    Only https is accepted; a plaintext content link is refused rather than
    silently downgraded.
    """
    scheme = _origin(url)[0]
    if scheme not in _SAFE_SCHEMES:
        raise err.UntrustedHost(
            f"refusing to fetch {scheme or 'scheme-less'} URL {url!r}: content "
            f"links must use https."
        )
    response = requests.get(url, timeout=timeout, stream=stream, allow_redirects=True)
    raise_for_status(response, method="GET", url=url)
    return response


def headers_without_authorization(headers: Dict) -> Dict:
    """Strip any credential from a header mapping. Used when logging a request."""
    return {k: v for k, v in (headers or {}).items() if k.lower() != "authorization"}
