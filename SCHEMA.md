---
title: Schema
---

# Schema — Wiki 結構規則

頁面類型、frontmatter、連結與專有名詞、版本控制等共通規則。各流程的步驟以 skill 為準。

## 1. 四層架構

| 層 | 路徑 | 誰寫 | 規則 |
|---|---|---|---|
| 原始資料 | `raw/sources/`、`raw/assets/` | 人類 | **不可變**。LLM 只讀不改；LLM 下載的來源先放 `.llm-wiki/inbox/`（附 `manifest.tsv`），由使用者用一行不覆蓋的指令搬入（wiki-ingest skill 第 1 步） |
| Wiki | `wiki/` | LLM | 所有知識頁面，**只存在本機，不上 HackMD** |
| 規則 | `PURPOSE.md`、`SCHEMA.md`、`GLOSSARY.md`、`CLAUDE.md`、`docs/`、`.claude/skills/` | 人類 + LLM 共同演化 | 改動要記入 `wiki/log.md` |
| 個人筆記鏡像 | `hackmd_note/` | 人類 | 從 HackMD PARA 資料夾拉下來的筆記；**唯一可以推上 HackMD 的目錄** |

## 2. 頁面類型

| 類型 | 目錄 | 用途 | 檔名 |
|---|---|---|---|
| entity | `wiki/<domain>/entities/` | 人物、組織、產品、資料集 | `kebab-case.md` |
| concept | `wiki/<domain>/concepts/` | 理論、方法、技術、術語 | `kebab-case.md` |
| source | `wiki/<domain>/sources/` | 單一著作的摘要（一份著作一頁，可含多個 `raw/sources/` 檔，例如多語版本或同一實作的多個檔案） | `YYYY-作者或機構-短標題.md` |
| query | `wiki/<domain>/queries/` | 有保存價值的問答與研究結果 | `YYYY-MM-DD-短標題.md` |
| synthesis | `wiki/<domain>/synthesis/` | 跨多個來源的分析、論點整合 | `kebab-case.md` |
| comparison | `wiki/<domain>/comparisons/` | 並排比較（表格為主） | `a-vs-b.md` |
| topic | `wiki/<domain>/topics/` | 主題導覽（見下方「主題頁」） | `kebab-case.md` |

source 頁的版本：
- source 頁檔名的年份跟 raw 檔名一致；raw 檔名在下載時取，年份用首次發表的年份（例：arXiv v1 是 2020、讀的是 2021 的 v4，檔名用 2020）。實際讀的版本寫在書目行。這條規則以前建立的頁不改名
- 書目行寫明讀的版本（期刊卷期頁，或工作論文編號與日期）與取得網址；另有期刊版時寫「正式發表於…（本頁沒有讀）」
- 工作論文版在「立場」註明未經同儕審查；頁碼一律是讀的那個檔案的頁碼

規則改版時，舊頁不批次改寫，下次 ingest 或 review 碰到再改。

特殊頁面：`wiki/index.md`（總目錄，只連到各領域的子 index）、`wiki/<domain>/index.md`（該領域的主題目錄）、`wiki/log.md`（操作紀錄，只追加）、`wiki/overview.md`（全域摘要）。

`wiki/meta/` 放工具產生的資料，不手動編輯：`ledgers/`（來源帳本，§9）、`log-archive/`（封存的 log）、`lineage/`（研究系譜，由 `wiki.py lineage` 從 source 頁的「研究脈絡」反推）。

`wiki/log.md` 的規則（舊紀錄不改）：
- 標題格式 `## [YYYY-MM-DD] <類型> | 標題`；只記 `ingest`、`lint`、`schema`，其他改動只留 git
- 每筆內文最多 1 行（`wiki.py log` 會擋）
- 封存：`log.md` 超過 150 筆時，用 `wiki.py log-archive --apply` 把較舊的紀錄依月份原文搬到 `wiki/meta/log-archive/YYYY-MM.md`（只搬移不改寫；最後一筆 lint 和它之後的紀錄留在 `log.md`）。`wiki/meta/` 不是內容頁：不計入頁數與孤兒頁檢查，搜尋不讀；lint 只讀其中的帳本（`ledgers/`）與研究系譜（`lineage/`）

