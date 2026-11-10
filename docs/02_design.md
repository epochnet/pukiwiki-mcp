# PukiWiki MCP サーバー 基本設計書

| 項目 | 内容 |
| --- | --- |
| ドキュメントID | 02_design |
| バージョン | 1.0 |
| 作成日 | 2026-06-16 |
| フェーズ | 基本設計 |
| 対応要件 | 01_requirements.md |

---

## 1. 設計方針

1. **レイヤ分離**: 「MCP 公開層」「ドメイン/クライアント層」「設定層」を分離し、
   PukiWiki 仕様変更や MCP SDK 変更の影響を局所化する（NFR-06）。
2. **境界でのエンコーディング吸収**: アプリ内部は常に Python `str`(UTF-8 相当)。
   HTTP 送受信の境界でのみ EUC-JP（設定値）へ/から変換する（NFR-02）。
3. **純粋関数化**: HTML/RSS パースとエンコーディング変換は副作用のない関数として実装し、
   ネットワークなしで単体テスト可能にする（NFR-07）。
4. **例外の正規化**: 想定エラーは独自例外 `PukiWikiError` に正規化し、MCP 層で
   人間可読メッセージに変換。プロセスは落とさない（NFR-03）。

## 2. システム構成

```
┌────────────────┐   stdio (MCP/JSON-RPC)   ┌───────────────────────────┐   HTTP(S)    ┌──────────────┐
│  MCP Client    │ <----------------------> │   PukiWiki MCP Server     │ <----------> │  PukiWiki    │
│ (Claude 等)    │   tools/call, tools/list │   (Python / FastMCP)      │  ?cmd=...    │  (PHP/EUC-JP)│
└────────────────┘                          └───────────────────────────┘              └──────────────┘
```

- クライアントはローカルプロセスとして本サーバーを起動（stdio）。
- 本サーバーは環境変数で与えられた `PUKIWIKI_URL` に対し HTTP リクエストを発行。

## 3. コンポーネント構成

| コンポーネント | モジュール | 責務 |
| --- | --- | --- |
| 設定 | `config.py` | 環境変数の読込・検証・既定値補完（`Settings`） |
| HTTP/ドメイン | `client.py` | PukiWiki への HTTP 呼出、エンコーディング変換、レスポンスのパース（`PukiWikiClient`） |
| パース純粋関数 | `client.py`（モジュール関数） | HTML/RSS → 構造化データ（テスト対象） |
| 例外 | `client.py` | `PukiWikiError` および派生 |
| MCP 公開層 | `server.py` | FastMCP インスタンス、各ツールの定義と入出力整形 |
| 起動 | `__main__.py` | `python -m pukiwiki_mcp` でサーバー起動 |

### 3.1 依存関係（一方向）

```
__main__  ─▶  server  ─▶  client  ─▶  config
                  │            │
                  └────────────┴──▶  例外(PukiWikiError)
```

`client` は `server`（MCP）を一切知らない。これにより client 層を単独でテスト・再利用できる。

## 4. 外部インターフェース設計（PukiWiki 呼出マッピング）

| MCP ツール | HTTP メソッド | PukiWiki 呼出 | 主パラメータ | レスポンス処理 |
| --- | --- | --- | --- | --- |
| `list_pages` | GET | `?cmd=list` | — | アンカー抽出 → ページ名配列 |
| `read_page` | GET | `?cmd=source&page=P` | page | `<pre id="source">` 抽出 → アンエスケープ |
| `page_exists` | GET | `?cmd=source&page=P` | page | 取得成否/未存在判定 |
| `search_pages` | GET | `?cmd=search&word=W&type=AND` | word, type | 結果アンカー抽出 → ページ名配列 |
| `get_page_diff` | GET | `?cmd=diff&page=P` | page | `<pre>` 差分抽出 |
| `get_recent_changes` | GET | `?cmd=rss` | — | RSS（XML）パース → `{page,date}` |
| `create_or_update_page` | GET→POST | `?cmd=edit`（digest 取得）→ POST | page, digest, msg, write, encode_hint | 302/結果判定 |
| `append_to_page` | GET→POST | 同上 + `add=true` | …, add, add_top | 302/結果判定 |
| `delete_page` | GET→POST | 同上, `msg=""` | page, digest, write, encode_hint | 302/結果判定 |

### 4.1 書込フロー（FR-07/08/09 共通）

