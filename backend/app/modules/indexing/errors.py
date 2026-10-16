class IndexFailure(Exception):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(code)
        self.code, self.message, self.retryable = code, message, retryable


class LeaseLost(Exception):
    pass