### 拆頁粒度（避免概念頁爆炸）
- 子方法、變體先寫成**家族頁裡的小節**（例如各種卡農寫法放在 `canon.md`）
- 符合下列任一條件才拆成獨立頁：被 **2 份以上來源**獨立討論，或在其他頁被連結 **3 次以上**
- 拆頁條件由 Lint 檢查並提出建議，不在 ingest 時直接拆

### 主題頁
- 導覽層級是 `index → 領域子 index → 主題頁 → 內容頁`：index 只列領域，子 index 只列主題，主題頁是頁面歸屬的唯一記錄處，frontmatter 不另記
- 內容：一句範圍說明，加上分區的成員清單（概念與實體、整合與比較、來源），每項 `- [標題](路徑) — 一句話摘要`；不寫論點，因此免填 `sources`
- 先讀：成員 8 個以上的主題頁，在範圍說明下方加 `## 先讀` 小節，依閱讀順序編號列 3 到 6 個成員（`1. [標題](路徑) — 為什麼先讀`）。這是導覽，不是論點；挑選原則是先概念頁、後代表性來源。新成員加入時不必重排，lint 時再檢查是否過時
- 歸屬：預設一個主題；內容確實以兩個主題為核心才重複列
- 例外：橫跨多個主題的 synthesis 頁可以只列在領域子 index，其他類型一律要有主題
- 新頁加進既有主題頁；只有主題增減時才改領域子 index（領域增減才改總 index）。沒有合適主題時，LLM 不自行新建，先提出建議由使用者決定
- 主題頁不算內容頁：不計入 §8 的頁數，它的連結也不算孤兒頁檢查的入連結

### 畫布
- `wiki/canvases/*.canvas` 是給人看的視覺地圖，只由使用者要求時建立或更新，不是歸屬的記錄處（歸屬以主題頁為準），lint 不檢查，內容可能落後於主題頁
- 主題間的箭頭要有 wiki 頁之間的實際連結為依據，箭頭方向是連結的方向

## 3. Frontmatter（每頁必備）

```yaml
---
title: 頁面標題
type: entity | concept | source | query | synthesis | comparison | topic
domain: <領域>                   # 所屬領域（kebab-case，見 §8；跨領域用 cross），超過 100 頁時依此分目錄
tags: [tag1, tag2]
sources: [wiki/<領域>/sources/xxx.md]   # 本頁論點的出處（repo 相對路徑）
created: 2026-11-05
updated: 2026-11-05
status: active | draft | disputed | superseded | archived
---
```

`status`（五種值，取代舊的 `draft`｜`needs-review`）：
- `active`：有出處、現行。新頁有出處（`sources` 非空）時的預設值。topic 頁免填 `sources`（§2），建立時直接寫 `active`
- `draft`：沒有任何出處（`sources` 空），之後要補出處或刪掉。沒有出處的新頁（topic 頁除外）才用
- `disputed`：有未解決的矛盾（原 `needs-review`），流程見 wiki-ingest skill
- `superseded`：整頁被取代，必填 `superseded_by: <取代者的 repo 相對路徑>`。頁不刪，只當歷史；來源層級的取代走 §9 的帳本
- `archived`：不再維護、保留歷史
- `/wiki-query` 預設略過 `superseded` 與 `archived` 的頁，使用者問歷史時才讀
- `tools/lint.py`：`status` 要是以上五種之一；`active` 的非 topic 頁要有 `sources`；`superseded` 要有 `superseded_by`。`disputed` 清單在 `disputed`。舊值還沒轉換的頁（`needs-review`，或有出處／是 topic 頁的 `draft`）列在 `legacy_status`，轉換用 `tools/status_migrate.py`（預設 dry-run，`--apply` 才寫，只改 `status` 那一行）

source 頁另外必備：

```yaml
tier: primary | secondary | tertiary
coverage: full | partial
source_kind: paper | report | article | docs | book | code   # 新建的頁必填（日期界線見 tools/lint.py 的 SOURCE_KIND_SINCE）
```

- `source_kind`：來源類型，決定怎麼讀（讀法表見 wiki-ingest skill 的 `references/extraction.md`）。和 `type`（頁面類型）不同
  - `paper`：論文、預印本、工作論文、綜述論文
  - `report`：技術報告、模型發表
  - `article`：部落格、解說文、轉述文、對話與訪談
  - `docs`：官方文件、規格、決策文件
  - `book`：書籍章節、講義
  - `code`：程式、資料集

