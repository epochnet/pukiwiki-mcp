# PukiWiki MCP サーバー 要件定義書

| 項目 | 内容 |
| --- | --- |
| ドキュメントID | 01_requirements |
| バージョン | 1.0 |
| 作成日 | 2026-06-16 |
| フェーズ | 要件定義 |
| ステータス | 確定 |

---

## 1. 背景・目的

本リポジトリは PHP 製 Wiki エンジン **PukiWiki**（`LANG='ja'` / 内部エンコーディング EUC-JP）の
インストール一式である。これに対し、**MCP (Model Context Protocol) サーバー**を新規開発し、
Claude などの MCP クライアント（LLM エージェント）から PukiWiki のページを
**閲覧・検索・作成・更新・削除**できるようにすることを目的とする。

PukiWiki は REST API を持たず、`?cmd=xxx` 形式の Web インターフェース（HTML / RSS 出力）
しか提供しない。本 MCP サーバーはこの Web インターフェースを **外部の HTTP クライアント**として
ラップし、構造化されたツール（tool）として LLM に公開するアダプタ層に相当する。

## 2. スコープ

### 2.1 対象（In Scope）

- PukiWiki への HTTP アクセスによるページ操作（読み書き両方）
- 接続先 URL 等のパラメータを **環境変数**で外部から指定できること
- MCP stdio トランスポートでの提供（Claude Desktop / Claude Code 等から起動）
- EUC-JP ⇔ UTF-8 のエンコーディング変換の吸収

### 2.2 対象外（Out of Scope）

- PukiWiki 本体（PHP）の改修
- 添付ファイル（attach プラグイン）のアップロード／ダウンロード ※将来拡張候補
- ページの凍結（freeze）/ 凍結解除、リネーム、バックアップ世代管理
- 認証（Basic 認証）機能 — 本リリースでは **認証なし**前提（後述 5.3 で拡張余地のみ確保）
- MCP の HTTP/SSE トランスポート（stdio のみ）

## 3. 用語

| 用語 | 説明 |
| --- | --- |
| MCP | Model Context Protocol。LLM にツール/リソースを公開する標準プロトコル |
| ツール (tool) | LLM が呼び出せる関数。本サーバーが公開する操作単位 |
| ページソース | PukiWiki ページの生テキスト（PukiWiki 整形ルール記法） |
| digest | 編集対象ページの現ソースの MD5。楽観的衝突検知に使用 |
| encode_hint | PukiWiki が POST 入力の文字コードを判定するためのヒント文字 |
| cmd | PukiWiki の動作指定パラメータ（`read`/`edit`/`source`/`search` 等） |

## 4. 前提条件（対象 PukiWiki の調査結果）

実コードの調査により確定した、本サーバーが依存する PukiWiki の仕様。

