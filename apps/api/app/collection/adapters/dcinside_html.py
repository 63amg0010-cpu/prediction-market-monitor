"""Author-free parsing of reviewed DCInside gallery HTML."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import ClassVar, Final, final, override

from pydantic import BaseModel, ConfigDict

_DIGITS: Final = re.compile(r"\d+")
_WHITESPACE: Final = re.compile(r"[^\S\n]+")
_EXCESS_NEWLINES: Final = re.compile(r"\n{3,}")
_KST: Final = timezone(timedelta(hours=9))
_BODY_BLOCK_TAGS: Final = frozenset({"br", "div", "li", "p"})


class DCInsidePostDocument(BaseModel):
    """Parsed post fields that exclude every author identity attribute."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    source_post_id: str
    title: str
    body: str
    published_at: datetime
    comments_count: int
    upvote_or_score: int


@final
class _ListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._current_post_id: str | None = None
        self.post_ids: list[str] = []

    @override
    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "tr":
            return
        classes = _attribute(attrs, "class")
        post_id = _attribute(attrs, "data-no")
        if (
            classes is not None
            and "us-post" in classes.split()
            and post_id is not None
            and post_id.isdecimal()
        ):
            self._current_post_id = post_id

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag != "tr" or self._current_post_id is None:
            return
        if self._current_post_id not in self.post_ids:
            self.post_ids.append(self._current_post_id)
        self._current_post_id = None


@final
class _ViewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._body_depth = 0
        self._ignored_depth = 0
        self._in_title = False
        self._in_score = False
        self._in_comments = False
        self._title_parts: list[str] = []
        self._body_parts: list[str] = []
        self._score_parts: list[str] = []
        self._comment_parts: list[str] = []
        self.published_raw: str | None = None

    @override
    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self._body_depth > 0:
            if tag in {"script", "style"}:
                self._ignored_depth += 1
            if tag == "div":
                self._body_depth += 1
            if tag in _BODY_BLOCK_TAGS and self._ignored_depth == 0:
                self._body_parts.append("\n")
            return
        classes = (_attribute(attrs, "class") or "").split()
        if tag == "div" and "write_div" in classes:
            self._body_depth = 1
        if tag == "span" and "title_subject" in classes:
            self._in_title = True
        if tag == "span" and "gall_reply_num" in classes:
            self._in_score = True
        if tag == "span" and "gall_comment" in classes:
            self._in_comments = True
        if tag == "span" and "gall_date" in classes and self.published_raw is None:
            self.published_raw = _attribute(attrs, "title")

    @override
    def handle_endtag(self, tag: str) -> None:
        if self._body_depth > 0:
            if tag in {"script", "style"} and self._ignored_depth > 0:
                self._ignored_depth -= 1
            if tag in _BODY_BLOCK_TAGS and self._ignored_depth == 0:
                self._body_parts.append("\n")
            if tag == "div":
                self._body_depth -= 1
            return
        if tag == "span":
            self._in_title = False
            self._in_score = False
            self._in_comments = False

    @override
    def handle_data(self, data: str) -> None:
        if self._body_depth > 0:
            if self._ignored_depth == 0:
                self._body_parts.append(data)
            return
        if self._in_title:
            self._title_parts.append(data)
        if self._in_score:
            self._score_parts.append(data)
        if self._in_comments:
            self._comment_parts.append(data)

    def document(self, source_post_id: str) -> DCInsidePostDocument:
        title = _single_line(self._title_parts)
        published_raw = self.published_raw
        if not title or published_raw is None:
            error_code = "required_post_fields_missing"
            raise DCInsideHtmlContractError(error_code)
        try:
            published_at = datetime.strptime(
                published_raw,
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=_KST).astimezone(UTC)
        except ValueError as error:
            error_code = "published_timestamp_invalid"
            raise DCInsideHtmlContractError(error_code) from error
        return DCInsidePostDocument(
            source_post_id=source_post_id,
            title=title,
            body=_body_text(self._body_parts),
            published_at=published_at,
            comments_count=_count(self._comment_parts),
            upvote_or_score=_count(self._score_parts),
        )


@final
class DCInsideHtmlContractError(ValueError):
    """Typed HTML contract failure without retaining provider content."""

    def __init__(self, code: str) -> None:
        """Retain only the stable failure code."""
        super().__init__(code)
        self.code: str = code


def parse_post_ids(source: str, limit: int) -> tuple[str, ...]:
    """Return unique numeric post IDs from the reviewed list page."""
    parser = _ListParser()
    parser.feed(source)
    parser.close()
    return tuple(parser.post_ids[:limit])


def parse_post_document(
    source: str,
    source_post_id: str,
) -> DCInsidePostDocument:
    """Return author-free post fields from one reviewed view page."""
    parser = _ViewParser()
    parser.feed(source)
    parser.close()
    return parser.document(source_post_id)


def _attribute(
    attrs: list[tuple[str, str | None]],
    name: str,
) -> str | None:
    return next((value for key, value in attrs if key == name), None)


def _single_line(parts: list[str]) -> str:
    return _WHITESPACE.sub(" ", "".join(parts)).strip()


def _body_text(parts: list[str]) -> str:
    value = _WHITESPACE.sub(" ", "".join(parts))
    lines = (line.strip() for line in value.splitlines())
    return _EXCESS_NEWLINES.sub("\n\n", "\n".join(line for line in lines if line))


def _count(parts: list[str]) -> int:
    match = _DIGITS.search("".join(parts))
    return int(match.group()) if match is not None else 0
