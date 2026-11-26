---
name: wiki-ingest
description: 把 raw/sources/ 的新來源（PDF、網頁快照）整理進 wiki。使用者說「ingest …」或 /wiki-ingest 時使用。
argument-hint: <raw 路徑或網址>
---

# /wiki-ingest $ARGUMENTS

開工前讀 `PURPOSE.md`、`wiki/index.md`、對應領域的 index 與相關主題頁、`SCHEMA.md`。工具語法見 `docs/tools.md`，派工細節見 `docs/dispatch.md`。

每篇來源都做完整閱讀、漏讀檢查、`concept-reader`（改到 concept 頁時），不分級。頁面追求知識密度、深度、廣度，不追求完整：每段都要有讀者可學、可用的內容；不影響結論的細節不寫。

## 步驟

1. **取得來源**
   - 來源不在 `PURPOSE.md` 的「納入」範圍時，在確認點告訴使用者
   - session 開始時 hook 列出新來源（`autopilot.py`，schema §10）：自動開始第 1–2 步，停在第 3 步確認點
   - `redact.py scan <raw>`：有 `block` 級的疑似密鑰時，寫頁時不轉錄，改寫 [REDACTED]
   - 網址用 `web_fetch.py`。LLM 不寫 `raw/`（schema §1）：下載到 `.llm-wiki/inbox/`，檔名用 schema §2 的 source 頁名稱，在 `manifest.tsv` 記檔名、SHA-256、版本、網址，再給使用者一行 `cp -n` 的 `!` 指令搬入。付費牆與非授權副本不抓，記進 `MISSING_SOURCES.md`
   - PDF 先跑 `doc.py check`：`NEEDS_OCR` 不讀；`SUSPECT_GLYPHS` 是連字亂碼時先修 `tools/docsrc/readers.py`，公式符號或真的刪節號等少量誤判可以照常讀；`OCR_USED` 頁上的數字看圖確認
   - `ledger.py check <raw>`：`duplicate` 或 `changed` 就停下來回報
   - 3 篇以上或使用者說全自動：閱讀改派閱讀 agent 並行，派法見 `docs/dispatch.md`「閱讀 agent」。單篇一律由主對話讀；超過 15 頁的 PDF 先用 `pdf_tool.py split` 分章，逐章讀
2. **閱讀**：依 schema §3 判定 `source_kind`，照 `references/extraction.md` 寫 `.llm-wiki/extract/<名稱>/extraction.md`，開頭是定位卡和論證鏈。全文讀一遍，不分遍。沒讀完設 `coverage: partial`，列出未讀章節
   - **價值門檻**：來源帶來可重用的綜合、導覽或新連結，才新增或擴寫 concept／entity 頁；只建 source 頁也是正確結果。不為了湊頁數新增或改寫
3. **確認點**：寫頁前列出，等使用者確認（使用者說全自動的批次列進結尾清單，不等；session 開始 hook 自動開始的 ingest 一律要等）
   - `source_kind`；要解決的問題一句；已用的預算
   - 核心論點 3–5 條
   - 要新增或修改的頁，每頁一句；略過的頁；新頁歸屬的主題（沒有合適主題時提出建議，由使用者決定）
   - 與現有內容的矛盾；要放的圖（原文編號、放哪頁、理由，規則見 `references/figures.md`）
   - 確認後：選定的圖先看過，再複製到 `wiki/assets/<source 名稱>/`
4. **寫 source 頁**：照 `assets/source-page.md` 與 `references/source-writing.md`；必填欄位見 schema §3（`status` 有 raw 出處就寫 `active`，沒有才寫 `draft`）。專有名詞保留英文（schema §4）；「## 名詞」只解釋這份來源自己定義的詞，通用術語連到 concept 頁，連過去之前確認那一頁有它的定義
5. **改 concept／entity 頁**：照 `references/concept-writing.md`，改寫既有論點，不在末端追加條目。frontmatter `sources` 指回 source 頁；新建頁的術語有常見中文說法時寫進 `aliases`（schema §3、§4）；跨來源結論標「推論」；連到這個 source 的 comparison、synthesis 頁也一併檢查。不回頭改舊 source 頁的啟後：啟後由研究系譜從承先反推
6. **矛盾**：兩頁 frontmatter 設 `status: disputed`；內文用文章的意思寫出各來源的說法與條件，各附出處，不加標籤。能用一手來源查證就修正並解除，在 log 內文記下依據（頁碼、表號）；查不到或一手來源互相矛盾，才交給使用者
   - **取代**：新來源明確更正或更新舊說法（同作者新版、勘誤、後續研究直接推翻）時，不算矛盾：舊說法移到該頁的「## 已被取代的說法」，新說法寫進正文。整份來源被新版取代時跑 `ledger.py supersede`（schema §9）
7. **導覽**：新頁加進主題頁；主題增減才改 index；改變領域層級結論才改 `wiki/overview.md`；改完主題頁後跑 `uv run python -I tools/wiki.py lineage <這篇所屬的主題…>`，更新研究系譜（source 頁「啟後」連的檔）
8. **核對**：每個 source 頁派 `source-verifier`，prompt 一律加「做漏讀檢查」。主對話跑 `doc.py quote --from <verify.md> --source <raw>`，不過就退回，最多 2 次；漏讀檢查與核對的 ⚠️、❌ 全讀並回原文確認，漏讀的 ❌ 要補寫頁面；✅ 抽查 2 條或 20%；修正由主對話做，concept 頁的 ❌ 照 `concept-writing.md`「核對結果怎麼修」。改到的 concept 頁在修正後派 `concept-reader`（派法見 `docs/dispatch.md`）
9. **lint**：`lint.py`，本次頁面的 `frontmatter_errors`、`alias_conflicts` 要為空；`alias_in_prose` 列到本次頁面的條目逐條判斷
10. **收尾**：`ledger.py add-source <raw> <source 頁>`；`log ingest "標題"`；`commit "docs(wiki): ingest 標題"`，路徑含 `wiki/meta/ledgers/` 與 `wiki/meta/lineage/`。`MISSING_SOURCES.md` 有列就刪掉那一列。`log` 印出 lint 提醒時轉告使用者。commit 之後由主對話跑 `uv run python -I tools/digest.py --mark`（批次只在最後一篇 commit 後跑一次），把印出的摘要放進給使用者的最後一則訊息；它列出上次看摘要之後新增的來源、結論、被取代的說法與新的 disputed 頁