```
1. GET ?cmd=edit&page=P             … 編集フォーム取得
2. フォームから digest(MD5) を抽出
3. POST { cmd=edit, page=P, digest, msg=<content>, write=1, encode_hint=<hint> }
   - body は設定エンコーディング(EUC-JP)でエンコードして送信
4. レスポンス判定:
   - 302 (Location: ページURI)         → 成功
   - 200 かつ衝突フォーム               → 衝突エラー(PukiWikiConflictError)
   - 200 かつ "PKWK_READONLY"/凍結文言  → 書込不可エラー
```

> **エンコーディング設計の要点**: `encode_hint` フィールドに設定エンコーディングで
> 表現したヒント文字（ja: `ぷ`）を載せる。PukiWiki は `mb_detect_encoding(encode_hint)`
> で送信コードを判定し、POST 全体を内部コードへ変換する。本サーバーは body 全体を
> 同一エンコーディングで送るため、判定と実体が一致し変換が正しく行われる。

## 5. データモデル（MCP 入出力）

ツールの戻り値は LLM が解釈しやすいよう、原則 **dict / list[dict]** の構造化データで返す。

| 型 | フィールド | 説明 |
| --- | --- | --- |
| PageContent | `page: str`, `exists: bool`, `content: str` | ページソース |
| SearchResult | `query: str`, `pages: list[str]` | 検索結果 |
| RecentChange | `page: str`, `date: str` | 更新項目（ISO8601 文字列） |
| WriteResult | `page: str`, `ok: bool`, `action: str` | 書込結果（action: created/updated/appended/deleted） |
| DiffResult | `page: str`, `diff: str` | 差分テキスト |

## 6. エラーハンドリング設計

| 例外クラス | 発生条件 | MCP 層での扱い |
| --- | --- | --- |
| `PukiWikiError` | 基底。HTTP/パースの一般障害 | エラーメッセージを返す（toolError） |
| `PageNotFoundError` | 対象ページが存在しない | 読取系は「未存在」を正常応答として返す場合あり |
| `PukiWikiConflictError` | digest 不一致（同時編集衝突） | 衝突した旨を返し、再取得→再試行を促す |
| `PukiWikiReadOnlyError` | READONLY/凍結で書込不可 | 書込不可を返す |
| `ConfigError` | 必須環境変数欠落 | 起動時に即時失敗（fail-fast） |

## 7. 設定設計

- 起動時に `Settings.from_env()` で環境変数を読み、`PUKIWIKI_URL` 欠落時は `ConfigError`。
- URL は末尾スラッシュを正規化し、`?cmd=...` を付与するベースとして保持。
- `PUKIWIKI_ENCODING` から `encode_hint` 用ヒント文字をマッピング
  （`euc-jp`/`utf-8` → `ぷ`、それ以外は空＝自動検出に委ねる）。

## 8. 技術スタック

| 区分 | 採用 | 理由 |
| --- | --- | --- |
| 言語 | Python 3.11+ | 要件確定。型ヒント・標準ライブラリの HTML/XML パースが利用可 |
| MCP | `mcp`（FastMCP デコレータ API） | 公式 SDK。ツール定義が簡潔で型から JSON Schema を自動生成 |
| HTTP | `httpx` | 同期/非同期両対応・タイムアウト/TLS 設定が容易・バイト送受信の制御が明確 |
| HTML パース | 標準 `html.parser` / 正規表現 | 依存最小（NFR-08）。対象出力が限定的でアンカー属性が安定 |
| XML パース | 標準 `xml.etree.ElementTree` | RSS パース。依存追加不要 |
| テスト | `pytest` | パース/エンコーディングの純粋関数を中心に単体テスト |

## 9. ディレクトリ構成（予定）

```
pukiwiki-mcp/
├── docs/
│   ├── 01_requirements.md
│   ├── 02_design.md
│   ├── 03_detailed_design.md
│   ├── 04_development.md
│   ├── 05_review.md
│   └── 06_test.md
├── src/pukiwiki_mcp/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── client.py
│   └── server.py
├── tests/
│   ├── test_config.py
│   ├── test_parsing.py
│   └── test_client.py
├── pyproject.toml
├── .env.example
└── README.md
```

## 10. トレーサビリティ（要件 → 設計）

| 要件 | 設計上の実現箇所 |
| --- | --- |
| FR-01〜09 | §4 マッピング・§5 データモデル・`server.py` 各ツール |
| FR-設定(5.3) | §7 設定設計・`config.py` |
| NFR-01 | `Settings` による環境変数化 |
| NFR-02 | §4.1 エンコーディング設計（境界変換） |
| NFR-03/05 | §6 エラーハンドリング・書込フローの digest 検知 |
| NFR-06/07/08 | §3 レイヤ分離・§8 依存最小・純粋関数化 |
