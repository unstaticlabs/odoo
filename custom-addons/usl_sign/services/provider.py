from abc import ABC, abstractmethod

from .yousign import YousignClient


class ProviderError(Exception):
    """Sanitized provider failure safe to surface in Odoo."""

    def __init__(self, message, *, retryable=False, uncertain=False, status_code=None):
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain
        self.status_code = status_code


class SignProvider(ABC):
    """Provider-neutral contract used by the Odoo orchestration layer."""

    @abstractmethod
    def create_request(self, payload):
        raise NotImplementedError

    @abstractmethod
    def recover_request(self, external_id):
        raise NotImplementedError

    @abstractmethod
    def upload_document(self, request_id, filename, content, initials=None):
        raise NotImplementedError

    @abstractmethod
    def add_signer(self, request_id, payload):
        raise NotImplementedError

    @abstractmethod
    def add_field(self, request_id, document_id, payload):
        raise NotImplementedError

    @abstractmethod
    def list_fields(self, request_id, document_id):
        raise NotImplementedError

    @abstractmethod
    def activate(self, request_id):
        raise NotImplementedError

    @abstractmethod
    def get_request(self, request_id):
        raise NotImplementedError

    @abstractmethod
    def cancel(self, request_id):
        raise NotImplementedError

    @abstractmethod
    def download_document(self, request_id, document_id):
        raise NotImplementedError

    @abstractmethod
    def download_audit_trail(self, request_id, signer_id):
        raise NotImplementedError


def get_provider(provider_code, configuration, *, session=None):
    if provider_code == "yousign":
        return YousignClient(configuration, session=session, error_class=ProviderError)
    raise ProviderError(f"Unsupported signature provider: {provider_code}")
