"""
Every exception codaio raises.

`CodaError` is the root, so `except codaio.err.CodaError` still catches
everything. Errors that came from an HTTP response carry the status code and
enough request context to say *which* call failed -- previously the status code
was formatted into the message string and could not be inspected.
"""


class CodaError(Exception):
    """
    Base for everything this library raises.

    Accepts a bare message, so ``CodaError("something went wrong")`` keeps
    working, and optionally the structured context the client has available.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int = None,
        status_message: str = None,
        method: str = None,
        url: str = None,
        request_id: str = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.status_message = status_message
        self.method = method
        self.url = url
        self.request_id = request_id


# --------------------------------------------------------------------------
# Errors that correspond to an HTTP response
# --------------------------------------------------------------------------


class HTTPError(CodaError):
    """The API answered with a non-2xx status."""


class BadRequest(HTTPError):
    """400."""


class InvalidQuery(BadRequest):
    """
    A query the API rejects, or that codaio knows it will reject.

    Also raised client-side before sending, where a combination of parameters
    is documented as invalid and spending a request to be told so is waste.
    """


class Unauthorized(HTTPError):
    """401. The token is missing, malformed, or revoked."""


class Forbidden(HTTPError):
    """403. The token is valid but not permitted to do this."""


class NotFound(HTTPError):
    """404."""


class DocumentNotFound(NotFound):
    pass


class TableNotFound(NotFound):
    pass


class RowNotFound(NotFound):
    pass


class ColumnNotFound(NotFound):
    pass


class PageNotFound(NotFound):
    pass


class FolderNotFound(NotFound):
    pass


class Gone(HTTPError):
    """410. The object existed and has been deleted."""


class PageDeleted(Gone):
    """
    410 from a page endpoint.

    Distinct from `PageNotFound`: the API is telling you this page *was* real,
    which is a different situation from a bad id.
    """


class UnprocessableEntity(HTTPError):
    """422."""


class RateLimited(HTTPError):
    """
    429.

    `retry_after` is the server's requested delay in seconds when it sent one.
    A 429 means the request was rejected rather than processed, so replaying it
    is safe regardless of what it would have done.
    """

    def __init__(self, message: str = "", *, retry_after: float = None, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(HTTPError):
    """5xx."""


# --------------------------------------------------------------------------
# Errors about the request itself rather than the response
# --------------------------------------------------------------------------


class RetryExhausted(CodaError):
    """Every attempt failed. `last_error` is the one that ended it."""

    def __init__(self, message: str = "", *, attempts: int = None, last_error=None, **kwargs):
        super().__init__(message, **kwargs)
        self.attempts = attempts
        self.last_error = last_error


class AmbiguousWrite(CodaError):
    """
    A write timed out after being sent, and codaio cannot tell whether it landed.

    Deliberately not retried: replaying it could apply the edit twice. There is
    no way to check afterwards either, because a `requestId` is only returned in
    a response that never arrived. Re-read the affected objects before acting.
    """


class UntrustedHost(CodaError):
    """
    codaio refused to send a request somewhere it does not trust.

    Either a link from a response body pointed off the API host while carrying
    the token, or a content URL used a scheme other than https.
    """


# --------------------------------------------------------------------------
# Asynchronous operations
# --------------------------------------------------------------------------


class MutationTimeout(CodaError):
    """
    A write was accepted but did not report completion in time.

    The edit is most likely still queued rather than lost. `request_id` lets you
    resume polling later -- though the API does not guarantee mutation status
    stays available for more than about a day.
    """


class ExportFailed(CodaError):
    """A page export finished unsuccessfully. `message` is the API's reason."""


class ExportTimeout(CodaError):
    """A page export did not produce a download link in time."""


class AttachmentUnavailable(CodaError):
    """An attachment cannot be fetched -- most often because it was deleted."""


# --------------------------------------------------------------------------
# Credentials, and problems with what the caller asked for
# --------------------------------------------------------------------------


class NoApiKey(CodaError):
    pass


class InvalidFilter(CodaError):
    pass


class AmbiguousName(CodaError):
    pass


class InvalidCell(CodaError):
    pass


class UnknownFieldWarning(UserWarning):
    """
    The API returned a field codaio does not model.

    A warning rather than an error: unknown fields are kept and reachable, so
    this is a prompt to model them, never a failure. Off by default.
    """