- `tier`
  - `primary`：論文本身、官方技術報告、原始資料，以及有原創研究或自有回測的業界白皮書；公司或專案寫自家產品、自家做法的文章與文件也算（例如公司的工程部落格）。未經同儕審查或有利益衝突時，在 source 頁的「立場」標示，不用降級表達
  - `secondary`：綜述、第三方的部落格與新聞報導等轉述（作者不是那個產品或做法的開發者）
  - `tertiary`：教科書摘要、百科、懶人包
- `coverage: partial` 時，內文要有「## 未讀章節」小節，列出還沒讀的部分，供之後補讀

concept、entity、synthesis、comparison 頁可以另外寫頁面關係 relations（選填，試行中）。四種關係各是一個頂層欄位，不要包在 `relations:` 底下（Obsidian 的屬性面板不支援巢狀屬性；lint 會把舊寫法報成 `nested_relations`）：

```yaml
requires: [wiki/<領域>/concepts/a.md]     # 讀懂本頁要先讀的頁（通常是內文「前置知識」列的頁）
extends: [wiki/<領域>/concepts/a.md]      # 本頁的方法改良或延伸自對方
alternative_to: [wiki/<領域>/concepts/x.md]    # 解決同一個問題的另一種做法；兩頁都要寫
contradicts: [wiki/<領域>/concepts/y.md]       # 兩頁說法矛盾；兩頁都要寫，也都要設 disputed
```

- 目標寫 repo 相對路徑（同 `sources`），只能指向上面四種頁，不指向 source 頁或主題頁
- 每個關係都要有內文依據：目標頁要在本頁內文有連結。關係只是把內文已經寫的東西結構化，不新增論點
- 反方向（誰以本頁為前置、誰延伸本頁）不寫，由 `tools/wiki.py related` 算出來
- `requires` 不可以繞成一圈；矛盾解決後兩頁的 `contradicts` 一起刪
- `tools/lint.py` 的 `relation_problems` 檢查以上規則。舊頁不批次補，下次 ingest 或 review 碰到再補

concept、entity 頁可以另外寫 `aliases`（選填）：

```yaml
aliases: [對位法]   # 這個術語常見的中文說法；內文不用，給 tools/search.py 與 lint 用
```

- 只寫在這個領域裡專指這個術語的中文說法，不寫上位詞或一般用語（counterpoint 頁不寫「音樂」）
- 只寫 wiki 或來源實際用過、或通行的說法，不自己造譯名
- 同一個領域裡，一個別名只能屬於一頁
- 用法見 §4「專有名詞保留原文」


## 4. 連結規則

- 頁面之間用相對路徑 Markdown 連結：`[RAG](../concepts/rag.md)`
  - 原因：Obsidian 與一般 Markdown 工具都能顯示
- 標題錨點用標題文字，空白寫 `%20`：`[call and response](x.md#call%20and%20response)`，不用 GitHub slug（`#call-and-response`），由 lint 的 `broken_anchors` 把關
- 每個論點盡量附出處：`（見 [來源](../sources/2024-xxx.md)）`
- **source ↔ concept／entity 必須雙向**：source 頁有「## 本來源更新的頁面」小節，列出它新增或修改的頁；那些頁的 frontmatter `sources` 要指回這個 source 頁。由 Lint 檢查兩邊是否一致；其他頁面之間的連結不要求雙向。source 頁之間的前後關係只寫在後來那一頁的「承先」，舊頁不改；反方向由研究系譜（`wiki.py lineage`）推出
- **推論要標示**：LLM 把多個來源接起來得出的結論，寫成「推論：…（依據 [A](…)、[B](…)）」，不可寫得像來源本身的論點；沒有可查證來源的論點記成「待查證」
- **解說不是論點**：concept 頁標「解說：」「示例（整理者構造）：」的段落，是用頁面已有的事實與公式做白話說明，不新增事實，所以不附出處，也不標推論（寫法見 wiki-ingest skill 的 `references/concept-writing.md`）
- **專有名詞保留原文**（只適用 wiki 頁；HackMD 筆記是使用者自己寫的，不套用）
  - 方法、模型、指標、領域術語寫英文原文（例：counterpoint、equal temperament、call and response、basso continuo）；縮寫照原文（BWV、MIDI、BPM）
  - **詞表就是 wiki 自己**：有自己 concept／entity 頁的術語，一律用那一頁的英文名稱，常見中文說法寫進那一頁的 `aliases`（§3）。新建頁時順手補 `aliases`，詞表就跟著長
  - 沒有自己頁面的常見領域詞（專家、報酬、損失、檢定）照常寫中文；方法或模型的專名沒有頁面時仍寫英文
  - 一般敘述照常寫中文；這條只管專有名詞，中文翻譯腔另外處理
  - **名詞解釋**：source 頁的「## 名詞」只解釋這份來源自己定義、或用法和通用意思不同的詞；通用術語寫在 concept 頁，source 頁連過去
  - 名詞解釋可以附常見中文當搜尋別名，寫成 `counterpoint（對位法）`；內文不用中文別名
  - **檢查**：`tools/lint.py` 的 `alias_in_prose` 列出內文用了某頁 `aliases` 的地方，只比對同領域的頁（cross 頁的別名各領域都比對，cross 頁比對所有領域）。列出來的是候選：同一個中文在句子裡只是一般用語時（例如「對位」不是指 counterpoint 這個技法）就不改。語意檢查只另外列「沒有頁面、但明顯是專名」的中文翻譯，不逐詞詢問
