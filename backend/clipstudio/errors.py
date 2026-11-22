"""Domain errors with user-facing messages."""


class ClipStudioError(Exception):
    """Base error. ``str(err)`` is safe to show to the user."""

    status_code = 400


class Cancelled(ClipStudioError):
    def __init__(self, msg: str = "Cancelled by user"):
        super().__init__(msg)


class InsufficientStorage(ClipStudioError):
    status_code = 507


class InvalidInput(ClipStudioError):
    status_code = 422


class MediaError(ClipStudioError):
    status_code = 422


class ModelUnavailable(ClipStudioError):
    status_code = 503


class NotFound(ClipStudioError):
    status_code = 404
