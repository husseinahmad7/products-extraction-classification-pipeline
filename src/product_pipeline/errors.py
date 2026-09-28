class PipelineError(Exception):
    """Stable error boundary; details must never contain source credentials."""

    def __init__(self, code: str, detail: str, retryable: bool = False):
        super().__init__(detail)
        self.code, self.detail, self.retryable = code, detail, retryable

    def problem(self, status: int = 422) -> dict:
        return {
            "type": f"urn:product-pipeline:error:{self.code}",
            "title": self.code,
            "status": status,
            "detail": self.detail,
            "retryable": self.retryable,
        }
