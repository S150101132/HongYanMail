"""Fixed local error codes never contain remote data or credentials."""
class AppError(Exception):
    def __init__(self, code: str, operation_id: str = '', category: str = ''):
        self.code = code if isinstance(code, str) and code.replace('_', '').isalnum() and len(code) <= 64 else 'INTERNAL_ERROR'
        self.operation_id = operation_id
        self.category = category
        super().__init__(self.code)
