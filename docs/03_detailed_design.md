# PukiWiki MCP サーバー 詳細設計書

| 項目 | 内容 |
| --- | --- |
| ドキュメントID | 03_detailed_design |
| バージョン | 1.0 |
| 作成日 | 2026-06-16 |
| フェーズ | 詳細設計 |
| 対応 | 02_design.md |

---

## 1. モジュール `config.py`

### 1.1 クラス `Settings`（dataclass, frozen）

| 属性 | 型 | 既定 | 由来 env |
| --- | --- | --- | --- |
| `base_url` | str | （必須） | `PUKIWIKI_URL` |
| `encoding` | str | `"euc-jp"` | `PUKIWIKI_ENCODING` |
| `timeout` | float | `30.0` | `PUKIWIKI_TIMEOUT` |
| `verify_tls` | bool | `True` | `PUKIWIKI_VERIFY_TLS` |
| `user_agent` | str | `"pukiwiki-mcp/<ver>"` | `PUKIWIKI_USER_AGENT` |
| `basic_user` | str \| None | `None` | `PUKIWIKI_BASIC_USER` |
| `basic_password` | str \| None | `None` | `PUKIWIKI_BASIC_PASSWORD` |

### 1.2 メソッド/関数

```
@classmethod from_env(cls, env: Mapping[str,str] | None = None) -> Settings
    - env 省略時は os.environ
    - PUKIWIKI_URL 未設定/空 → ConfigError
    - base_url: 末尾に "/" が無ければ付与（rstrip して + "/" ではなく、index.php 等
      末尾がスクリプトの場合も考慮し "値の末尾が / でなければそのまま結合可能な形" に正規化）
      → 正規化規則: もし末尾が "/" でなければ、クエリ結合時に "?" を付ける基準とする。
        実装上は base_url をそのまま保持し、query 結合は urllib で行う(§2.3)。
    - bool 解釈: "1/true/yes/on"(大小無視)=True, "0/false/no/off"=False
    - timeout: float 変換失敗 → ConfigError

encode_hint_char(encoding) -> str
    - "euc-jp"|"eucjp"|"utf-8"|"utf8" → "ぷ"
    - その他 → ""（PukiWiki 側の auto 検出に委ねる）
```

`ConfigError(Exception)` を本モジュールに定義。

## 2. モジュール `client.py`

### 2.1 例外階層

```
PukiWikiError(Exception)              # 基底
├── PageNotFoundError                 # ページ未存在
├── PukiWikiConflictError             # digest 不一致(同時編集)
└── PukiWikiReadOnlyError             # READONLY/凍結で書込不可
```

### 2.2 純粋関数（テスト対象・ネットワーク非依存）

| 関数 | シグネチャ | 仕様 |
| --- | --- | --- |
| `parse_source_html` | `(html: str) -> str` | `<pre id="source">…</pre>` の内側を取り出し HTML アンエスケープして返す。`id="source"` が無ければ `PageNotFoundError` |
| `parse_page_list` | `(html: str) -> list[str]` | `?cmd=read&page=` を含む `<a href>` から `page` クエリ値を抽出・重複排除・ソート。URL デコードして返す |
| `parse_search_results` | `(html: str) -> list[str]` | 検索結果本文 `<div id|class="search"...>` 配下、または結果リンク群からヒットページ名を抽出。`parse_page_list` を再利用 |
| `parse_diff_html` | `(html: str) -> str` | `<pre ...>…</pre>`（先頭の pre）内をアンエスケープして返す |
| `parse_rss` | `(xml: str, limit: int\|None) -> list[dict]` | RSS(1.0/2.0) の `item` から `title`/`link` と `date`(dc:date or pubDate) を抽出。新しい順、`limit` 件 |
| `extract_digest` | `(html: str) -> str` | `<input ... name="digest" ... value="HEX">` から MD5 を抽出。無ければ "" |
| `extract_textarea_msg` | `(html: str) -> str` | `<textarea name="msg">…</textarea>` 内をアンエスケープ（read 補助・予備） |
| `is_conflict_page` | `(html: str) -> bool` | 衝突フォームの特徴（複数 digest/衝突メッセージ ID 等）で判定 |
| `looks_readonly` | `(html: str) -> bool` | "PKWK_READONLY" 文言や freeze 由来の文言を検出 |

> パースは `html.parser.HTMLParser` を継承した小さなコレクタクラスで実装。
> `id`/`name` 属性をアンカーにし、スキン依存の class 名には極力依存しない（リスク対応）。

#### アンカー抽出の詳細（`parse_page_list`）
- `<a href="...">` の href を解析し、クエリ文字列に `cmd=read`（または `plugin=read`）かつ
  `page=` を含むものを採用、もしくは `?` 直後がページ名のみのリンク（`index.php?PageName`）形式に対応。
