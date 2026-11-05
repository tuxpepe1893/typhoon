"""Errors which can be presented directly by both clients."""


class TyphoonError(Exception):
    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details
