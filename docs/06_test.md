# PukiWiki MCP サーバー テスト報告書

| 項目 | 内容 |
| --- | --- |
| ドキュメントID | 06_test |
| バージョン | 1.0 |
| 作成日 | 2026-06-16 |
| フェーズ | テスト |
| 実行環境 | Python 3.13 / pytest / httpx MockTransport |
| 結果 | **38 passed**（失敗 0） |

---

## 1. テスト方針

- ネットワークに依存しない自動テストとする（CI で再現可能にするため）。
  - パース・設定・エンコーディングは **純粋関数の単体テスト**。
  - HTTP を伴う `PukiWikiClient` は **`httpx.MockTransport`** でレスポンスを差し替える結合テスト。
- 日本語（EUC-JP）の往復・スキン混入除去・書込分岐（成功/衝突/READONLY/凍結/削除）を重点検証。

## 2. テスト構成

| ファイル | 区分 | 件数 | 対象 |
| --- | --- | --- | --- |
| `tests/test_config.py` | 単体 | 7 | `Settings.from_env`、bool/timeout/encoding 検証、`encode_hint_char`、Basic 認証 |
| `tests/test_parsing.py` | 単体 | 16 | source/diff/list/search/digest/conflict/readonly/RSS の各パーサ |
| `tests/test_client.py` | 結合 | 15 | 各メソッドの HTTP 呼出・エンコーディング・書込分岐 |
| 合計 | | **38** | |

## 3. 主なテストケースと意図

### 3.1 設定（test_config）
- 必須 `PUKIWIKI_URL` 欠落で `ConfigError`／既定値補完／不正 bool・timeout・encoding の拒否。
- `encode_hint_char` が `euc-jp`/`utf-8` で `'ぷ'`、その他で空。

### 3.2 パース（test_parsing）
- `parse_source_html`: 実体参照（`&lt; &amp; &quot;`）を原文へ復元。未存在で `PageNotFoundError`。
- `parse_page_list`: bare-query / `page=` 両形式の **EUC-JP ページ名**を正しくデコード。
  action リンク（edit/diff/search）・`#` アンカーを除外。**MenuBar（#menubar）を除外**。重複排除・ソート。
- `parse_diff_html`: `#body` に限定し、ヘッダの無関係な `<pre>` を拾わない。
- `parse_rss`: RSS 1.0(RDF + dc:date) / RSS 2.0(pubDate) 両対応、`limit` 反映、不正 XML で例外。

### 3.3 クライアント結合（test_client）
- `read_page`: GET クエリに EUC-JP %エンコードのページ名と `cmd=source` を含むこと、本文往復一致。
- `search_pages`: `type=OR` 指定・検索語の EUC-JP エンコードを検証。
- 書込分岐:

  | ケース | 応答 | 期待 |
  | --- | --- | --- |
  | 新規作成 | 302 | `created`。POST body に EUC-JP 内容 / digest / `encode_hint=ぷ` / `write=1` |
  | 既存更新 | 302 | `updated` |
  | 衝突 | 200 + 編集フォーム | `PukiWikiConflictError` |
  | READONLY | 200 + マーカー | `PukiWikiReadOnlyError` |
  | 凍結/編集禁止(非空) | 200 平文 | `PukiWikiError`（誤って成功にしない） |
  | 削除(空内容) | 200 平文 | 成功 |
  | 追記 | 302 | `appended`、body に `add=true` |
  | 存在しないページ削除 | not found | `PageNotFoundError` |
  | HTTP 500 | 500 | `PukiWikiError` |

## 4. テスト中に検出した不具合

| ID | 重大度 | 事象 | 原因 | 対応 | 状態 |
| --- | --- | --- | --- | --- | --- |
| R-11 | 高 | `get_recent_changes` が EUC-JP の RSS で例外（`ValueError: multi-byte encoding`） | `ElementTree`(expat) が `encoding="EUC-JP"` 宣言を解釈できず、`str` への encoding 宣言も拒否する | クライアントが wiki エンコーディングで decode した `str` を `parse_rss` に渡し、`parse_rss` 側で XML 宣言を除去してから `fromstring` するよう修正。`_get_bytes` は廃止 | ✅ 修正・再テストで解消 |

## 5. 実行手順・結果

```bash
cd pukiwiki-mcp
uv run pytest -q
# => 38 passed
```

```
......................................                                   [100%]
38 passed in 0.07s
```

## 6. 受入基準の充足状況（要件 §8）

| AC | 内容 | 検証手段 | 判定 |
| --- | --- | --- | --- |
| AC-1 | `PUKIWIKI_URL` のみで起動・ツール列挙 | サーバ import スモーク（9 ツール登録）+ `test_config` | ✅ |
| AC-2 | 日本語ページの作成→読取で内容一致 | `test_read_page_roundtrip_japanese` / `test_create_new_page_*`（EUC-JP 往復） | ✅ |
| AC-3 | 更新が反映・差分取得 | `test_update_existing_page` / `test_get_diff` | ✅ |
| AC-4 | 未存在 read がクラッシュしない | `test_read_page_not_found` | ✅ |
| AC-5 | 検索ヒット一覧取得 | `test_search_pages_passes_type` | ✅ |
| AC-6 | 削除後に存在しない | `test_delete_page_success` / `test_delete_missing_page_raises` | ✅ |
| AC-7 | 設計どおりの単体テスト全通過 | `pytest`（38 passed） | ✅ |

## 7. 残課題・申し送り

- **実機結合テスト（E2E）は未実施**。本テストは MockTransport による疑似応答に基づく。
  実際の PukiWiki インスタンス（EUC-JP）へ接続した E2E は、環境準備後に別途実施を推奨。
  - 観点: 実 `cmd=list`/`cmd=rss`/`cmd=search` の HTML/RSS 実体に対する `_page_from_href` 妥当性、
    実際の 302 リダイレクト・衝突再現、長文/特殊文字の往復。
- スキン（`pukiwiki.skin.php`）が改変され `<div id="body">` 構造が変わると一覧/検索/差分の
  スコープ抽出が影響を受けるため、回帰テスト（`test_parsing`）の維持が重要。
- 添付・凍結操作・リネームは要件外（将来拡張時に対応テストを追加）。