- source 頁的原始檔要寫成相對連結，例如 `[x.pdf](../../../raw/sources/x.pdf)`（source 頁在 `wiki/<domain>/sources/`）（空白寫 `%20`），不要寫成行內程式碼，讓 Obsidian 能直接開原文對照；frontmatter 的 `sources:` 照舊寫 `raw/sources/x.pdf`。lint 的 `unlinked_raw_sources` 會列出漏掉的頁，`uv run python -I tools/wiki.py link-raw <頁>` 可以把行內程式碼轉成連結
- 圖片用本機相對路徑：原始資料自帶的圖直接連 `raw/sources/<名稱>_images/`；從 PDF 截的圖存到 `wiki/assets/<source 名稱>/`（`.llm-wiki/extract/` 是暫存區，不能被連結）
- 圖片數量、截圖與說明句規則：見 `.claude/skills/wiki-ingest/references/figures.md`

## 5. 操作流程

每個流程的步驟在對應的 skill（`.claude/skills/<skill>/SKILL.md`）：`wiki-ingest`、`wiki-query`、`wiki-lint`、`wiki-save`、`wiki-study`、`wiki-slides`。

## 6. HackMD 個人筆記優化

目前沒有優化筆記的流程（refine 已刪除，可以從 tag `pre-slim` 救回）；可以推上 HackMD 的範圍見 §1。

## 7. 版本控制（git + GitHub private repo）

- 遠端：自己的 GitHub private repo，分支 `main`
- **進 git**：`.gitignore`、`README.md`、`CLAUDE.md`、`PURPOSE.md`、`SCHEMA.md`、`GLOSSARY.md`、`MISSING_SOURCES.md`、`PLAN.md`、`LICENSE`、`LICENSE-CONTENT`、`docs/`、`template/`（公開模板）、`wiki/`、`tools/`、`tests/`、`.claude/skills/`、`.claude/agents/`、`.claude/settings.json`、`.claude/hooks/`、`pyproject.toml`、`uv.lock`、`hackmd_note/README.md`、`slides/*/outline.md`（簡報大綱）
- **不進 git**（見 `.gitignore`）：`.mcp.json`（token）、`.claude/settings.local.json`、`raw/`（著作權與大小，另外備份）、`hackmd_note/` 的筆記內容、`.obsidian/`、`.llm-wiki/`、`.vault-meta/`、`.venv/`、`slides/` 底下的 `.pptx` 與 `render/`（可由大綱重新產生）
  - 因此 wiki 裡連到 `raw/sources/*_images/` 的圖片在 GitHub 上看不到，只在本機有效
