"""HTTP client and response parsers for PukiWiki.

This module wraps PukiWiki's ``?cmd=...`` web interface as a small client
(:class:`PukiWikiClient`) and provides pure parsing helpers that are
network-independent and unit-testable.

See docs/03_detailed_design.md (2).
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import quote, quote_from_bytes, unquote_to_bytes, urlsplit
from xml.etree import ElementTree as ET

import httpx

from .config import Settings

logger = logging.getLogger("pukiwiki_mcp.client")


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------
class PukiWikiError(Exception):
    """Base error for PukiWiki interactions."""


class PageNotFoundError(PukiWikiError):
    """Requested page does not exist."""


class PukiWikiConflictError(PukiWikiError):
    """Optimistic-lock (digest) conflict: the page was edited concurrently."""


class PukiWikiReadOnlyError(PukiWikiError):
    """Page is frozen or the wiki is read-only."""


# --------------------------------------------------------------------------
# Pure parsers (no network) -- see detailed design 2.2
# --------------------------------------------------------------------------
class _PreCollector(HTMLParser):
    """Collect text inside the first ``<pre>`` matching ``want_id`` (or any)."""

    def __init__(self, want_id: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self._want_id = want_id
        self._depth = 0
        self._buf: list[str] = []
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "pre":
            if self._depth:
                self._buf.append(self.get_starttag_text() or "")
            return
        if self._depth:  # nested <pre> (unlikely) -- keep collecting raw
            self._depth += 1
            self._buf.append(self.get_starttag_text() or "")
            return
        attr = {k: (v or "") for k, v in attrs}
        if self._want_id is None or attr.get("id") == self._want_id:
            self.found = True
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self._done = True
        elif self._depth:
            self._buf.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._buf.append(data)

    @property
    def text(self) -> str:
        return "".join(self._buf)


def parse_source_html(html: str) -> str:
    """Extract page source from a ``cmd=source`` response.

    Raises :class:`PageNotFoundError` if no ``<pre id="source">`` is present.
    """
    collector = _PreCollector(want_id="source")
    collector.feed(html)
    if not collector.found:
        raise PageNotFoundError("source block not found (page may not exist)")
    return collector.text


class _BodyHtmlCollector(HTMLParser):
    """Reconstruct the inner HTML of ``<div id="body">`` (the content area).

    The PukiWiki skin renders the page content inside ``<div id="body">`` and
    navigation (MenuBar etc.) in sibling ``<div id="menubar">``. Scoping anchor
    and ``<pre>`` extraction to the body avoids picking up skin navigation
    links. Entities are preserved (convert_charrefs=False) so the reconstructed
    fragment can be re-parsed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._active = False
        self._depth = 0
        self.found = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            if not self._active and dict(attrs).get("id") == "body":
                self._active = True
                self._depth = 1
                self.found = True
                return
            if self._active:
                self._depth += 1
        if self._active:
            self._parts.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._active:
            self._parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._active:
            self._depth -= 1
            if self._depth == 0:
                self._active = False
                return
        if self._active:
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._active:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._active:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._active:
            self._parts.append(f"&#{name};")

    @property
    def html(self) -> str:
        return "".join(self._parts)


def extract_body_html(html: str) -> str:
    """Return the inner HTML of ``<div id="body">``, or the whole input if the
    body marker is absent (e.g. a non-default skin)."""
    collector = _BodyHtmlCollector()
    collector.feed(html)
    return collector.html if collector.found else html


def parse_diff_html(html: str) -> str:
    """Extract diff text from the first ``<pre>`` in the content body."""
    collector = _PreCollector(want_id=None)
    collector.feed(extract_body_html(html))
    return collector.text


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)


def _page_from_href(href: str, encoding: str) -> str | None:
    """Return the page name encoded in an ``href``, or ``None`` if it is not a
    page-read link.

    Handles both the standard ``index.php?PageName`` form (query without '=')
    and the ``?cmd=read&page=PageName`` form. Page names are percent-encoded in
    the wiki ``encoding``.
    """
    href = href.strip()
    if not href or href.startswith("#"):
        return None
    query = urlsplit(href).query
    if not query:
        return None

    if "=" in query:
        # Parse the raw (still percent-encoded) pairs ourselves: page names are
        # percent-encoded in the wiki encoding (often EUC-JP), so we must
        # unquote to *bytes* and decode with `encoding` rather than let
        # parse_qsl assume UTF-8.
        raw_params: dict[str, str] = {}
        for pair in query.split("&"):
            key, _, value = pair.partition("=")
            raw_params[key] = value
        cmd = raw_params.get("cmd") or raw_params.get("plugin")
        # Exclude action links (edit/diff/source/...) -- only plain reads.
        if cmd not in (None, "", "read"):
            return None
        if "page" not in raw_params:
            return None
        data = unquote_to_bytes(raw_params["page"])
    else:
        # Bare query == rawurlencoded page name (standard URI handler).
        data = unquote_to_bytes(query)

    name = data.decode(encoding, "replace").strip()
    return name or None


