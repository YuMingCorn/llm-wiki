---
name: wiki-lint
description: 檢查 wiki 健康狀況，產出審查報告與待辦清單，修復另外進行。使用者說「lint wiki」或 /wiki-lint 時使用。
---

# /wiki-lint

讀 `SCHEMA.md` 後執行審查，結果寫入 `.llm-wiki/review/YYYY-MM-DD-lint.md`（同一天已有報告時依序用 `-2`、`-3` 結尾，不覆蓋）。**除報告與 `wiki/overview.md` 索引同步外，不直接修改任何知識頁面，所有修正須經使用者同意。**

1. **機械檢查**：`uv run python -I tools/lint.py > .llm-wiki/lint-before.json`，輸出 JSON：壞連結、標題錨點（`broken_anchors`；`slug_only` 表示 GitHub 可用但 Obsidian 可能跳不到）、重複檔名、連到隱藏目錄的連結、孤兒頁（沒有任何**內容頁**連到它；主題頁與 `index.md`、`overview.md`、`log.md` 的連結不算；主題只有一份 source 頁時，那頁改列在 `single_source_topics`，不算孤兒）、沒有導覽歸屬的頁（`index_missing`：內容頁不在任何主題頁，或主題頁不在 index；橫跨多主題的 synthesis 頁可以只列在 index）、空小節（`empty_sections`）、內文用了某頁的中文別名（`alias_in_prose`，候選清單，不算 `--strict` 違規）與同一別名屬於兩頁（`alias_conflicts`）、缺 frontmatter、source ↔ concept 雙向對應、內文連到 source 頁但 frontmatter `sources` 沒列（`sources_missing`）與列了但內文沒連（`sources_unused`，候選清單，不算 `--strict` 違規；新來源沒改論點時只列 `sources` 是允許的）、來源帳本違規與過期來源（`ledgers`，schema §9）、disputed／partial 清單、舊 status 待轉換清單（`legacy_status`）、頁數門檻（schema §8）、未 push commit 數、上次 lint 後的 ingest 次數、異動檔案清單（`changed_since_last_lint`）。需要逐頁看 frontmatter 時用 `--frontmatter`（TSV，一頁一行）。另外跑 `uv run python -I tools/wiki.py log-check` 與 `uv run python -I tools/wiki.py index-check`（log 與 index 的規則檢查），違規列進報告，提醒附在後面
   - 腳本執行失敗就停下來修腳本；**禁止**由 LLM 全量讀檔手動比對雙向連結
   - 保留腳本輸出的路徑、行號、類別與數量，只回報輸出裡有的檢查。孤兒頁可能是刻意的，不要只憑結果推斷原因
   - 依影響分組：導覽失效（壞連結、錨點）→ 目標模糊（重複檔名）→ 中繼資料與帳本 → 維護性（空小節、導覽歸屬遺漏）
   - 暫存頁可用 `--exclude "<glob>"` 排除；需要以 exit code 判斷時加 `--strict`
   - 使用紀錄（每次都跑，跳過語意檢查時也跑）：`uv run python -I tools/questions.py report --since <日期>`，日期取 `wiki/log.md` 最後一筆 `lint` 紀錄的日期；沒有上一次 lint 時不加 `--since`。把題數、缺口分類與各清單照抄進報告的「使用紀錄」：缺來源的問題建議寫進 `MISSING_SOURCES.md`，頁面難懂的頁建議改寫，搜尋沒找到的 TSV 行建議加進 `tools/search_eval.tsv`。這些都是建議，使用者同意才做