- 有寫入 repo 的 command 結束時**自動 commit**（唯讀的 `/wiki-query`、`/wiki-study` 只在使用者同意保存後才 commit）
- **commit 格式：Conventional Commits**
  - 格式：`<type>(<scope>): <subject>`。subject 不超過 50 字元、不加句號，開頭寫操作名稱；body（選填）每行不超過 72 字元，寫變動與原因；footer 放任務編號或 `BREAKING CHANGE:`
  - type 用標準 Conventional Commits（`feat`、`fix`、`docs`、`refactor`、`test`、`chore` 等），依變動性質選；scope 必填，限 `wiki`（知識頁與各 command 的產出）、`slides`（簡報大綱）、`schema`（schema、CLAUDE.md、purpose、GLOSSARY）、`docs`（`docs/`）、`skills`、`agents`（`.claude/agents/`）、`tools`（`tools/` 與 `tests/`）、`config`（settings、hook）、`repo`（其他：README、PLAN、MISSING_SOURCES、template 等）；`wiki.py commit` 會擋清單外的 scope
- **不自動 push**；人類說要推時才 `git push`
- commit 前用 `git status` 確認沒有誤加私人檔案

## 8. 規模門檻：依領域分目錄

- 計算方式：`wiki/` 底下所有內容頁（不含 index、log、overview 與主題頁）
- 內容頁超過 100 頁時改成領域在外層的目錄結構（`tools/migrate_domains.py`）。領域名稱自己決定，用 kebab-case，不能和類型目錄或 `assets`、`meta`、`canvases` 同名；工具把 `wiki/` 底下其他的目錄都當成領域。`cross` 是保留名稱，放跨領域的交會主題，它的頁會和每個領域比對專有名詞（§4）。這個 wiki 用哪些領域記在 `PURPOSE.md`。例如：

```
wiki/
  index.md            # 只連到各領域的子 index
  overview.md  log.md
  music/    index.md  topics/ concepts/ sources/ synthesis/ comparisons/ ...
  history/  index.md  topics/ concepts/ entities/ sources/
  cross/    index.md  topics/ concepts/ sources/   # 跨領域的交會主題；跨領域的 source 也放這裡
  assets/  meta/  canvases/   # 共用，不分領域
```

- 依 frontmatter 的 `domain` 決定位置，所以 `domain` 必填；新頁直接建在 `wiki/<domain>/<類型>/`，需要的子目錄（例如 `queries/`）第一次用到時再建
- 不同領域的頁互相連結時，相對路徑要多一層（例如 `../../history/concepts/x.md`）；source 頁連 raw 是 `../../../raw/sources/…`
- 頁面改 `domain`、改名或批次搬移時用 `tools/move_pages.py`（會一併改寫連結、`sources`、畫布與帳本），不能只改 frontmatter 或手動 `git mv`
- `wiki/log.md` 只追加，舊紀錄裡的路徑不改

## 9. 來源帳本

由 `tools/ledger.py` 寫入與檢查，`tools/lint.py` 回報違規。帳本在 `wiki/meta/ledgers/source-ledger.json`，不手動編輯。

- 每筆記錄：raw 檔的 SHA-256、路徑、source 頁、`independence_key`（預設為 source 頁路徑；同一份研究分成不同 source 頁時要設成相同值）、`review_state`（active／superseded）、`refresh_due`（選填，過期即視為過時）
- 用途：ingest 前偵測重複或被改過的 raw、標出過期來源
- 論點的可信度不另記帳本，看頁面標記，信心分數由 `tools/confidence.py` 從這些標記算出（見下方「信心分數」）：source 頁的「結論」（舊頁是「可驗證的主張」）與附頁碼的內文已核對；「推論」、審閱註記是暫定；矛盾在兩頁的 frontmatter 設 `status: disputed`，內文用文章的意思寫出各來源的說法並附出處，不加標籤；沒有證據寫「待查證」
- 絕不捏造引句、頁碼、日期或 locator；沒有證據就寫「待查證」

### 信心分數
- `uv run python -I tools/confidence.py <頁…>` 為每個論點算 0–1 的分數與標籤，不存檔。分數每次從頁面標記算出，不另設論點帳本：帳本會和頁面標記重複，頁面一改就對不上
- 論點是非 source 內容頁中附了 source 頁連結的一行（「同上」與小節開頭的「（見 …）」沿用前面的出處）。表格列與只引 concept 頁的行不計分，所以 comparison 與 synthesis 頁的平均分偏低
- 標題含「分歧」「矛盾」「不一致」的小節，同一行的多份來源代表意見不同，標「有分歧」、分數 ×0.7，不算多來源支持
- 每份獨立來源（帳本 `independence_key` 相同的算一份）的證據＝tier 權重（primary 1.0、secondary 0.6、tertiary 0.4）× 定位（沒有頁碼、表號、式號 ×0.8）× 狀態（被取代 0、過期 ×0.5）；合併成 1 − Π(1 − 0.7 × 證據)
- 上限：「推論：」0.5、「待查證」0.1；`disputed` 頁 ×0.7；「已被取代的說法」小節 0
- 分數不隨年份衰減。寫頁面時不必管分數，照 §4 的出處與標記規則寫，分數自然反映

