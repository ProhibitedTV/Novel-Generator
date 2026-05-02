from __future__ import annotations


class ProviderError(RuntimeError):
    pass


class ProviderTransportError(ProviderError):
    pass