- href 中の `page` 値を `urllib.parse` で取り出し、`unquote_to_bytes` → 設定エンコーディングで decode。
- 自ページ・編集リンク（`cmd=edit` 等）や `#` アンカーは除外。

### 2.3 クラス `PukiWikiClient`

```
__init__(self, settings: Settings, http: httpx.Client | None = None)
    - self._enc = settings.encoding
    - self._hint = encode_hint_char(self._enc)
    - http 未指定なら _build_client() で httpx.Client を生成
        headers={"User-Agent": ua}, timeout, verify=verify_tls,
        auth=(user,pass) if basic_user else None,
        follow_redirects=False   # ★ 書込成功(302)を自前判定するため自動追従しない

close(self)  /  __enter__/__exit__   … httpx.Client のライフサイクル管理
```

#### 2.3.1 内部ヘルパ

```
_build_url(self, **params) -> str
    - urllib で base_url にクエリを連結。
    - 値は str のまま渡し、エンコードは _encode_params で実施。

_encode_query(self, params: dict[str,str]) -> str
    - 各 value を設定エンコーディングで bytes 化 → quote_from_bytes(safe="")
    - "k=v&k2=v2" を生成（GET のページ名を EUC-JP %エンコードするため）

_get(self, params: dict[str,str]) -> str
    - GET base_url?<_encode_query(params)>
    - resp.content(bytes) を 設定エンコーディングで decode(errors="replace")
    - HTTP>=400 → PukiWikiError

_post_edit(self, fields: dict[str,str]) -> httpx.Response
    - fields に encode_hint を付与（self._hint が非空のとき）
    - 各 value を設定エンコーディングで bytes 化し application/x-www-form-urlencoded body 構築
      content = "&".join(quote(k)+"="+quote(v))  ※ quote は quote_from_bytes
    - headers Content-Type: application/x-www-form-urlencoded
    - POST して Response を返す（follow_redirects=False）
```

#### 2.3.2 公開メソッド（server から呼ばれる）

| メソッド | 戻り | 処理概要 |
| --- | --- | --- |
| `list_pages()` | `list[str]` | `_get(cmd=list)` → `parse_page_list` |
| `read_page(page)` | `str` | `_get(cmd=source,page)` → `parse_source_html`。未存在は `PageNotFoundError` |
| `page_exists(page)` | `bool` | `read_page` を試み、`PageNotFoundError` を False に変換 |
| `search_pages(query, and_search=True)` | `list[str]` | `_get(cmd=search,word,type)` → `parse_search_results` |
| `get_diff(page)` | `str` | `_get(cmd=diff,page)` → `parse_diff_html` |
| `get_recent_changes(limit)` | `list[dict]` | `_get(cmd=rss)` → `parse_rss` |
| `_fetch_digest(page)` | `str` | `_get(cmd=edit,page)` → `extract_digest` |
| `write_page(page, content, *, add=False, add_top=False)` | `str(action)` | §2.3.3 |
| `delete_page(page)` | `None` | `write_page(page, "")` 相当（空書込）。事前存在確認 |

#### 2.3.3 `write_page` アルゴリズム

```
1. existed = page_exists(page)          # created/updated の判別用
2. digest  = _fetch_digest(page)        # 新規ページは空文字相当の digest が返る
3. fields = {cmd:"edit", page, digest, msg:content, write:"1"}
   if add:      fields["add"]="true"
   if add_top:  fields["add_top"]="true"
4. resp = _post_edit(fields)
5. 判定:
   - 300<=status<400 かつ Location あり  → 成功
   - status==200:
        body = decode(resp)
        if is_conflict_page(body):  raise PukiWikiConflictError
        if looks_readonly(body):    raise PukiWikiReadOnlyError
        # それ以外の 200 は「削除完了画面」等の正常系もあるため成功扱い
        → 成功
   - status>=400 → PukiWikiError
6. action 決定: add→"appended" / content==""→"deleted" / existed→"updated" / else→"created"
```

> **新規ページの digest**: PukiWiki は存在しないページの編集フォームでも
> `md5("")`（=空ソースの MD5）を digest として埋め込む。`_fetch_digest` はその値を
> そのまま使えばよく、特別扱い不要。

## 3. モジュール `server.py`

### 3.1 初期化

```
settings = Settings.from_env()
client   = PukiWikiClient(settings)
mcp      = FastMCP("pukiwiki")          # サーバー名
```

> クライアントはプロセス内シングルトン（httpx.Client を再利用しコネクションを保つ）。
> プロセス終了時に `client.close()`。

### 3.2 ツール定義（`@mcp.tool()`）

各ツールは型注釈付きの関数として定義し、docstring を LLM 向け説明にする。
内部で `PukiWikiError` を捕捉し、戻り値の dict にエラーを載せる（プロセスは落とさない）。