| # | 項目 | 確認結果 | 根拠 |
| --- | --- | --- | --- |
| P1 | 内部エンコーディング | **EUC-JP**（`SOURCE_ENCODING` / `CONTENT_CHARSET`） | `pukiwiki.ini.php`: `define('LANG','ja')` かつ `PKWK_UTF8_ENABLE` 未定義 → `lib/init.php` の分岐で EUC-JP |
| P2 | POST 入力の文字コード変換 | `encode_hint` を `mb_detect_encoding` で判定し `SOURCE_ENCODING` へ変換 | `lib/init.php` |
| P3 | `encode_hint` の値（ja） | `ぷ`（U+3077） | `ja.lng.php`: `$_LANG['encode_hint']['ja']` |
| P4 | ページソース取得 | `?cmd=source&page=...` が `<pre id="source">…</pre>` を返す | `plugin/source.inc.php` |
| P5 | 編集フォーム | `?cmd=edit&page=...` が `<textarea name="msg">` と `<input name="digest">` を返す | `lib/html.php: edit_form()` |
| P6 | 書込 | `cmd=edit` の POST に `write` キーが存在すると書込実行。`digest` 不一致時は衝突フォーム(200)を返す | `plugin/edit.inc.php: plugin_edit_write()` |
| P7 | 削除 | 書込時に `msg` が空文字ならページ削除 | `plugin/edit.inc.php` |
| P8 | 追記 | `add=true`（+`add_top=true` で先頭）で既存末尾/先頭に追記 | `plugin/edit.inc.php` |
| P9 | 検索 | `?cmd=search&word=...&type=AND|OR` が結果 HTML を返す | `plugin/search.inc.php` |
| P10 | 差分 | `?cmd=diff&page=...` が `<pre>` 差分を返す | `plugin/diff.inc.php` |
| P11 | 最近の更新 | `?cmd=rss`（RSS 1.0/2.0）で構造化された更新一覧を取得可能 | `plugin/rss.inc.php` |
| P12 | ページ一覧 | `?cmd=list` がページ名リンクの一覧 HTML を返す | `plugin/list.inc.php` |
| P13 | 安全モード | `PKWK_SAFE_MODE` / `PKWK_READONLY` は ini 未定義＝既定 OFF | `pukiwiki.ini.php` |
| P14 | 認証 | `.htaccess` の Basic 認証ディレクティブはコメントアウト済（認証なし） | `.htaccess` |

## 5. 機能要件

### 5.1 公開ツール一覧（FR）

| ID | ツール名 | 種別 | 概要 |
| --- | --- | --- | --- |
| FR-01 | `list_pages` | 読 | 全ページ名の一覧を取得 |
| FR-02 | `read_page` | 読 | 指定ページのソース（生テキスト）を取得 |
| FR-03 | `page_exists` | 読 | 指定ページの存在有無を返す |
| FR-04 | `search_pages` | 読 | キーワード全文検索（AND/OR）。ヒットしたページ名を返す |
| FR-05 | `get_page_diff` | 読 | 指定ページの最新差分を取得 |
| FR-06 | `get_recent_changes` | 読 | 最近更新されたページ一覧（RSS 由来）を取得 |
| FR-07 | `create_or_update_page` | 書 | ページを新規作成または全文上書き更新 |
| FR-08 | `append_to_page` | 書 | 既存ページの末尾（または先頭）へ追記 |
| FR-09 | `delete_page` | 書 | 指定ページを削除 |

### 5.2 ツール別 詳細要件

- **FR-01 list_pages**
  - 入力: なし
  - 出力: ページ名の配列（システムページ・除外設定ページを除く既定動作）
- **FR-02 read_page**
  - 入力: `page`（ページ名, 必須）
  - 出力: ソーステキスト（UTF-8）。存在しない場合はその旨を示す
- **FR-03 page_exists**
  - 入力: `page`
  - 出力: 真偽
- **FR-04 search_pages**
  - 入力: `query`（キーワード, 必須）, `and_search`（真:AND / 偽:OR, 既定 AND）
  - 出力: ヒットしたページ名の配列
- **FR-05 get_page_diff**
  - 入力: `page`
  - 出力: 差分テキスト（`+`/`-` 行を含む）
- **FR-06 get_recent_changes**
  - 入力: `limit`（最大件数, 任意）
  - 出力: `{page, date}` の配列（新しい順）
- **FR-07 create_or_update_page**
  - 入力: `page`（必須）, `content`（必須）
  - 動作: 現 digest を取得 → 上書き書込。衝突時はエラーを返す
  - 出力: 成否・対象ページ
- **FR-08 append_to_page**
  - 入力: `page`（必須）, `content`（必須）, `to_top`（既定 false）
  - 出力: 成否
- **FR-09 delete_page**
  - 入力: `page`（必須）
  - 動作: 空内容で書込しページ削除
  - 出力: 成否

### 5.3 設定要件（環境変数）

