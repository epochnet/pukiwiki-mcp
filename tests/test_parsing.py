"""Unit tests for the pure parsing helpers (network-independent)."""

import urllib.parse

import pytest

from pukiwiki_mcp import client as c


def euc_link(page: str) -> str:
    return "./?" + urllib.parse.quote_from_bytes(page.encode("euc-jp"))


def euc_page_link(page: str) -> str:
    return "./?cmd=read&page=" + urllib.parse.quote_from_bytes(page.encode("euc-jp"))


# -- source / diff ---------------------------------------------------------
def test_parse_source_unescapes_entities():
    html = '<html><pre id="source">a &lt;b&gt; &amp; &quot;c&quot;\n* 見出し</pre></html>'
    assert c.parse_source_html(html) == 'a <b> & "c"\n* 見出し'


def test_parse_source_missing_raises():
    with pytest.raises(c.PageNotFoundError):
        c.parse_source_html("<html><body>not found</body></html>")


def test_parse_diff_scoped_to_body_ignores_header_pre():
    html = (
        "<html><body><pre>HEADER</pre>"
        '<div id="body"><pre>+ added\n- removed\n&lt;x&gt;</pre></div>'
        "</body></html>"
    )
    assert c.parse_diff_html(html) == "+ added\n- removed\n<x>"


# -- page list / search ----------------------------------------------------
def test_page_list_bare_query_euc():
    html = f'<div id="body"><a href="{euc_link("テスト")}">テスト</a></div>'
    assert c.parse_page_list(html, "euc-jp") == ["テスト"]


def test_page_list_page_param_euc():
    html = f'<div id="body"><a href="{euc_page_link("日本語")}">x</a></div>'
    assert c.parse_page_list(html, "euc-jp") == ["日本語"]


def test_page_list_excludes_action_links():
    html = (
        '<div id="body">'
        '<a href="./?cmd=edit&page=x">edit</a>'
        '<a href="./?cmd=diff&amp;page=y">diff</a>'
        '<a href="./?cmd=search&word=z">search</a>'
        '<a href="#anchor">top</a>'
        "</div>"
    )
    assert c.parse_page_list(html, "euc-jp") == []


def test_page_list_excludes_menubar():
    html = (
        '<div id="contents">'
        f'<div id="body"><a href="{euc_link("本文ページ")}">p</a></div>'
        f'<div id="menubar"><a href="{euc_link("MenuBar")}">m</a></div>'
        "</div>"
    )
    assert c.parse_page_list(html, "euc-jp") == ["本文ページ"]


def test_page_list_dedup_and_sort():
    a = euc_link("Beta")
    b = euc_link("Alpha")
    html = f'<div id="body"><a href="{a}">B</a><a href="{a}">B</a><a href="{b}">A</a></div>'
    assert c.parse_page_list(html, "euc-jp") == ["Alpha", "Beta"]


def test_page_list_fallback_without_body():
    html = f'<a href="{euc_link("単独")}">x</a>'
    assert c.parse_page_list(html, "euc-jp") == ["単独"]


# -- digest / textarea / conflict / readonly -------------------------------
def test_extract_digest():
    html = '<input type="hidden" name="digest" value="0123456789abcdef0123456789ABCDEF" />'
    assert c.extract_digest(html) == "0123456789abcdef0123456789ABCDEF"


def test_extract_digest_absent():
    assert c.extract_digest("<form></form>") == ""


def test_is_conflict_page():
    assert c.is_conflict_page('<textarea name="msg">x</textarea>') is True
    assert c.is_conflict_page("<p>deleted</p>") is False


def test_looks_readonly():
    assert c.looks_readonly("PKWK_READONLY prohibits editing") is True
    assert c.looks_readonly("ok") is False


# -- rss -------------------------------------------------------------------
def test_parse_rss_rdf():
    xml = (
        '<?xml version="1.0"?>'
        '<rdf:RDF xmlns:rdf="r" xmlns:dc="d">'
        "<item><title>P1</title><link>u1</link>"
        "<dc:date>2026-06-16T10:00:00+09:00</dc:date></item>"
        "<item><title>P2</title><link>u2</link>"
        "<dc:date>2026-06-15T10:00:00+09:00</dc:date></item>"
        "</rdf:RDF>"
    )
    out = c.parse_rss(xml)
    assert out == [
        {"page": "P1", "date": "2026-06-16T10:00:00+09:00"},
        {"page": "P2", "date": "2026-06-15T10:00:00+09:00"},
    ]


def test_parse_rss_2_0_pubdate_and_limit():
    xml = (
        "<rss><channel>"
        "<item><title>A</title><pubDate>Mon, 16 Jun 2026 10:00:00 +0900</pubDate></item>"
        "<item><title>B</title><pubDate>Sun, 15 Jun 2026 10:00:00 +0900</pubDate></item>"
        "</channel></rss>"
    )
    out = c.parse_rss(xml, limit=1)
    assert out == [{"page": "A", "date": "Mon, 16 Jun 2026 10:00:00 +0900"}]


def test_parse_rss_invalid():
    with pytest.raises(c.PukiWikiError):
        c.parse_rss("<not-xml")