### 取代與過時
- **來源被取代**：同一份研究出了新版、勘誤，或後續研究直接推翻時，用 `ledger.py supersede <舊頁> --by <新頁> --reason "<新版改了什麼>"`。舊紀錄留在帳本，狀態改成 `superseded`，記下取代者與日期；舊 source 頁不刪
- **論點被取代**：舊說法不刪，移到該頁的 `## 已被取代的說法` 小節，一行一條：`- YYYY-MM-DD｜舊說法（見 [舊來源](…)）；取代它的說法（見 [新來源](…)）`。正文只寫新說法。lint 的 `supersession_problems` 檢查日期與出處連結
- 取代和矛盾不同：取代是新來源明確更正舊說法，有先後；矛盾是兩份來源各說各話，照上面的 `disputed` 流程
- **過時**：頁面 frontmatter `sources` 引用的來源已被取代或過了 `refresh_due`，lint 的 `stale_pages` 會列出，下次 review 時改寫或補新來源。設定或清除到期日：`ledger.py refresh <頁> --due YYYY-MM-DD｜--clear`

## 10. 知識生命週期、搜尋與協作

### 記憶分層
LLM Wiki v2 的四層記憶對應到既有的位置；資訊往上升級都要經過既有流程，內容進 `wiki/` 只能走 `/wiki-ingest`，或在使用者同意後走 `/wiki-save`（session 摘要、對話內容不直接寫進 wiki）：

| 層 | 這裡的位置 | 升級方式 |
|---|---|---|
| working（剛收到、還沒處理） | `.llm-wiki/inbox/`、`.llm-wiki/extract/` | `/wiki-ingest` 或丟棄 |
| episodic（單次的整理） | source 頁、`wiki/<domain>/queries/`、`wiki/log.md` | 同一結論被多次使用或多份來源支持時，整合成 synthesis／comparison |
| semantic（跨來源的事實） | concept、entity、synthesis、comparison 頁 | 定論改 `wiki/overview.md` |
| procedural（做法） | `.claude/skills/`、`docs/`、`SCHEMA.md` | 重複出現的流程問題，由 `/wiki-lint` 報告提出，使用者同意才改規則 |

`uv run python -I tools/retention.py promote` 列出升級建議：用過 3 次以上或被 2 頁以上連到的 query 頁，以及放了 14 天以上的 inbox／extract 項目。只是建議，照做前先問使用者。

### 遺忘曲線
- 留存率 R = exp(−天數 ／ S)，天數從最後一次強化算起；S ＝ 頁面類型的基準天數（concept、entity、synthesis 365，comparison 270，source 180，query 60）×（1 + ln(1 + 使用次數)）
- 強化：頁面的 `created`／`updated`，以及用 `retention.py touch <頁…> --kind query` 明確登記的使用（`/wiki-query` 回答後登記引用到的頁）。讀頁不算強化，因為 subagent 的檢查會大量讀頁
- 留存率只影響 `search.py --retention` 的排序（最多差 10%）與 lint 的 `faded_pages` 清單（低於 0.3），不刪頁、不改頁、不影響信心分數
- 使用紀錄在 `.llm-wiki/memory/access.jsonl`，只在本機，不進 git

### 最近新增摘要
- `/wiki-ingest` commit 後由主對話跑 `tools/digest.py --mark`，把上次看摘要之後的新來源、結論、被取代的說法與新的矛盾整理成短摘要給使用者；標記存在 `.llm-wiki/memory/`，不進 git，摘要不寫進 `wiki/`

