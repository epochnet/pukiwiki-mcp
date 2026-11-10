"""Integration tests for PukiWikiClient using httpx.MockTransport (no network)."""

import json
import urllib.parse

import httpx
import pytest

from pukiwiki_mcp.client import (
    PageNotFoundError,
    PukiWikiClient,
    PukiWikiConflictError,
    PukiWikiError,
    PukiWikiReadOnlyError,
)
from pukiwiki_mcp.config import Settings

BASE = "https://wiki.test/"
ENC = "euc-jp"


def euc_q(text: str) -> str:
    """EUC-JP percent-encoding as produced by the client."""
    return urllib.parse.quote_from_bytes(text.encode(ENC), safe="")


def make_client(handler) -> PukiWikiClient:
    settings = Settings.from_env({"PUKIWIKI_URL": BASE, "PUKIWIKI_ENCODING": ENC})
    http = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return PukiWikiClient(settings, http=http)


def euc_response(status: int, html: str, headers=None) -> httpx.Response:
    return httpx.Response(
        status, content=html.encode(ENC), headers=headers or {}
    )


# -- reads -----------------------------------------------------------------
def test_read_page_roundtrip_japanese():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.query.decode("ascii")
        body = '<html><body><pre id="source">本文\n* 見出し</pre></body></html>'
        return euc_response(200, body)

    client = make_client(handler)
    assert client.read_page("テストページ") == "本文\n* 見出し"
    # page name must be percent-encoded as EUC-JP in the query
    assert euc_q("テストページ") in captured["query"]
    assert "cmd=source" in captured["query"]


def test_read_page_not_found():
    def handler(request):
        return euc_response(200, "<html><body>The page does not exist</body></html>")

    assert make_client(handler).page_exists("無い") is False
    with pytest.raises(PageNotFoundError):
        make_client(handler).read_page("無い")


def test_list_pages():
    def handler(request):
        link = "./?" + euc_q("ページA")
        link2 = "./?" + euc_q("ページB")
        html = f'<div id="body"><a href="{link}">A</a><a href="{link2}">B</a></div>'
        return euc_response(200, html)

    assert make_client(handler).list_pages() == ["ページA", "ページB"]


def _utf8_q(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def _json_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json; charset=UTF-8"},
    )


def test_search_pages_uses_search2_with_utf8_query():
    captured = []

    def handler(request):
        captured.append(request.url.query.decode("ascii"))
        return _json_response(
            {"results": [{"name": "結果"}], "search_done": True}
        )

    client = make_client(handler)
    assert client.search_pages("語") == ["結果"]
    q = captured[0]
    assert "cmd=search2" in q and "action=query" in q
    # search2 query is UTF-8 percent-encoded, NOT the wiki (EUC-JP) encoding
    assert _utf8_q("語") in q
    assert euc_q("語") not in q


def test_search_pages_paginates_until_search_done():
    """search2 scans the wiki in chunks; results across pages are merged."""
    pages = [
        {"results": [{"name": "A"}], "search_done": False, "next_start_index": 100},
        {"results": [{"name": "B"}, {"name": "A"}], "search_done": False,
         "next_start_index": 200, "search_start_time": 123},
        {"results": [{"name": "C"}], "search_done": True},
    ]
    calls = {"n": 0}
    seen_sst = []

    def handler(request):
        q = request.url.query.decode("ascii")
        seen_sst.append("search_start_time=123" in q)
        resp = _json_response(pages[calls["n"]])
        calls["n"] += 1
        return resp

    # de-duplicated and sorted across all chunks
    assert make_client(handler).search_pages("x") == ["A", "B", "C"]
    assert calls["n"] == 3
    # the continuation token from chunk 2 is echoed on the 3rd request
    assert seen_sst[2] is True


def test_search_pages_or_unions_per_term():
    def handler(request):
        q = request.url.query.decode("ascii")
        term = "P" if _utf8_q("赤") in q else "Q"
        name = "赤ページ" if term == "P" else "青ページ"
        return _json_response({"results": [{"name": name}], "search_done": True})

    out = make_client(handler).search_pages("赤 青", and_search=False)
    assert out == ["赤ページ", "青ページ"]


def test_search_pages_falls_back_to_legacy_when_no_search2():
    captured = []

    def handler(request):
        q = request.url.query.decode("ascii")
        captured.append(q)
        if "cmd=search2" in q:
            # install without the plugin: an HTML error page, not JSON
            return euc_response(404, "<html><body>plugin not found</body></html>")
        link = "./?cmd=read&page=" + euc_q("結果")
        return euc_response(200, f'<div id="body"><a href="{link}">r</a></div>')

    out = make_client(handler).search_pages("語", and_search=False)
    assert out == ["結果"]
    assert any("cmd=search&" in q or q.startswith("cmd=search&") for q in captured)
    assert any("type=OR" in q for q in captured)


def test_get_diff():
    def handler(request):
        return euc_response(
            200, '<div id="body"><pre>+ 追加\n- 削除</pre></div>'
        )

    assert make_client(handler).get_diff("P") == "+ 追加\n- 削除"


