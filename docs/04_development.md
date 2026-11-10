# PukiWiki MCP サーバー 開発フェーズ報告書

| 項目 | 内容 |
| --- | --- |
| ドキュメントID | 04_development |
| バージョン | 1.0 |
| 作成日 | 2026-06-16 |
| フェーズ | 開発 |
| 対応 | 03_detailed_design.md |

---

## 1. 成果物一覧

| パス | 役割 | 対応設計 |
| --- | --- | --- |
| `src/pukiwiki_mcp/__init__.py` | パッケージ定義・バージョン | — |
| `src/pukiwiki_mcp/config.py` | 環境変数読込 `Settings` / `ConfigError` / `encode_hint_char` | 詳細設計 §1 |
| `src/pukiwiki_mcp/client.py` | HTTP クライアント・パーサ・例外 | 詳細設計 §2 |
| `src/pukiwiki_mcp/server.py` | FastMCP・9 ツール定義 | 詳細設計 §3 |
| `src/pukiwiki_mcp/__main__.py` | stdio 起動エントリ | 詳細設計 §4 |
| `pyproject.toml` | パッケージ/依存/スクリプト定義 | 基本設計 §8 |
| `.env.example` | 環境変数サンプル | 要件 §5.3 |
| `README.md` | 利用手順 | — |

## 2. 実装上の要点

### 2.1 設定 (`config.py`)
- `Settings` は `frozen` dataclass。`from_env()` で `os.environ`（テスト時は任意 dict）から構築。
- `PUKIWIKI_URL` 欠落で `ConfigError`（fail-fast）。
- `PUKIWIKI_ENCODING` は `"x".encode(enc)` で早期に妥当性検証（不正名は `ConfigError`）。
- `encode_hint`（プロパティ）は `euc-jp`/`utf-8` 系で `'ぷ'`、その他は空文字。
- `basic_auth`（プロパティ）はユーザ未設定時 `None`（＝認証なし）。

### 2.2 パーサ (`client.py`, 純粋関数)
- `html.parser.HTMLParser`（`convert_charrefs=True`）で実体参照を自動アンエスケープ。
  → `parse_source_html` / `parse_diff_html` は `<pre>` 内テキストを原文へ復元。
- ページ名抽出 `_page_from_href` は **スキン非依存**の `href` 解析:
  - `index.php?PageName`（標準 URI ハンドラ）形式
  - `?cmd=read&page=PageName` 形式
  - `cmd` が `edit/diff/source/search` 等の場合は除外（読みリンクのみ採用）。
- `extract_digest` は `name="digest"` の `value`（32 桁 16 進）を正規表現で抽出。
- `parse_rss` は名前空間に依存せず `localname == "item"` を走査し、
  `title` と `dc:date`/`pubDate` を取得（RSS 1.0/2.0 両対応）。

### 2.3 文字コード境界 (`client.py`)
- `_encode_pairs`: value を **wiki エンコーディングで bytes 化** → `quote_from_bytes`。
  GET/POST 双方の `k=v` 生成に共通使用。
- ページ名のデコードは `unquote_to_bytes` → `decode(encoding)`。
  `urllib.parse_qsl` を使わない（UTF-8 前提で EUC-JP バイトを破壊するため。後述 §3 参照）。
- レスポンスは `resp.content`(bytes) を `decode(encoding, "replace")`。
- POST 時のみ `encode_hint` を付与（GET は付けず rawurlencode 経路として扱わせる）。

### 2.4 書込フロー (`write_page`)
- `follow_redirects=False` で生成した `httpx.Client` を使用し、書込成功（302）を自前判定。
- 200 応答は `is_conflict_page`（`<textarea name="msg">` の有無）で衝突を判定、
  `looks_readonly` で凍結/READONLY を判定、それ以外（削除完了画面等）は成功扱い。
- `action` は `appended`/`deleted`/`updated`/`created` を返却。

### 2.5 MCP 層 (`server.py`)
- `@mcp.tool()` で 9 ツールを定義。型注釈から JSON Schema を自動生成。
- 既知例外（`PageNotFoundError`/`Conflict`/`ReadOnly`/`PukiWikiError`）を捕捉し
  構造化 dict（`error` キー等）で返却。プロセスは落とさない（NFR-03）。
- ログは `basicConfig` で **stderr** 出力（stdout は JSON-RPC 専用）。

## 3. 開発中に検出・修正した不具合

| # | 事象 | 原因 | 対処 |
| --- | --- | --- | --- |
| D-1 | `?cmd=read&page=<EUC-JP>` 形式のページ名が `?????` に化ける | `urllib.parse_qsl` が %xx を UTF-8 として decode し、EUC-JP バイトを置換文字に破壊 | 生のクエリを自前で `&`/`=` 分割し、`page` 値を `unquote_to_bytes` → `decode(encoding)` で復元するよう修正。`parse_qsl` 依存を撤去 |

## 4. 開発時の動作確認（スモーク）

| 確認 | 結果 |
| --- | --- |
| `python -m pukiwiki_mcp` 相当のサーバ import / 9 ツール登録 | OK（`list_tools()` が 9 件） |
| `parse_source_html` の実体参照アンエスケープ（`&lt; &amp; &quot;`） | OK |
| `extract_digest`（32 桁 MD5 抽出） | OK |
| ページ名抽出（bare-query / `page=` 両形式・EUC-JP・スラッシュ・空白） | OK |
| `parse_rss`（RDF + dc:date） | OK |
| `is_conflict_page` / `looks_readonly` | OK |

※ 体系的な検証は Phase 6（テスト）で `pytest` により自動化する。

## 5. 既知の制約・申し送り

- ページ一覧/検索は HTML スクレイピング依存（PukiWiki に API がないため）。
  スキン改変で `href` 形式が変わった場合は `_page_from_href` の調整が必要。
- `get_recent_changes` は `?cmd=rss` のフォーマット（既定 RSS 1.0）に依存。
- 添付ファイル・凍結操作・リネームは未対応（要件外、将来拡張候補）。
