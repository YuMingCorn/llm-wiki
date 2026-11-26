# LLM Wiki

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

讓 LLM 維護的個人知識庫模板。你放進論文、技術報告與文章，[Claude Code](https://docs.claude.com/en/docs/claude-code) 把它們整理成互相連結、附頁碼出處的 Markdown 頁面，供你查詢、學習與寫作時參考。

這個 repo 是空白的。它只有規則、Claude Code 的 skill、agent、hook，以及 Python 工具和測試，沒有內容頁，也沒有預設領域。

## 特色

- **每個論點都有出處。** 來源頁的論點附頁碼、表號或式號；跨來源的結論標成「推論」，沒有證據的標成「待查證」。
- **寫完會回原文核對。** 獨立的 agent 拿頁面回原文逐條比對，附逐字證據，也找出漏讀的重點。
- **規則由程式把關。** hook 擋下含疑似密鑰的寫入，並在寫入後回報格式錯誤與壞連結；`tools/lint.py` 檢查整個 wiki。
- **概念頁為讀者而寫。** 概念頁依「要解決的問題」組織，另有 agent 扮演初學者，回報哪裡讀不懂。
- **純 Markdown。** 使用相對路徑連結，GitHub、Obsidian 和一般編輯器都能直接開啟。

## 快速開始

需要：

- [uv](https://docs.astral.sh/uv/)（Python 3.12 以上）
- [Node.js](https://nodejs.org/)（執行 hook）
- [Claude Code](https://docs.claude.com/en/docs/claude-code)

```bash
git clone https://github.com/YuMingCorn/llm-wiki.git
cd llm-wiki
uv sync
uv run pytest -q
```

接著：

1. **填寫 [`PURPOSE.md`](PURPOSE.md)。** 把每個「（填寫）」換成你的目標、核心問題和收錄範圍。LLM 每次匯入前都會讀它，判斷一份來源值不值得記。
2. **建立第一個領域。** 見下方〈[建立領域](#建立領域)〉。
3. **匯入第一份來源。** 把 PDF 放進 `raw/sources/`，在 repo 根目錄啟動 Claude Code，執行 `/wiki-ingest`。之後每次 session 開始，hook 都會列出還沒匯入的來源。

## 匯入流程

`/wiki-ingest` 處理一份來源的步驟如下。詳細規則見 [`.claude/skills/wiki-ingest/SKILL.md`](.claude/skills/wiki-ingest/SKILL.md)。

```
/wiki-ingest
├─ 1. 取得來源      查來源帳本，避免重複匯入
├─ 2. 閱讀          讀完原文，寫成抽取單（暫存，不進 wiki）
├─ 3. 確認點        列出要新增與修改的頁面，等你確認
├─ 4. 寫來源頁      論點附頁碼，公式用 LaTeX 照原文轉錄
├─ 5. 改概念頁      改寫既有論點，而不是在結尾追加
├─ 6. 處理矛盾      先查一手來源；查不到才交給你判斷
├─ 7. 更新導覽      把新頁加進主題頁
├─ 8. 核對          source-verifier 回原文核對；concept-reader 檢查可讀性
├─ 9. 機械檢查      tools/lint.py
└─ 10. 收尾         登記帳本、寫操作紀錄、commit（不 push）
```

## 建立領域

領域是 `wiki/` 底下的一個目錄，名稱用小寫 kebab-case，例如 `music` 或 `world-history`。`cross` 保留給跨領域的主題。工具會自動偵測 `wiki/` 底下的領域，不必修改程式。

以 `music` 為例：

1. 建立 `wiki/music/index.md`：

   ```markdown
   ---
   title: Music Index
   type: index
   domain: music
   tags: [llm-wiki]
   updated: YYYY-MM-DD
   ---

   # Music

   > 本領域的主題目錄。主題頁列出成員與一句話摘要，規則見 schema §2、§8。回到[總目錄](../index.md)。
   ```

2. 在 `wiki/index.md` 的「領域」一節加上：`- [Music](music/index.md) — 一句話說明`
3. 在 `PURPOSE.md` 的「領域與審閱檢查表」列出這個領域，並寫下審閱它的來源時要檢查的事。
4. 執行 `uv run python -I tools/wiki.py index-check`，確認導覽正確。

`sources/`、`concepts/` 等子目錄會在第一次匯入時建立。主題頁等有內容後再建，規則見 [`SCHEMA.md`](SCHEMA.md) §2、§8。

## 架構

| 層 | 路徑 | 誰寫 | 說明 |
|---|---|---|---|
| 原始資料 | `raw/sources/` | 你 | LLM 只讀不改。不進 git |
| Wiki | `wiki/` | LLM | 所有知識頁 |
| 規則 | `SCHEMA.md`、`PURPOSE.md`、`GLOSSARY.md`、`CLAUDE.md`、`docs/`、`.claude/` | 你與 LLM | 修改規則要記進 `wiki/log.md` |
| 個人筆記 | `hackmd_note/` | 你 | 選用，HackMD 筆記的本機鏡像。只有 README 進 git |

資訊只從 wiki 流向個人筆記。筆記的內容要進 wiki，必須提供原始來源，重新走一次 `/wiki-ingest`。

導覽分四層，每一層只列下一層：

```
wiki/index.md                 總目錄：列出領域
└─ wiki/<領域>/index.md       領域目錄：列出主題
   └─ topics/<主題>.md        主題頁：列出成員與建議閱讀順序
      └─ 內容頁               來源、概念、實體、綜合、對比、問答
```

| 頁面類型 | 目錄 | 用途 |
|---|---|---|
| source | `sources/` | 一份著作一頁：問題、做法、公式、結果、限制，附頁碼 |
| concept | `concepts/` | 跨來源整理一個理論、方法或術語 |
| entity | `entities/` | 人物、組織、產品、資料集 |
| synthesis | `synthesis/` | 跨多份來源的分析 |
| comparison | `comparisons/` | 以表格並排比較 |
| query | `queries/` | 值得保存的問答 |
| topic | `topics/` | 導覽頁，不寫論點 |

frontmatter 欄位、`status` 的五種值與拆頁門檻，見 [`SCHEMA.md`](SCHEMA.md)。

## 寫作規則

- **出處。** 每個論點都附出處。不捏造引句、頁碼或日期。
- **矛盾。** 來源說法不一致時，相關頁面標成 `disputed`，內文分別寫出各方說法與條件，不硬下結論。
- **取代。** 新來源更正舊說法時，舊說法移到「已被取代的說法」一節，保留日期與出處。
- **概念頁。** 讀完要能說出：它解決什麼問題、核心想法、機制如何推導、何時失效，以及前置、替代與後繼方法。
- **專有名詞。** 方法、模型與指標保留原文；常見的中文說法寫進 `aliases`，供搜尋使用。
- **信心分數。** 由 [`tools/confidence.py`](tools/confidence.py) 依頁面標記計算，不手動填寫。規則與設計理由見 [SCHEMA.md](SCHEMA.md) §9「信心分數」。

## 指令

指令是定義在 [`.claude/skills/`](.claude/skills/) 的 Claude Code skill，也可以用自然語言觸發，例如「ingest …」、「問：…」、「lint wiki」。

| 指令 | 用途 | 寫入 wiki |
|---|---|---|
| [`/wiki-ingest`](.claude/skills/wiki-ingest/SKILL.md) | 把 PDF 或網頁快照整理成來源頁，並更新相關概念頁 | 會，先經你確認 |
| [`/wiki-query`](.claude/skills/wiki-query/SKILL.md) | 用 wiki 回答問題並附上引用 | 你同意才保存 |
| [`/wiki-study`](.claude/skills/wiki-study/SKILL.md) | 產生知識地圖、三個根本爭議與 10 題測驗 | 你同意才保存 |
| [`/wiki-save`](.claude/skills/wiki-save/SKILL.md) | 把對話中的判斷存成綜合、對比或問答頁 | 會，先經你確認 |
| [`/wiki-lint`](.claude/skills/wiki-lint/SKILL.md) | 檢查壞連結、孤兒頁、矛盾與過時內容 | 不會；修復另外進行 |
| [`/wiki-slides`](.claude/skills/wiki-slides/SKILL.md) | 以一個概念為中心產生 PowerPoint 簡報 | 不會；大綱存在 `slides/` |

會寫入的指令結束時自動 commit，但不會 push。

## Agent 與 hook

主對話負責寫入 wiki 與 commit。[`.claude/agents/`](.claude/agents/) 裡的 subagent 只做檢查，只能寫自己的暫存目錄 `.llm-wiki/extract/<名稱>/`。分工見 [`docs/dispatch.md`](docs/dispatch.md)。

| Agent | 用途 |
|---|---|
| [`source-verifier`](.claude/agents/source-verifier.md) | 拿頁面回原文逐條核對，附逐字證據，並找出漏讀的重點 |
| [`concept-reader`](.claude/agents/concept-reader.md) | 扮演只讀過 wiki 的學生，回報概念頁哪裡讀不懂 |
| [`lint-checker`](.claude/agents/lint-checker.md) | 語意檢查：矛盾、過時內容、該拆的頁面 |
| [`slide-reviewer`](.claude/agents/slide-reviewer.md) | 審查簡報大綱：每頁的主張是不是完整的一句話、出處與原頁影像是否支持它 |
| [`agy-runner`](.claude/agents/agy-runner.md) | 選用：把任務交給外部的 agy（Gemini）執行 |

hook 設定在 [`.claude/settings.json`](.claude/settings.json)：

| 時機 | 動作 |
|---|---|
| session 開始 | 列出新來源、被修改的原始檔、到期的 lint 與過時頁 |
| 執行 Bash 前 | 要求用 `uv run python` 執行 Python，用專案工具寫操作紀錄 |
| 寫入 `wiki/` 前 | 擋下疑似 API token 或私鑰 |
| 寫入 `wiki/` 後 | 檢查該頁的 frontmatter、頁面關係與連結，把問題回報給 agent |
| session 結束 | 把這次的 commit 與未 commit 的檔案寫成摘要（只在本機） |
| subagent 執行時 | 限制核對用 agent 能讀寫的檔案與能跑的指令 |

## 工具

重複的操作都由 [`tools/`](tools/) 的 Python 工具處理，用法見 [`docs/tools.md`](docs/tools.md)。

| 工具 | 用途 |
|---|---|
| `wiki.py` | 寫操作紀錄、commit（先掃描敏感資料）、檢查導覽 |
| `lint.py` | 機械檢查：frontmatter、壞連結與錨點、孤兒頁、別名 |
| `search.py` | 混合搜尋：BM25 與頁面連結圖；`uv sync --group search` 後加上本機向量搜尋 |
| `doc.py`、`pdf_tool.py`、`web_fetch.py` | 讀取 PDF 與網頁、擷取引句、截圖 |
| `ledger.py` | 來源帳本：記錄每份原始檔的 SHA-256 與對應的來源頁 |
| `slides.py` | 把簡報大綱轉成 `.pptx` |

## 不進 git 的檔案

| 路徑 | 內容 |
|---|---|
| `raw/` | 原始 PDF 與網頁快照（著作權與檔案大小因素，請自行備份） |
| `hackmd_note/` 的筆記 | HackMD 筆記的本機鏡像 |
| `.mcp.json` | MCP 設定，可能含 API token |
| `.claude/settings.local.json` | 本機權限設定 |
| `.llm-wiki/` | 抽取單、核對結果、lint 報告等中間產物 |

## 選用功能

- **HackMD 同步**：設定 HackMD MCP server（寫在 `.mcp.json`）後，可以把筆記同步到 `hackmd_note/`。見 [`docs/hackmd.md`](docs/hackmd.md)。
- **向量搜尋**：`uv sync --group search` 安裝本機 embedding 模型。沒有安裝時，搜尋只用 BM25 與連結圖。

## 限制

- 頁面由 LLM 撰寫。即使經過核對，仍可能有錯誤，引用前請查證原文。
- 規則文件與指令目前以繁體中文撰寫。
- 目前只在 Windows 10 上測試過。

## 文件

- [`PURPOSE.md`](PURPOSE.md)：這個 wiki 收什麼、不收什麼（需自行填寫）
- [`SCHEMA.md`](SCHEMA.md)：頁面類型、frontmatter、連結與圖片、commit 格式、來源帳本
- [`GLOSSARY.md`](GLOSSARY.md)：用語
- [`CLAUDE.md`](CLAUDE.md)：給 Claude Code 的工作指引
- [`docs/`](docs/)：分工、HackMD 同步、工具用法、開發規則、決策紀錄

## 貢獻

歡迎提出 issue 與 pull request。修改 `tools/` 前請先讀 [`docs/development.md`](docs/development.md)，並確認 `uv run pytest -q` 通過。

## 授權

[MIT](LICENSE)。你用這個模板建立的 wiki 內容，授權由你自行決定。
