# 派工

派 subagent 時讀這份。界線（只寫 `.llm-wiki/extract/<名稱>/`、共用頁由主對話寫）見 `CLAUDE.md`「分工原則」；`<名稱>` 的定義見 `GLOSSARY.md`。模型以 `.claude/agents/*.md` 的 frontmatter 為準，和下表不一致時改下表。

## 分配表

| 步驟 | 誰做 | 模型 | 可寫入 |
|---|---|---|---|
| 找來源、下載、預檢 | 主對話；搜尋可派 `haiku-explore` | session 模型；搜尋用 haiku | `.llm-wiki/inbox/` |
| 單篇閱讀 | 主對話 | session 模型 | `.llm-wiki/extract/<名稱>/` |
| 批次閱讀（3 篇以上或全自動） | 閱讀 agent：`general-purpose`，每篇一個 | session 模型 | `.llm-wiki/extract/<名稱>/` |
| 寫 wiki 頁、主題頁、帳本、log、commit | 主對話 | session 模型 | `wiki/` |
| 回原文核對 | `source-verifier` | sonnet | 只有 `verify.md` |
| concept 頁讀懂檢查 | `concept-reader` | sonnet | 只有 `student.md` |
| 簡報語意審查（/wiki-slides） | `slide-reviewer` | sonnet | 只有 `review.tsv` |
| lint 語意檢查（超過 12 頁） | `lint-checker` | sonnet | 唯讀 |
| lint 修復的 raw 查證 | `general-purpose`，`model: sonnet` | sonnet | 不寫檔 |
| 改 `tools/` 後的檢查 | `sonnet-checker` | sonnet | 唯讀 |
| agy 任務（只在使用者要求時） | `agy-runner` | haiku；agy 用 Gemini | `.llm-wiki/extract/<名稱>/agy-*` 與快照 |
| 其他獨立子任務，一份說明就能做完 | `general-purpose`，`model: sonnet` | sonnet | `.llm-wiki/extract/<名稱>/` |
| 其他獨立子任務，需要主對話完整上下文 | fork | 同主對話 | `.llm-wiki/extract/<名稱>/` |

## 通用規則

適用於閱讀 agent、fork 與 `general-purpose`。這三種沒有 hook 擋寫入。

- 派工前：`uv run python -I tools/work.py snapshot <名稱>`
- 交回：建議清單，每項一行 `頁面｜新增或修改｜內容摘要｜證據位置`；最後列出自己寫了哪些檔案
- 收回：`work.py changed <名稱> --mine <主對話同期改的檔…> --allow .llm-wiki/extract/<名稱>/`。它比對整個 repo，不只 `wiki/`。`others` 裡的檔案逐一判斷：hook 與工具會更新 `.llm-wiki/memory/` 的快取，這類不算；agent 寫的就退回
- 套用前：每一類建議至少抽查 1 條，回原文核對；同一頁被多份建議提到時，一次讀、一次寫
- 它們不寫 `wiki/`、帳本、log，不 commit，不派 subagent

## 閱讀 agent

- prompt 只給：raw 路徑、目標 source 頁路徑與 domain、`manifest.tsv` 版本、同批其他篇與可能共用的 concept 頁。規則讓它讀 `docs/batch-reader.md`。不附主對話的判斷
- 同時最多 2 個；不先試跑
- 主對話不重讀全文，但結論依賴的圖要親自看。之後照常做 ingest 第 8 步核對

## fork

只用在需要主對話完整上下文的子任務。圖片只截到 `.llm-wiki/extract/<名稱>/`，挑圖與複製到 `wiki/assets/` 由主對話做。

## source-verifier

- 一律用 `subagent_type: source-verifier`，不用 fork，以免帶著主對話的判斷核對
- prompt 只給：頁面路徑（source 頁與本次改到的 concept 頁）、raw 路徑、輸出 `.llm-wiki/extract/<名稱>/verify.md`，並加「做漏讀檢查」
- 驗收：`doc.py quote --from <verify.md> --source <raw>` 通過 → ⚠️、❌ 全部回原文確認 → ✅ 抽查 2 條或 20%。quote 不過就退回，最多 2 次，之後主對話自己核對

## concept-reader

- ingest 的核對修正做完後，每個改到的 concept 頁派一個
- 分兩次派。第一次給頁面路徑、輸出 `.llm-wiki/extract/<名稱>/student.md`、停止的標題（通常是「機制」；沒有這節時，寫第一個談失效或變體的標題的前一節），並寫「只做到預測就交回」。確認 `student.md` 只有「## 預測」後，用 SendMessage 請它讀完並回答五題
- 依回饋修改，最多來回 2 次；還卡住的地方列成「缺」，由使用者決定要不要補來源
- 修改寫入 `wiki/` 前，再派一次 `source-verifier` 核對改過的句子，輸出到 `.llm-wiki/extract/<名稱>-r2/verify.md`

## slide-reviewer

- `/wiki-slides` 的 `check` 通過後派一個；不用 fork，以免帶著寫大綱時的判斷
- 派工前主對話先跑 `slides.py pages <outline> --out .llm-wiki/extract/<名稱>/pages/`（它沒有 Bash，只能看已經產生的影像）
- prompt 只給：大綱路徑、影像資料夾、輸出 `.llm-wiki/extract/<名稱>/review.tsv`
- 驗收：每頁都有一列；`看過的頁` 有寫影像檔名的列抽查 2 列，確認影像存在、內容對得上。統計由主對話做，不採用它自己的統計

## lint-checker

- 主對話先跑 `uv run python -I tools/lint.py > .llm-wiki/lint-before.json`（它只有 Read、Grep、Glob）
- prompt 給 JSON 路徑與負責的頁面清單，依主題頁分組，每個最多 12 頁，同時最多 2 個
- 主對話抽查回報後照抄進報告

## lint 修復查證

- 用在 `/wiki-lint` 修復時，報告標「未核對」的項目，一組一個 `general-purpose`（`model: sonnet`）
- prompt 只給：報告裡這組待辦的原文、各項依據的 raw 路徑，並寫「不寫任何檔案，也不派 subagent」
- 交回每項一行：`<檔案>:<行號>｜原句「…」→ 改成「…」｜證據 <raw 路徑> 頁碼或行號｜成立／不成立`
- 收回時用 `git status` 確認 `wiki/` 沒被改；套用前每一類至少抽查 1 條
