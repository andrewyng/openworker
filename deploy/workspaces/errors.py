class Failure(Exception):
    def __init__(self, code="workspace_provider_unavailable", status=503):
        self.code, self.status = code, status
