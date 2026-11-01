class APIError(Exception):
    def __init__(self, message: str, *, reason: str = "validation_failed"):
        super().__init__(message)
        self.reason = reason

class AuthenticationError(APIError):
    def __init__(self, message: str, *, reason: str = "authentication_failed", status: int = 400):
        super().__init__(message, reason=reason)
        self.status = status
