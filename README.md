# PukiWiki MCP Server

PukiWiki サイトを **MCP (Model Context Protocol)** のツールとして公開する Python サーバーです。
Claude などの MCP クライアントから、PukiWiki のページを閲覧・検索・作成・更新・削除できます。

- **Wiki 側に何も設置しません**。PukiWiki の通常の画面（HTTP）を使うので、PHP のファイルやプラグインの追加は不要です
- 接続先は環境変数で指定するだけ（必須は `PUKIWIKI_URL` のみ）
- EUC-JP / UTF-8 のどちらのサイトにも対応（送受信の境界で自動変換）
- 依存は `mcp` と `httpx` だけ（HTML / XML の解析は標準ライブラリ）

検証状況：本物の PukiWiki で確かめたのは **PukiWiki 1.5.4（EUC-JP）** です。

## 公開ツール

| ツール | 種別 | 説明 |
| --- | --- | --- |
| `list_pages` | 読 | 全ページ名の一覧 |
| `read_page` | 読 | ページのソース取得 |
| `page_exists` | 読 | ページの存在確認 |
| `search_pages` | 読 | 全文検索（AND/OR） |
| `get_page_diff` | 読 | 最新差分の取得 |
| `get_recent_changes` | 読 | 最近の更新一覧（RSS 由来） |
| `create_or_update_page` | 書 | ページ作成 / 全文上書き |
| `append_to_page` | 書 | 末尾（または先頭）追記 |
| `delete_page` | 書 | ページ削除 |

書き込み系のツールは、Wiki の内容を実際に変更します。AI に渡すときは、実行の前に内容を確認する設定にしてください。

**既知の制限**：書き込みは、送る直前にページの版（digest）を取り直して送ります。そのため、`read_page` で読んでから書き込むまでの間に人が編集すると、その編集は上書きされます（検知できるのは、ほぼ同時の書き込みだけです）。

## 使い方（uv）

[uv](https://docs.astral.sh/uv/) があれば、インストールせずに起動できます。

```bash
PUKIWIKI_URL=https://wiki.example.com/ uvx --from git+https://github.com/epochnet/pukiwiki-mcp pukiwiki-mcp
```

### Claude Desktop / Claude Code への登録例

```json
{
  "mcpServers": {
    "pukiwiki": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/epochnet/pukiwiki-mcp", "pukiwiki-mcp"],
      "env": {
        "PUKIWIKI_URL": "https://wiki.example.com/",
        "PUKIWIKI_ENCODING": "euc-jp"
      }
    }
  }
}
```

Claude Code ならコマンドでも登録できます。

```bash
claude mcp add pukiwiki \
  -e PUKIWIKI_URL=https://wiki.example.com/ -e PUKIWIKI_ENCODING=euc-jp \
  -- uvx --from git+https://github.com/epochnet/pukiwiki-mcp pukiwiki-mcp
```

### 手元のリポジトリから動かす

```bash
git clone https://github.com/epochnet/pukiwiki-mcp.git
cd pukiwiki-mcp
uv sync                                              # .venv を作って依存を入れる
PUKIWIKI_URL=https://wiki.example.com/ uv run pukiwiki-mcp
```

## 環境変数

`.env.example` を参照。必須は `PUKIWIKI_URL` のみです。

| 変数 | 必須 | 既定 | 説明 |
| --- | --- | --- | --- |
| `PUKIWIKI_URL` | ✅ | — | ベース URL（例 `https://wiki.example.com/`） |
| `PUKIWIKI_ENCODING` | | `euc-jp` | Wiki の内部エンコーディング（`euc-jp` / `utf-8`） |
| `PUKIWIKI_TIMEOUT` | | `30` | HTTP タイムアウト秒 |
| `PUKIWIKI_VERIFY_TLS` | | `true` | TLS 証明書検証 |
| `PUKIWIKI_USER_AGENT` | | `pukiwiki-mcp/<ver>` | User-Agent |
| `PUKIWIKI_LOG_LEVEL` | | `INFO` | ログレベル（stderr 出力） |
| `PUKIWIKI_BASIC_USER` / `PUKIWIKI_BASIC_PASSWORD` | | （空） | Basic 認証（既定なし） |
| `PUKIWIKI_MCP_TRANSPORT` | | `stdio` | MCP トランスポート（`stdio` / `streamable-http` / `sse`） |
| `PUKIWIKI_MCP_HOST` | | `127.0.0.1` | HTTP トランスポート時の待ち受けアドレス |
| `PUKIWIKI_MCP_PORT` | | `8000` | HTTP トランスポート時のポート |

## HTTP で待ち受ける場合の注意

`streamable-http` / `sse` には**認証がありません**。待ち受けると、そのアドレスに届く人は誰でも Wiki を読み書きできます。

- 既定の待ち受けは `127.0.0.1`（この PC の中からだけ）です
- ほかのマシンから使う場合は、認証のあるリバースプロキシや VPN の内側に置いてください。`PUKIWIKI_MCP_HOST=0.0.0.0` でそのまま外に公開しないでください

## Docker で起動

### A. HTTP サービスとして起動（`docker compose up`）

```bash
cp .env.example .env       # PUKIWIKI_URL を編集
docker compose up --build  # http://127.0.0.1:8000/mcp で待受
```

- コンテナの中では `0.0.0.0` で待ち受け、ホスト側には `127.0.0.1:8000` だけで公開します（`compose.yml`）
- 接続設定は `.env` から読み込みます。`.env` が無い場合は、`PUKIWIKI_URL` を環境変数で渡してください

HTTP トランスポート対応の MCP クライアントからは `http://127.0.0.1:8000/mcp` を指定します。

### B. stdio として起動（`docker run -i`）

```bash
docker build -t pukiwiki-mcp .
docker run -i --rm \
  -e PUKIWIKI_MCP_TRANSPORT=stdio \
  -e PUKIWIKI_URL=https://wiki.example.com/ \
  -e PUKIWIKI_ENCODING=euc-jp \
  pukiwiki-mcp
```

> イメージの既定のトランスポートは `streamable-http`（compose 用）です。stdio で使うときは上のように上書きします。

## 開発

```bash
uv sync          # 開発用の依存（pytest）も入る
uv run pytest
```

設計の資料は [docs/](docs/) にあります（要件定義・基本設計・詳細設計・開発・レビュー・テスト）。

## ライセンス

MIT（[LICENSE](LICENSE)）。Copyright (c) 2026 株式会社EPOCH-NET
