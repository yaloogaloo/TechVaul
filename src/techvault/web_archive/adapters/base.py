"""Site adapter protocol and dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from techvault.web_archive.urls import ParsedWebUrl


@dataclass
class AdapterResult:
    ok: bool
    title: str = ""
    author: str | None = None
    published_at: str | None = None
    body_html: str = ""
    extractor: str = ""
    failure_code: str | None = None
    failure_message: str | None = None
    warnings: list[str] = field(default_factory=list)


class SiteAdapter(Protocol):
    site_key: str

    def supports(self, parsed: ParsedWebUrl) -> bool: ...

    def extract(self, parsed: ParsedWebUrl, html: str, final_url: str) -> AdapterResult: ...


def try_adapters(adapters: list[SiteAdapter], parsed: ParsedWebUrl, html: str, final_url: str) -> AdapterResult | None:
    for adapter in adapters:
        if adapter.supports(parsed):
            return adapter.extract(parsed, html, final_url)
    return None
