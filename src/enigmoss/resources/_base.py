"""Base class of the resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from enigmoss._http import Endpoint, Requester
from enigmoss._options import RequestOptions
from enigmoss._page import Page, page_from
from enigmoss._parse import Reader, items
from enigmoss._retry import MAX_RETRY_AFTER

T = TypeVar("T")


class Resource:
    __slots__ = ("_requester",)

    def __init__(self, requester: Requester) -> None:
        self._requester = requester

    def _page(
        self,
        endpoint: Endpoint,
        query: Mapping[str, Any],
        options: RequestOptions | None,
        parse: Callable[[Reader], T],
        max_retry_after: float = MAX_RETRY_AFTER,
    ) -> Page[T]:
        result = self._requester.execute(
            endpoint, query=query, options=options, max_retry_after=max_retry_after
        )
        return page_from(result, parse)

    def _one(self, endpoint: Endpoint, parse: Callable[[Reader], T], **kwargs: Any) -> T:
        result = self._requester.execute(endpoint, **kwargs)
        return result.parse(lambda data: parse(Reader(data)))

    def _all(self, endpoint: Endpoint, parse: Callable[[Reader], T], **kwargs: Any) -> list[T]:
        result = self._requester.execute(endpoint, **kwargs)
        return result.parse(lambda data: [parse(r) for r in items(data)])