### 學習狀態
- `/wiki-study` 的探底題、測驗批改與補弱的檢查題，每題的結果與答案依據頁記在 `.llm-wiki/memory/learning.jsonl`（只在本機，不進 git），用 `tools/learning.py` 寫入與讀取。看得出使用者的想法錯在哪裡時，多記一行誤解；只記那一題的內容，不記對使用者本人或推理習慣的評語
- 用途：下一次 `/wiki-study` 依弱點頁與誤解出題，「建議接著讀」與補弱的選頁依弱點排序。它不影響留存率、信心分數與搜尋排序，也不改主題頁的「先讀」

### 問題紀錄
- `/wiki-query` 與 `/wiki-study` 結束時，問題、wiki 答得出的程度與主要缺口記在 `.llm-wiki/memory/questions.jsonl`（只在本機，不進 git），用 `tools/questions.py` 寫入與讀取。缺口分三種，各對應一種修法：缺來源（找來源、`/wiki-ingest`）、頁面難懂（改寫頁面）、搜尋沒找到（加進 `tools/search_eval.tsv`）
- 用途：讓使用端的需求決定要補哪些來源、改哪些頁、哪條規則值得改。`/wiki-lint` 報告附上次 lint 之後的摘要；它不影響留存率、信心分數與搜尋排序，照摘要做任何修改前先問使用者

### 搜尋
- `tools/search.py`：BM25（中文兩字一組、英文專有名詞保留整詞，以 `##` 小節為單位；查詢裡出現某頁的 `aliases` 時，也搜那一頁的英文標題）＋ 圖（`relations`、內文連結雙向、frontmatter `sources`）＋ 選用的本機向量，用加權 RRF 合併
- 向量只用本機模型（`uv sync --group search` 後可用；沒裝時自動只用 BM25 與圖），wiki 內容不送出本機。改用外部 embedding API 要先問使用者
- 改搜尋的權重或預設流程前，先跑 `search.py eval`（題目在 `tools/search_eval.tsv`），和改之前比

### 自動化（hooks，`.claude/settings.json` → `tools/autopilot.py`）
| 事件 | 做什麼 |
|---|---|
| session 開始（只在新 session，resume 與 `/compact` 不重發） | 列出新來源（`raw/sources/` 有、帳本沒有）、內容被改過的原始檔、新來源裡的疑似密鑰、到期的 lint、過時頁、帳本問題、別人的認領。有新來源時自動開始 `/wiki-ingest` 第 1–2 步，停在第 3 步確認點（不寫 wiki 頁） |
| 寫入 `wiki/`、`hackmd_note/` 之前 | 內容有 `block` 級疑似密鑰就擋下（exit 2） |
| 寫完 `wiki/` 頁之後 | 檢查那一頁的 frontmatter、`relations`、「已被取代的說法」，有問題就要求 agent 修；壞連結與錨點只提醒（目標可能是這次稍後才寫的頁） |
| session 結束 | 把這次的 commit、log 新增與未 commit 的 wiki 檔寫成摘要，放 `.llm-wiki/memory/sessions/`（working 層，不進 wiki，不進 git） |

- 查詢結果是否保存仍照 `/wiki-query` 第 6 步提議，使用者同意才存；lint 的到期提醒在 session 開始時列出

### 敏感資料
- `tools/redact.py` 分兩級：`block`（私鑰、各家 API token、JWT）與 `warn`（`password = "…"` 這類指派，常是文件裡的範例，只回報）
- `raw/` 唯讀不改；檢查點是 session 開始（新來源，最多掃 20 秒，沒掃完的到 ingest 第 1 步再掃）、寫入 `wiki/`／`hackmd_note/` 前（hook）、`wiki.py commit`（staged 內容有 `block` 就拒絕）
- 回報只顯示遮蔽後的開頭 4 字元，不印出密鑰本身；wiki 裡需要提到時寫 `[REDACTED]`

### 協作
- 主對話以外的 session 或 agent 同時在跑時，寫共用頁（`wiki/index.md`、主題頁、帳本、被 3 個以上內容頁連到的頁；主題頁、index、overview 的連結不算）前先 `work.py claim <頁…> --by <名稱>`，寫完 `release`；被別人認領的頁不寫，等對方釋放或到期
- 派工前的快照改用 `work.py snapshot <名稱>`，收回時 `work.py changed <名稱> --mine <自己同期改的頁…> --allow .llm-wiki/extract/<名稱>/`（比對整個 repo）
- 同步層是 git：各 session 的修改由主對話整理後 commit；subagent 仍只寫 `.llm-wiki/extract/<名稱>/`