| 関数 | 引数（型） | 戻り（dict） |
| --- | --- | --- |
| `list_pages()` | — | `{"pages":[...], "count":n}` |
| `read_page(page:str)` | page | `{"page","exists":bool,"content":str}` |
| `page_exists(page:str)` | page | `{"page","exists":bool}` |
| `search_pages(query:str, and_search:bool=True)` | query, and_search | `{"query","pages":[...],"count":n}` |
| `get_page_diff(page:str)` | page | `{"page","diff":str}` |
| `get_recent_changes(limit:int=20)` | limit | `{"changes":[{"page","date"}],"count":n}` |
| `create_or_update_page(page:str, content:str)` | page, content | `{"page","ok":bool,"action":str}` |
| `append_to_page(page:str, content:str, to_top:bool=False)` | page, content, to_top | `{"page","ok":bool,"action":"appended"}` |
| `delete_page(page:str)` | page | `{"page","ok":bool,"action":"deleted"}` |

### 3.3 共通エラー整形

```
try:
    ...client 呼出...
except PageNotFoundError:
    return {"page":page,"exists":False,...}   # 読取系
except PukiWikiConflictError:
    return {"page":page,"ok":False,"error":"conflict: 他者により更新されています。再取得して再試行してください"}
except PukiWikiReadOnlyError:
    return {"page":page,"ok":False,"error":"readonly: 書込が許可されていません(凍結/READONLY)"}
except PukiWikiError as e:
    return {"page":page,"ok":False,"error":str(e)}
```

入力バリデーション: `page` 空文字は即エラー dict を返す。

## 4. モジュール `__main__.py`

```
from .server import mcp, client
def main():
    try:
        mcp.run()              # stdio トランスポート
    finally:
        client.close()
if __name__ == "__main__":
    main()
```

`ConfigError` は import 時（server.py の `Settings.from_env()`）に送出され、
スタックトレースとともに即終了（fail-fast, NFR-起動）。

## 5. シーケンス（create_or_update_page）

```
Client        server.create_or_update_page    PukiWikiClient            PukiWiki
  |  tools/call (page, content)  |                   |                      |
  |----------------------------->|                   |                      |
  |                              | write_page(...)   |                      |
  |                              |------------------>| GET ?cmd=edit&page    |
  |                              |                   |--------------------->| 
  |                              |                   |<---- edit form ------|
  |                              |                   | extract_digest        |
  |                              |                   | POST cmd=edit(+write) |
  |                              |                   |--------------------->|
  |                              |                   |<-- 302 Location ------|
  |                              |<-- "updated" -----|                      |
  |<-- {ok:true,action:updated}--|                   |                      |
```

## 6. 文字コード処理 詳細仕様

| 局面 | 入力 | 変換 | 送出/格納 |
| --- | --- | --- | --- |
| GET クエリ生成 | `str`(UTF-8) | `str.encode(enc)` → `quote_from_bytes` | %xx 列（EUC-JP バイト） |
| GET レスポンス | bytes | `bytes.decode(enc, "replace")` | `str` |
| POST body 生成 | `str` | `str.encode(enc)` → `quote_from_bytes`、`encode_hint`=ヒント文字 | EUC-JP body |
| POST レスポンス | bytes | `bytes.decode(enc, "replace")` | `str` |
| RSS | bytes | XML 宣言の encoding に従い ElementTree が解釈（必要なら enc 指定で decode 後に fromstring） | `str` |

> RSS は文字コード宣言を持つため、`resp.content`(bytes) を ElementTree に渡すか、
> 設定エンコーディングで decode 後に宣言を除去して `fromstring` する。
> 実装では「bytes をそのまま `ET.fromstring`」を基本とし、失敗時に decode フォールバック。

## 7. ロギング

- `logging` を使用し、`stderr` へ出力（stdio の JSON-RPC は stdout を占有するため
  **ログは必ず stderr**）。レベルは環境変数 `PUKIWIKI_LOG_LEVEL`（既定 `INFO`）。
- HTTP のメソッド・cmd・page・ステータス・所要は DEBUG で記録。本文はログしない。

## 8. テスト設計の指針（→ Phase6 で実装）

| 種別 | 対象 | 方法 |
| --- | --- | --- |
| 単体 | 純粋パース関数（§2.2） | 固定 HTML/RSS 文字列を入力し期待出力を検証 |
| 単体 | `Settings.from_env` | env 辞書を与え正規化/例外を検証 |
| 単体 | エンコーディング往復 | `encode→quote→unquote→decode` の一致確認（日本語） |
| 結合 | `PukiWikiClient` の各メソッド | `httpx.MockTransport` でレスポンスを差し替え、ネットワークなしで検証 |
| 結合 | `write_page` の 302/衝突/READONLY 分岐 | MockTransport でステータス/本文を出し分け |
