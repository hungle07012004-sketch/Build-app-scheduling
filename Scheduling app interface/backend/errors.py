class AppError(Exception):
    def __init__(self, code, message, details=None, status=400):
        self.code, self.message = code, message
        self.details, self.status = details, status