| 環境変数 | 必須 | 既定値 | 説明 |
| --- | --- | --- | --- |
| `PUKIWIKI_URL` | ✅ | — | PukiWiki のベース URL（例: `https://wiki.example.com/`） |
| `PUKIWIKI_ENCODING` | — | `euc-jp` | Wiki 側の内部エンコーディング。UTF-8 サイトでは `utf-8` |
| `PUKIWIKI_TIMEOUT` | — | `30` | HTTP タイムアウト秒 |
| `PUKIWIKI_VERIFY_TLS` | — | `true` | TLS 証明書検証の有効/無効 |
| `PUKIWIKI_USER_AGENT` | — | `pukiwiki-mcp/<ver>` | 送信 User-Agent |
| `PUKIWIKI_BASIC_USER` | — | （空） | （拡張余地）Basic 認証ユーザ。未指定なら認証なし |
| `PUKIWIKI_BASIC_PASSWORD` | — | （空） | （拡張余地）Basic 認証パスワード |

> 認証は本リリースの要件外だが、`.htpasswd` の存在を踏まえ、将来 `.htaccess` で
> Basic 認証を有効化した場合に備え、環境変数の口だけ確保する（既定は認証なし）。

## 6. 非機能要件（NFR）

| ID | 区分 | 要件 |
| --- | --- | --- |
| NFR-01 | 可搬性 | 接続先・エンコーディング・TLS 等は環境変数で切替可能。コード変更不要 |
| NFR-02 | 文字コード | LLM/MCP 境界は UTF-8、PukiWiki 境界は設定エンコーディング。変換漏れによる文字化けを起こさない |
| NFR-03 | 堅牢性 | ページ不存在・凍結・衝突・HTTP エラーを区別し、明確なエラーメッセージを返す（例外で落とさない） |
| NFR-04 | 安全性 | 破壊的操作（更新/削除）は書込ツールに限定。読取ツールは副作用なし |
| NFR-05 | 整合性 | 更新は digest による楽観ロックで上書き衝突を検知する |
| NFR-06 | 保守性 | HTTP/エンコーディング/パースの責務を分離し、PukiWiki 仕様変更時の影響を局所化 |
| NFR-07 | テスト容易性 | HTML パース・エンコーディング変換を純粋関数化し、ネットワーク非依存で単体テスト可能にする |
| NFR-08 | 依存最小 | 外部依存は MCP SDK と HTTP クライアントに限定（HTML パースは標準ライブラリ） |

## 7. 制約条件

- 実装言語: **Python 3.11+**（公式 `mcp` / FastMCP を使用）
- トランスポート: **stdio**
- PukiWiki 本体は変更しない（外部クライアントとして振る舞う）
- PukiWiki は構造化 API を持たないため、一部機能は HTML スクレイピングに依存する
  （→ パースの堅牢性は設計で担保）

## 8. 受入基準（Acceptance Criteria）

| # | 基準 |
| --- | --- |
| AC-1 | `PUKIWIKI_URL` のみ設定すれば stdio で MCP サーバーが起動し、ツール一覧が列挙される |
| AC-2 | 日本語を含むページの作成 → 読取で、文字化けなく内容が一致する |
| AC-3 | 既存ページの更新後、`read_page` に反映され、`get_page_diff` に差分が出る |
| AC-4 | 存在しないページの `read_page` がクラッシュせず「未存在」を返す |
| AC-5 | 検索でヒットしたページ名一覧が取得できる |
| AC-6 | 削除後に `page_exists` が false を返す |
| AC-7 | 設計どおりの単体テストが全て通る |

## 9. リスクと対応方針

| リスク | 影響 | 対応 |
| --- | --- | --- |
| HTML 構造変更でパース破綻 | 読取系の機能不全 | パース箇所を局所化＋緩いマッチ＋単体テストで早期検知 |
| 文字コード判定ミス | 文字化け | `encode_hint` を明示送出し、エンコーディングを設定値で固定 |
| 同時編集による衝突 | 上書き失敗/データ損失 | digest 楽観ロックで検知しエラー返却 |
| スキン改変による出力差異 | パース差異 | `id="source"` 等の ID 属性をアンカーに使用（スキン非依存箇所を優先） |
