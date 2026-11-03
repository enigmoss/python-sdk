"""Cursor pagination."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar

from enigmoss._http import ApiResult
from enigmoss._parse import Reader, items

T = TypeVar("T")

__all__ = ["Page"]


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of a list, newest first.

    Pass ``next_cursor`` as ``cursor=`` to get the next page, or use ``iterate()`` to walk all of
    them. Iterating a page yields its items.

    Attributes:
        items: The objects of this page.
        next_cursor: Cursor of the next page, ``None`` on the last one.
        has_more: ``True`` when there is a next page.
    """

    items: tuple[T, ...]
    next_cursor: str | None
    has_more: bool

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


def page_from(result: ApiResult, parse: Callable[[Reader], T]) -> Page[T]:
    cursor = result.meta.get("cursor")
    cursor = cursor if isinstance(cursor, dict) else {}
    next_cursor = cursor.get("next")
    return Page(
        items=result.parse(lambda data: tuple(parse(r) for r in items(data))),
        next_cursor=next_cursor if isinstance(next_cursor, str) else None,
        has_more=cursor.get("has_more") is True,
    )


def walk(fetch: Callable[[str | None], Page[T]]) -> Iterator[T]:
    """Lazily walks all pages, fetching the next one only when the previous one is consumed."""
    cursor: str | None = None
    while True:
        page = fetch(cursor)
        yield from page.items
        cursor = page.next_cursor
        if not page.has_more or cursor is None:
            return