2. **語意檢查**：範圍頁是 `wiki/` 底下除了 `log.md`、`meta/`、`assets/` 以外的頁（主題頁、index、overview 都算），分三種：
   - (a) 腳本標出、要讀頁判斷的項目所在的頁：`broken_links`、`broken_anchors`、`orphans`、`index_missing`、`sources_missing`、`sources_unused`、`relation_problems`、`supersession_problems`、`stale_pages`、`backlink_mismatches`、`frontmatter_errors`。最新一份 `.llm-wiki/review/*-lint*.md` 已判定是刻意、而且頁面不在 (b) 的項目不算。`disputed`、`legacy_status`、`partial_sources`、`alias_in_prose` 是常駐清單，只有頁面也在 (b) 或 (c) 時才看
   - (b) `changed_since_last_lint` 裡被 ingest 或一般編輯改到的頁（若無歷史 lint 紀錄則取前 15 篇核心頁）。用 `git log <since>..HEAD --format=%s --name-only -- wiki/`（`<since>` 取 JSON 的 `changed_since_last_lint.since`）分辨每頁是被哪些 commit 改的，只取同時在 `changed_since_last_lint.files` 裡的頁；`since` 是 null 時不跑。也被修復 commit 改過的頁仍算 (b)，整頁讀
   - (c) 只被 `edit 依第 N 次 lint 修正` 這類修復 commit 改到的頁：主對話只看 `git diff <since> -- <頁>` 的修改處是否寫對，不整頁讀，也不派子代理
   - 一頁同時屬於多類時，依 (a)、(b)、(c) 的順序取前面那一類
   - (a)、(b)、(c) 都沒有頁時跳過語意檢查，報告寫「沿用第 N 次」，只列仍未修的待辦編號。全 wiki 掃描只在使用者要求時做
   - 上次報告的舊待辦：頁面和它的依據頁都不在 `changed_since_last_lint` 時只引用編號，不重讀
   - (a) 加 (b) 在 12 頁以內由主對話自己做。超過 12 頁時派唯讀的 `lint-checker`，每個最多 12 頁、同時最多 2 個，超過 24 頁就分波派。分組依主題頁；一個主題超過 12 頁再依子主題或頁面類型切；不屬於任何主題的頁合成一組。prompt 給頁面清單和第 1 步 JSON 的路徑，見 `docs/dispatch.md`
   - 子代理回報的最後一行是計數行；沒有計數行，或標了「未完成」，就把沒查的頁重新派一次或由主對話自己讀。回報的每一類至少抽查 1 條，再照抄進報告
   - 檢查（`lint-checker` 的檢查項目包含以下全部）：
     - 論點矛盾或過時；頁面標了矛盾（`status: disputed`）或「待查證」的論點是否仍成立
     - 被提及但尚未建立頁面的概念（Missing Concepts）
     - 僅有 secondary／tertiary 來源支撐的薄弱論點
     - schema §2 拆頁／合併建議
     - schema §4 專有名詞：有頁面的術語由 `alias_in_prose` 機械列出，這裡逐條判斷是不是一般用語；語意檢查只另外列「沒有頁面、但明顯是專名」的中文翻譯，以及新 concept／entity 頁漏寫 `aliases`，不逐詞詢問
3. **索引同步**：更新 `wiki/overview.md` 以反映最新結構
4. **撰寫報告**：照 `assets/report.md` 的骨架寫入上述報告路徑，機械檢查的事實和語意判斷、修復建議分開寫。語意待辦依分類連續編號，每項一行，格式和 `lint-checker` 的回報相同：`<檔案>:<行號>｜<問題類型>｜原句「<頁面原文，最多 60 字>」→ 建議改成「<改寫後的句子>」｜依據 <路徑>:<行號>｜已核對／未核對／待確認`。這樣修復時不必重新調查。同一類問題反覆出現時，指出產生它的流程；流程或 `tools/` 的改進提案寫在「現行試行機制評估」，另開對話做，不排進修復
5. **收尾**：`log lint "第 N 次"`（內文最多 1 行：報告路徑），`commit "docs(wiki): lint 第 N 次" wiki/overview.md wiki/log.md`（語法見 `CLAUDE.md`；報告在 `.llm-wiki/`，不進 git）。在對話中僅列出高優先級待辦，等待使用者指示

## 修復（另一個操作，使用者選定項目後才做）

修復的成本約等於「呼叫次數 × 當下對話長度」，所以以一組（報告的一類，約 10 項以內）為單位，減少輪數。

1. 從報告讀選定的組；使用者要逐條決定時，把這組的問題一次列出來問，決定完再一起執行。刪頁或併頁要使用者明確同意
2. 分流：
   - 「已核對」且寫了確切改法的項目：主對話直接改
   - 「未核對」且依據只在 wiki 頁：主對話讀依據那幾行後改
   - 「未核對」且要回 raw 查證：派 `general-purpose`（`model: sonnet`）查，一組一個，派法見 `docs/dispatch.md`「lint 修復查證」。主對話每一類至少抽查 1 條後套用
   - 「待確認」或沒有確切改法：主對話讀依據後提出改法，問使用者決定
3. 批次執行：這組所有目標頁的相關行在同一輪用並行的讀取呼叫取得（讀行範圍，不整頁讀）；修改也在同一輪發出，同一頁的多處修改合成一次
4. 這組改完跑一次 `uv run python -I tools/lint.py --compare .llm-wiki/lint-before.json`，回報 `resolved`、`new`、`remaining`；出現 `new` 要處理或說明
5. 每組收尾：`commit "docs(wiki): edit 依第 N 次 lint 修正" <修改的頁面>`。還有下一組時，建議使用者先 `/clear`，再說「修第 N 次 lint 的 X 類」；報告就是下一組需要的全部上下文
6. 修復中發現 `tools/` 或流程要改時，記進報告的「現行試行機制評估」，另開對話做
