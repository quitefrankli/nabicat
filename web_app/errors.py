class APIError(Exception):
    def __init__(self, message: str, *, reason: str = "validation_failed"):
        super().__init__(message)
        self.reason = reason

class AuthenticationError(APIError):
    pass