def test_recent_changes():
    def handler(request):
        xml = (
            '<?xml version="1.0" encoding="EUC-JP"?>'
            '<rdf:RDF xmlns:rdf="r" xmlns:dc="d">'
            "<item><title>更新A</title><dc:date>2026-06-16</dc:date></item>"
            "</rdf:RDF>"
        )
        return euc_response(200, xml)

    out = make_client(handler).get_recent_changes(limit=5)
    assert out == [{"page": "更新A", "date": "2026-06-16"}]


# -- writes ----------------------------------------------------------------
def _edit_form(digest="0" * 32, existing_body=""):
    return (
        f'<form><input type="hidden" name="digest" value="{digest}" />'
        f'<textarea name="msg">{existing_body}</textarea></form>'
    )


def test_create_new_page_redirect_success():
    posted = {}

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.query.decode("ascii")
        if request.method == "GET" and "cmd=source" in q:
            return euc_response(200, "<html>does not exist</html>")  # not found
        if request.method == "GET" and "cmd=edit" in q:
            return euc_response(200, _edit_form())
        if request.method == "POST":
            posted["body"] = request.content.decode("ascii")
            return httpx.Response(302, headers={"location": BASE + "?" + euc_q("新規")})
        raise AssertionError(q)

    action = make_client(handler).write_page("新規", "新しい内容")
    assert action == "created"
    # POST body must carry EUC-JP encoded content, the digest, and encode_hint
    assert euc_q("新しい内容") in posted["body"]
    assert "digest=" + "0" * 32 in posted["body"]
    assert "encode_hint=" + euc_q("ぷ") in posted["body"]
    assert "write=1" in posted["body"]


def test_update_existing_page():
    def handler(request):
        q = request.url.query.decode("ascii")
        if request.method == "GET" and "cmd=source" in q:
            return euc_response(200, '<pre id="source">old</pre>')  # exists
        if request.method == "GET" and "cmd=edit" in q:
            return euc_response(200, _edit_form(digest="a" * 32, existing_body="old"))
        return httpx.Response(302, headers={"location": BASE + "?P"})

    assert make_client(handler).write_page("P", "new") == "updated"


def test_conflict_detected():
    def handler(request):
        q = request.url.query.decode("ascii")
        if request.method == "GET" and "cmd=source" in q:
            return euc_response(200, '<pre id="source">old</pre>')
        if request.method == "GET" and "cmd=edit" in q:
            return euc_response(200, _edit_form(digest="b" * 32))
        # write returns the edit form again (collision)
        return euc_response(200, _edit_form(digest="c" * 32, existing_body="theirs"))

    with pytest.raises(PukiWikiConflictError):
        make_client(handler).write_page("P", "mine")


def test_readonly_detected():
    def handler(request):
        q = request.url.query.decode("ascii")
        if request.method == "GET" and "cmd=source" in q:
            return euc_response(200, "<html>nope</html>")
        if request.method == "GET" and "cmd=edit" in q:
            return euc_response(200, "PKWK_READONLY prohibits editing")
        return euc_response(200, "PKWK_READONLY prohibits editing")

    with pytest.raises(PukiWikiReadOnlyError):
        make_client(handler).write_page("P", "x")


def test_frozen_nonempty_write_is_error_not_success():
    """A non-empty write returning 200 (no redirect) must NOT be a success."""

    def handler(request):
        q = request.url.query.decode("ascii")
        if request.method == "GET" and "cmd=source" in q:
            return euc_response(200, '<pre id="source">old</pre>')
        if request.method == "GET" and "cmd=edit" in q:
            return euc_response(200, _edit_form(digest="d" * 32))
        # editing prohibited message page (no textarea, no readonly marker)
        return euc_response(200, "<html><body>editing is prohibited</body></html>")

    with pytest.raises(PukiWikiError):
        make_client(handler).write_page("P", "content")


def test_delete_page_success():
    def handler(request):
        q = request.url.query.decode("ascii")
        if request.method == "GET" and "cmd=source" in q:
            return euc_response(200, '<pre id="source">old</pre>')  # exists
        if request.method == "GET" and "cmd=edit" in q:
            return euc_response(200, _edit_form(digest="e" * 32))
        # deletion renders a plain 200 message page (no redirect)
        return euc_response(200, "<html><body>deleted</body></html>")

    # should not raise
    make_client(handler).delete_page("P")


def test_delete_missing_page_raises():
    def handler(request):
        return euc_response(200, "<html>nope</html>")  # not found

    with pytest.raises(PageNotFoundError):
        make_client(handler).delete_page("無い")


def test_append_action():
    posted = {}

    def handler(request):
        q = request.url.query.decode("ascii")
        if request.method == "GET" and "cmd=source" in q:
            return euc_response(200, '<pre id="source">base</pre>')
        if request.method == "GET" and "cmd=edit" in q:
            return euc_response(200, _edit_form(digest="f" * 32))
        posted["body"] = request.content.decode("ascii")
        return httpx.Response(302, headers={"location": BASE + "?P"})

    assert make_client(handler).write_page("P", "more", add=True) == "appended"
    assert "add=true" in posted["body"]


def test_http_error_raises():
    def handler(request):
        return euc_response(500, "boom")

    with pytest.raises(PukiWikiError):
        make_client(handler).list_pages()