def parse_page_list(html: str, encoding: str = "euc-jp") -> list[str]:
    """Extract a sorted, de-duplicated list of page names from anchors in the
    content body (skin navigation is excluded via :func:`extract_body_html`)."""
    collector = _AnchorCollector()
    collector.feed(extract_body_html(html))
    names: set[str] = set()
    for href in collector.hrefs:
        name = _page_from_href(href, encoding)
        if name:
            names.add(name)
    return sorted(names)


def parse_search_results(html: str, encoding: str = "euc-jp") -> list[str]:
    """Extract matched page names from a legacy ``cmd=search`` response.

    Older PukiWiki renders results as page-read anchors, so this reuses the
    anchor extraction. The trailing search form contains no page-read links and
    is therefore ignored.

    Note: PukiWiki >= 1.5.x ships the ``search2`` plugin, whose result page
    contains only the (empty) search form -- results are fetched by JavaScript
    from a JSON endpoint. On those installs this parser returns nothing; see
    :meth:`PukiWikiClient.search_pages` and :func:`parse_search2_results`.
    """
    return parse_page_list(html, encoding)


class NotSearch2ResponseError(PukiWikiError):
    """A ``cmd=search2`` request did not return the expected JSON.

    Signals that the install lacks the search2 plugin so the caller can fall
    back to the legacy ``cmd=search`` HTML scrape.
    """


def parse_search2_results(payload: str | bytes) -> dict:
    """Parse one ``cmd=search2&action=query`` JSON response.

    Returns a dict with the fields the pagination loop needs::

        {
            "names": [page name, ...],   # matches in this chunk (in scan order)
            "search_done": bool,          # True once the whole wiki was scanned
            "next_start_index": int|None, # cursor for the next request
            "search_start_time": int|None,# continuation token to pass back
        }

    Raises :class:`NotSearch2ResponseError` if the payload is not the expected
    JSON object (e.g. an HTML error page on installs without the plugin).
    """
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise NotSearch2ResponseError("search2 response was not JSON") from exc
    if not isinstance(data, dict) or "results" not in data:
        raise NotSearch2ResponseError("search2 response missing 'results'")
    names = [
        r["name"]
        for r in data.get("results") or []
        if isinstance(r, dict) and r.get("name")
    ]
    return {
        "names": names,
        "search_done": bool(data.get("search_done")),
        "next_start_index": data.get("next_start_index"),
        "search_start_time": data.get("search_start_time"),
    }


_DIGEST_RE = re.compile(
    r'<input\b[^>]*\bname=["\']digest["\'][^>]*>', re.IGNORECASE
)
_VALUE_RE = re.compile(r'\bvalue=["\']([0-9a-fA-F]{32})["\']')


def extract_digest(html: str) -> str:
    """Extract the MD5 ``digest`` value from an edit form (or '' if absent)."""
    m = _DIGEST_RE.search(html)
    if not m:
        return ""
    v = _VALUE_RE.search(m.group(0))
    return v.group(1) if v else ""


class _TextareaCollector(HTMLParser):
    def __init__(self, name: str) -> None:
        super().__init__(convert_charrefs=True)
        self._name = name
        self._active = False
        self._buf: list[str] = []
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "textarea" and dict(attrs).get("name") == self._name:
            self._active = True
            self.found = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea":
            self._active = False

    def handle_data(self, data: str) -> None:
        if self._active:
            self._buf.append(data)

    @property
    def text(self) -> str:
        return "".join(self._buf)


def extract_textarea_msg(html: str) -> str:
    """Extract the contents of ``<textarea name="msg">`` (edit form body)."""
    c = _TextareaCollector("msg")
    c.feed(html)
    return c.text


def is_conflict_page(html: str) -> bool:
    """True when a write POST response re-rendered the edit form (= conflict).

    A successful edit redirects (302); a successful delete returns a plain
    message page. Only a digest collision returns the edit form again, which
    always contains ``<textarea name="msg">``.
    """
    c = _TextareaCollector("msg")
    c.feed(html)
    return c.found


