# PukiWiki MCP サーバー レビュー報告書

| 項目 | 内容 |
| --- | --- |
| ドキュメントID | 05_review |
| バージョン | 1.0 |
| 作成日 | 2026-06-16 |
| フェーズ | レビュー |
| 対象 | Phase 4 実装一式 |
| レビュー観点 | 設計整合性 / PukiWiki 実挙動整合 / 文字コード / 堅牢性 / 保守性 |

---

## 1. レビュー方法

- 実装コードを詳細設計（03）と PukiWiki 実コード（`plugin/edit.inc.php`, `source.inc.php`,
  `list.inc.php`, `diff.inc.php`, `lib/init.php`, `skin/pukiwiki.skin.php`）に突き合わせて精査。
- 文字コード往復・パース・書込分岐は実 HTML 断片を投入して挙動確認。

## 2. 指摘事項と対応

### 重大度: 高

| ID | 指摘 | 詳細 | 対応 | 状態 |
| --- | --- | --- | --- | --- |
| R-2 | 一覧/検索でスキンのナビゲーションリンクが混入 | `cmd=list`/`cmd=search` のレスポンスはスキン全体を含み、`<div id="menubar">` の MenuBar/RecentChanges 等の `index.php?Page` リンクも `parse_page_list` が拾ってしまう | `extract_body_html()` を新設し、`<div id="body">`（コンテンツ領域）内に限定してアンカー抽出。`#body` 不在のスキンでは全体にフォールバック | ✅ 修正済 |
| R-7 | 凍結ページ等への書込を「成功」と誤判定 | 非空書込が失敗（凍結/編集禁止の `die_message`）した場合 200 が返るが、衝突でも READONLY マーカーでもないため成功扱いになっていた | 「非空書込の成功は必ず 302」というPukiWiki の挙動に基づき、非空 content で 200 が返ったら（衝突/READONLY 以外でも）`PukiWikiError` を送出。空 content（削除）の 200 のみ成功とする | ✅ 修正済 |

### 重大度: 中

| ID | 指摘 | 詳細 | 対応 | 状態 |
| --- | --- | --- | --- | --- |
| D-1（再掲） | `page=` 形式の EUC-JP ページ名が文字化け | `urllib.parse_qsl` が %xx を UTF-8 で decode し EUC-JP バイトを破壊 | 生クエリを自前分割し `unquote_to_bytes`→`decode(encoding)` で復元（Phase 4 で修正済） | ✅ 修正済 |
| R-3 | `diff` のパースが文書先頭の無関係な `<pre>` を拾う恐れ | スキンヘッダ等に `<pre>` があると先頭一致で誤取得 | `parse_diff_html` も `extract_body_html` で `#body` に限定してから先頭 `<pre>` を取得 | ✅ 修正済 |

### 重大度: 低（確認のみ・対応不要）

| ID | 観点 | 確認結果 |
| --- | --- | --- |
| R-4 | 書込成功判定（302） | PukiWiki は `header('Location: ...'); exit;` で 302 を返す。`follow_redirects=False` で正しく捕捉 | OK |
| R-5 | 新規ページの digest | 非存在ページの編集フォームも `md5("")` を埋め込み、`extract_digest` がそのまま使用可能。特別扱い不要 | OK |
| R-6 | READONLY/SAFE_MODE 検出 | `die_message('PKWK_READONLY ...')` の文言を `looks_readonly` が検出 | OK |
| R-8 | 削除の判定 | 空書込の応答にはテキストエリアが無く `is_conflict_page=False`、`content==""` で成功判定 | OK |
| R-9 | ログ出力先 | stdio の JSON-RPC は stdout 占有のため、ログは `basicConfig` で stderr 出力 | OK |
| R-10 | 衝突検出 | digest 不一致時は自動マージ可能でも編集フォーム（`<textarea name="msg">`）を再表示。テキストエリア有無で衝突判定可能 | OK |

## 3. 設計・要件トレーサビリティ確認

| 要件/設計 | 実装 | 判定 |
| --- | --- | --- |
| FR-01〜09（9 ツール） | `server.py` に 9 ツール定義、`list_tools()` が 9 件を返却 | ✅ |
| NFR-01（環境変数化） | `Settings.from_env` で全パラメータを env から取得 | ✅ |
| NFR-02（文字コード境界） | `_encode_pairs`/`decode`/`encode_hint`、ページ名は bytes 経由 decode | ✅ |
| NFR-03（堅牢性） | 既知例外を dict 化、プロセス非停止 | ✅ |
| NFR-04（副作用分離） | 読取ツールは GET のみ、書込ツールに破壊操作を限定 | ✅ |
| NFR-05（楽観ロック） | `write_page` が digest を取得し POST、衝突を検出 | ✅ |
| NFR-06/07/08（保守性/テスト容易性/依存最小） | 層分離・純粋関数・標準ライブラリパース | ✅ |

## 4. コード品質所見

- 良い点
  - パースが純粋関数として独立し、`#body` スコープ化でスキン依存を最小化。
  - エンコーディング処理が境界（`_encode_pairs`/decode）に集約。
  - 書込の成功条件を PukiWiki の実挙動（302）に厳密化し、誤検知リスクを排除。
- 改善余地（将来）
  - HTML 依存部（list/search）は API 不在ゆえ不可避。スキン変更時の回帰は Phase 6 のテストで検知する方針。
  - `get_recent_changes` は RSS フォーマット依存。フォーマット変更時の追従が必要。

## 5. レビュー結論

重大度「高」2 件・「中」2 件をすべて修正し、再スモークでサーバ起動・9 ツール登録・
パース挙動（body スコープ、差分、文字コード往復）を確認した。
**Phase 6（テスト）へ進行可**と判断する。