_READONLY_MARKERS = (
    "PKWK_READONLY",
    "PKWK_SAFE_MODE",
)


def looks_readonly(html: str) -> bool:
    return any(marker in html for marker in _READONLY_MARKERS)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


def parse_rss(xml: str | bytes, limit: int | None = None) -> list[dict]:
    """Parse an RSS/RDF feed into ``[{"page": title, "date": iso}]``.

    Namespace-agnostic: works for RSS 1.0 (RDF + dc:date) and RSS 2.0
    (channel/item + pubDate).

    The input should already be decoded to ``str`` (the client decodes the
    response using the wiki encoding). The XML declaration is stripped before
    parsing because expat cannot handle a non-UTF-8 ``encoding="..."``
    declaration (e.g. EUC-JP), and rejects encoding declarations on ``str``.
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    xml = _XML_DECL_RE.sub("", xml, count=1).lstrip()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise PukiWikiError(f"invalid RSS: {exc}") from exc

    changes: list[dict] = []
    for item in root.iter():
        if _localname(item.tag) != "item":
            continue
        title = ""
        date = ""
        for child in item:
            ln = _localname(child.tag)
            text = (child.text or "").strip()
            if ln == "title" and not title:
                title = text
            elif ln == "date" and not date:  # dc:date
                date = text
            elif ln == "pubDate" and not date:
                date = text
        if title:
            changes.append({"page": title, "date": date})
    if limit is not None:
        changes = changes[: max(0, limit)]
    return changes


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


class PukiWikiClient:
    """Synchronous client over PukiWiki's web interface."""

    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._enc = settings.encoding
        self._hint = settings.encode_hint
        self._http = http or self._build_client(settings)

    @staticmethod
    def _build_client(settings: Settings) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
            verify=settings.verify_tls,
            auth=settings.basic_auth,
            follow_redirects=False,  # detect write success (302) ourselves
        )

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PukiWikiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level helpers -------------------------------------------------
    def _encode_pairs(self, params: dict[str, str]) -> str:
        """Encode params to ``k=v&...`` with values byte-encoded in wiki enc."""
        parts = []
        for key, value in params.items():
            kb = quote_from_bytes(key.encode("ascii"), safe="")
            vb = quote_from_bytes(value.encode(self._enc), safe="")
            parts.append(f"{kb}={vb}")
        return "&".join(parts)

    def _url_with_query(self, query: str) -> str:
        base = self._settings.base_url
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}{query}"

    def _get(self, params: dict[str, str]) -> str:
        url = self._url_with_query(self._encode_pairs(params))
        logger.debug("GET %s", params)
        resp = self._http.get(url)
        if resp.status_code >= 400:
            raise PukiWikiError(f"HTTP {resp.status_code} for {params}")
        return resp.content.decode(self._enc, "replace")

    def _post_edit(self, fields: dict[str, str]) -> httpx.Response:
        fields = dict(fields)
        if self._hint:
            fields["encode_hint"] = self._hint
        body = self._encode_pairs(fields)
        logger.debug("POST cmd=edit page=%s", fields.get("page"))
        return self._http.post(
            self._settings.base_url,
            content=body.encode("ascii"),
            headers={"Content-Type": _FORM_CONTENT_TYPE},
        )

    # -- read operations ---------------------------------------------------
    def list_pages(self) -> list[str]:
        return parse_page_list(self._get({"cmd": "list"}), self._enc)

    def read_page(self, page: str) -> str:
        return parse_source_html(self._get({"cmd": "source", "page": page}))

    def page_exists(self, page: str) -> bool:
        try:
            self.read_page(page)
            return True
        except PageNotFoundError:
            return False

    # Safety cap on search2 pagination requests. search2 scans the whole wiki
    # in chunks; this bounds the worst case if the server never reports
    # search_done (results are still returned, just possibly incomplete).
    _SEARCH2_MAX_REQUESTS = 500

    def search_pages(self, query: str, and_search: bool = True) -> list[str]:
        """Full-text search returning matching page names (sorted, unique).

        Uses the ``search2`` JSON endpoint (PukiWiki >= 1.5.x), which is what
        the wiki UI itself uses. Falls back to the legacy ``cmd=search`` HTML
        scrape on installs without the plugin.

        ``and_search`` requires every whitespace-separated term (search2's
        native behaviour); ``and_search=False`` unions the per-term matches.
        """
        terms = query.split()
        try:
            if and_search or len(terms) <= 1:
                names = self._search2(query)
            else:
                merged: set[str] = set()
                for term in terms:
                    merged.update(self._search2(term))
                names = list(merged)
        except NotSearch2ResponseError:
            logger.info("search2 unavailable; falling back to legacy cmd=search")
            html = self._get(
                {
                    "cmd": "search",
                    "word": query,
                    "type": "AND" if and_search else "OR",
                }
            )
            return parse_search_results(html, self._enc)
        return sorted(set(names))

    def _search2(self, query: str) -> list[str]:
        """Run a single search2 query, paginating until the scan is complete."""
        names: list[str] = []
        seen: set[str] = set()
        start = 0
        search_start_time: int | None = None
        for _ in range(self._SEARCH2_MAX_REQUESTS):
            chunk = parse_search2_results(self._get_search2(query, start, search_start_time))
            if chunk["search_start_time"] is not None:
                search_start_time = chunk["search_start_time"]
            for name in chunk["names"]:
                if name not in seen:
                    seen.add(name)
                    names.append(name)
            if chunk["search_done"]:
                break
            nxt = chunk["next_start_index"]
            if not isinstance(nxt, int) or nxt <= start:
                break  # no forward progress -- avoid an infinite loop
            start = nxt
        else:
            logger.warning(
                "search2 hit the %d-request cap; results may be incomplete",
                self._SEARCH2_MAX_REQUESTS,
            )
        return names

    def _get_search2(self, query: str, start: int, search_start_time: int | None) -> str:
        """GET one search2 chunk. The JSON API is UTF-8 regardless of the wiki
        encoding (the wiki UI builds the URL with ``encodeURIComponent``)."""
        parts = [
            "cmd=search2",
            "action=query",
            "encode_hint=" + quote("ぷ", safe=""),
            "q=" + quote(query, safe=""),
            "start=" + str(start),
        ]
        if search_start_time is not None:
            parts.append("search_start_time=" + quote(str(search_start_time), safe=""))
        url = self._url_with_query("&".join(parts))
        logger.debug("GET cmd=search2 q=%r start=%d", query, start)
        resp = self._http.get(url)
        if resp.status_code >= 400:
            # Treat as "no search2 here" so the caller can fall back.
            raise NotSearch2ResponseError(f"HTTP {resp.status_code} for cmd=search2")
        return resp.content.decode("utf-8", "replace")

    def get_diff(self, page: str) -> str:
        return parse_diff_html(self._get({"cmd": "diff", "page": page}))

    def get_recent_changes(self, limit: int | None = None) -> list[dict]:
        return parse_rss(self._get({"cmd": "rss"}), limit)

    # -- write operations --------------------------------------------------
    def _fetch_digest(self, page: str) -> str:
        return extract_digest(self._get({"cmd": "edit", "page": page}))

    def write_page(
        self,
        page: str,
        content: str,
        *,
        add: bool = False,
        add_top: bool = False,
    ) -> str:
        """Create/update/append/delete a page. Returns the action performed."""
        existed = self.page_exists(page)
        digest = self._fetch_digest(page)

        fields = {
            "cmd": "edit",
            "page": page,
            "digest": digest,
            "msg": content,
            "write": "1",
        }
        if add:
            fields["add"] = "true"
            if add_top:
                fields["add_top"] = "true"

        resp = self._post_edit(fields)
        status = resp.status_code

        if 300 <= status < 400 and resp.headers.get("location"):
            pass  # success: PukiWiki redirects to the saved page after write
        elif status == 200:
            # A successful non-empty write always redirects (302). A 200 means
            # the write was NOT applied, except for deletion (empty content),
            # which renders a plain "deleted" message page with status 200.
            body = resp.content.decode(self._enc, "replace")
            if is_conflict_page(body):
                raise PukiWikiConflictError(
                    "page was modified concurrently (digest mismatch)"
                )
            if looks_readonly(body):
                raise PukiWikiReadOnlyError("page is frozen or wiki is read-only")
            if content != "":
                raise PukiWikiError(
                    "write was not applied (page may be frozen or editing prohibited)"
                )
            # content == "" : deletion succeeded
        else:
            raise PukiWikiError(f"unexpected HTTP {status} on write")

        if add:
            return "appended"
        if content == "":
            return "deleted"
        return "updated" if existed else "created"

    def delete_page(self, page: str) -> None:
        if not self.page_exists(page):
            raise PageNotFoundError(f"page does not exist: {page}")
        self.write_page(page, "")
