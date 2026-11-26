# 工具語法

所有 `tools/` 的用法都在這裡；參數細節以各工具的 `--help` 為準。`log` 與 `commit` 每個任務都會用，語法留在 `CLAUDE.md`。

## 取得來源（ingest）
| 用途 | 指令 |
|---|---|
| 帳本檢查（ingest 前） | `uv run python -I tools/ledger.py check <raw 檔>`：`new`／`duplicate`（已收錄）／`changed`（同路徑內容不同） |
| 帳本登記（ingest 後） | `uv run python -I tools/ledger.py add-source <raw 檔> <source 頁> [--independence-key K] [--refresh-due YYYY-MM-DD]`；其餘子指令（`backfill`、`rekey`、`validate`）見 `--help` |
| 來源被取代 | `uv run python -I tools/ledger.py supersede <舊 source 頁> --by <新 source 頁> --reason "<原因>" [--date YYYY-MM-DD]`：舊紀錄改成 `superseded`，記下取代者、日期與原因；取代者本身已被取代、或舊頁已經被取代時拒絕；原本指向舊頁的取代紀錄會改指向新頁，避免連鎖指到過時的頁 |
| 到期日 | `uv run python -I tools/ledger.py refresh <source 頁> --due YYYY-MM-DD`（或 `--clear`） |
| 網頁 | `uv run python -I tools/web_fetch.py <url> [--md]` → `.llm-wiki/extract/web/<slug>/`；官方文件（Claude Code、MCP 規格等）加 `--md`：先抓 `<url>.md`，沒有就自動退回 HTML；要 ingest 的網頁把快照另存成 `raw/sources/YYYY-作者-短標題.md`（加上 `_images/`） |
| PDF 章節 | `uv run python -I tools/pdf_tool.py toc <pdf>`；`split <pdf> --level 1 --out .llm-wiki/extract/<名稱>/sections`（`<!-- figure: … -->` 標出圖的位置） |
| PDF 圖表 | `uv run python -I tools/pdf_tool.py figures <pdf> --out .llm-wiki/extract/<名稱>/figures`（`manifest.json` 附圖說；裁不出來的標 `crop: page`）；指定區域：`shot <pdf> --pages 12 --clip x0,y0,x1,y1 --out .llm-wiki/extract/<名稱>/shots`。不給 `--out` 時，輸出資料夾以 PDF 檔名命名，不是 source 頁檔名，所以一律給 `--out` |

抽取結果只是中間產物，不放進 `raw/`；wiki 要用的圖複製到 `wiki/assets/<source 名稱>/`。

## 讀原文與核對
| 用途 | 指令 |
|---|---|
| 搜尋 | `uv run python -I tools/doc.py grep "<regex>" <檔案或目錄…> [-i] [-C 2] [--pages 3-8]` |
| 印出範圍 | `uv run python -I tools/doc.py show <檔案> --pages 12-13`（或 `--lines 100-160`） |
| 驗收引句 | `uv run python -I tools/doc.py quote --from <verify.md> [--source <raw>]`：每行 `證據：` 的引句要逐字出現在標示的頁或行；exit 0 全部通過 |
| 匯入前檢查 PDF | `uv run python -I tools/doc.py check <pdf…>`：每個檔印頁數、無文字層的頁與 OCR 狀況、字中可疑字元（壞掉的連字，附例字與字型）、各字型的 span 數，最後一行是結果 `OK`／`OCR_USED`／`SUSPECT_GLYPHS`／`NEEDS_OCR`；exit 0 表示全部 `OK` 或 `OCR_USED` |

PDF 換行會拆開詞組，找不到時改用短關鍵字或 `\s+`。

沒有文字層的頁（掃描檔）會自動用 Tesseract 英文 OCR，結果依檔案雜湊快取在 `.llm-wiki/ocr/`；頁碼照樣寫 `p.N`，`show` 的頁首標成 `--- p.N (OCR) ---`。OCR 的數字與專有名詞可能讀錯，引用 OCR 頁的數字前要用 `pdf_tool.py shot` 對照頁面圖片。找不到 Tesseract 時這些頁沒有文字，`check` 會報 `NEEDS_OCR`。整份文字層是亂碼時（常用英文字比例低於 5%，例如字型對應壞掉的舊掃描檔），每頁都改用 OCR，`check` 印「文字層亂碼，整份改用 OCR」。出現 `SUSPECT_GLYPHS` 時，把報告裡的字型與字元補進 `tools/docsrc/readers.py` 的字型對照表。

PDF 依內容順序讀（雙欄論文先左欄再右欄），同一基線的表格列與公式片段併成一行；旋轉表逐格輸出。Elsevier 的數學字型（`AdvMacMthSy*`、`AdvMPi*`）會還原成 `−`、`×`、`=` 等符號。逐欄寫入的表格、分數、圖內文字仍可能錯位，數字要對圖時用 `pdf_tool.py shot` 看圖。

## 檢查
| 用途 | 指令 |
|---|---|
| 機械 lint | `uv run python -I tools/lint.py`（JSON；`--compare <舊 JSON>` 比對修復結果） |
| 逐頁 frontmatter | `uv run python -I tools/lint.py --frontmatter`（TSV） |
| 搜尋（schema §10） | `uv run python -I tools/search.py "<問題>" [-n 10] [--no-vector] [--retention] [--json]`：查詢裡的中文別名（concept 頁的 `aliases`）會加上該頁英文標題一起搜；每行「名次、分數、頁#小節、標題、各路名次（`bm25:2,graph:5`）」；`eval` 跑 `tools/search_eval.tsv`，報告各組合的 recall@5、第一名命中數與 MRR，並和整句字面比對（grep 基準）比；`index` 預先建向量快取（`.llm-wiki/search/`）。向量要先 `uv sync --group search`，預設模型 `Qwen/Qwen3-Embedding-0.6B-Q`（約 1.1 GB，第一次會下載；4 執行緒下建 1533 段的快取約 1 小時，之後只嵌入改過的段落；每次查詢載入模型約 10–20 秒）。15 題評估：整句字面比對（`eval` 裡的 grep 基準，不等於 /wiki-query 舊流程）4、BM25 12、向量 15、三路合併 14 題在前 5 名，用 `uv run --group search python -I tools/search.py …` 執行 |
| 留存與分層（schema §10） | `uv run python -I tools/retention.py touch <頁…> [--kind query｜ingest｜review]`、`report [--below 0.3]`、`promote`；lint 的 `faded_pages` 是狀態清單 |
| 最近新增摘要（schema §10） | `uv run python -I tools/digest.py [--since <commit>] [--limit 2] [--mark] [--save] [--json]`：比較起點 commit 與 HEAD 的內容頁，印出新來源（每篇最多 2 條「結論」，舊頁讀「可驗證的主張」，沒有條列時取段落）、其他新頁、新增的「已被取代的說法」、新標成 `disputed` 的頁、有修改的頁、只改名或搬移的頁與刪除的頁，每類最多 10 項。起點預設是上次 `--mark` 記下的 commit（`.llm-wiki/memory/digest-mark.json`）；沒有標記或那個 commit 不存在時，從 7 天前開始。`--save` 另存到 `.llm-wiki/digests/`。不寫 `wiki/` |
| 學習狀態（schema §10） | `uv run python -I tools/learning.py record <主題頁> <題號｜next> <right｜partial｜wrong｜skipped> <答案依據頁…> [--study <study 頁>] [--kind quiz｜probe｜check] [--note "<一行誤解>"]`：登記 `/wiki-study` 的一題，寫入 `.llm-wiki/memory/learning.jsonl`（不進 git），同一 study 頁的同一題重新登記時新的取代舊的；`--kind` 分測驗題（`quiz`，預設）、探底題（`probe`）與補弱的檢查題（`check`），三種各自編號、互不取代，探底與補弱的題號寫 `next` 會接著當天同主題同種類的號碼，而且不加 `--study`；`--note` 記這題露出的誤解（最多 200 字），會列在 `status` 與 `next` 的弱點頁底下（每頁最新 2 筆）；`status [--topic <主題頁>]` 列各主題的作答日期與對錯數，加上弱點頁（每頁最近 3 次結果的平均失分：錯、沒答 1，部分 0.5，對 0）；`next [--topic <主題頁>] [-n 5]` 列建議先重讀的頁，主題還沒作答過時改列主題頁的「先讀」。只印出建議，不改主題頁 |
| 學習網頁 | `uv run python -I tools/study_html.py build <.llm-wiki/study/….json> [--open]`：把 `/wiki-study` 的 JSON（格式在程式開頭說明）轉成單一本機 HTML，寫在 JSON 旁邊（`.llm-wiki/` 不進 git）；版面與互動在 `tools/study_assets/`（CSS、JS，產生時嵌入）。出處轉成 `obsidian://` 連結、標題取冒號或括號前的短名；只從 CDN 載入 Mermaid，沒網路時顯示縮排樹；測驗有作答框（暫存在瀏覽器）與「複製全部答案」，有 `grading` 時改顯示批改。印三行：網頁路徑、爭議、覆蓋；有「注意：」表示某類題目少於 2 題或出處檔案不存在 |
| 問題紀錄（schema §10） | `uv run python -I tools/questions.py record "<問題>" <answered｜partial｜unanswered> [--kind query｜study] [--gap missing-source｜unclear｜search-miss] [--pages <頁…>] [--note "<缺什麼>"]`：`/wiki-query`、`/wiki-study` 結束時登記一題，寫入 `.llm-wiki/memory/questions.jsonl`（不進 git）；不是 `answered` 時要寫缺口，`answered` 只能帶 `search-miss`，`search-miss` 要列答案頁。`report [--since YYYY-MM-DD] [--json]` 印題數、缺口分類、缺來源與頁面難懂的清單，以及還不在 `tools/search_eval.tsv` 的搜尋沒找到問題（TSV 行，可以直接貼進去）。只追加與印出，不改 wiki |
| 自動化（schema §10） | `uv run python -I tools/autopilot.py brief`（session 開始的提示）、`new-sources [--json]`（帳本沒有的原始檔；雜湊依大小與修改時間快取在 `.llm-wiki/memory/raw-hashes.json`）；`defer <raw…> [--undo]`（暫不 ingest，不再出現在新來源提示）；`hook <session-start｜post-edit｜pre-write｜session-end>` 由 `.claude/settings.json` 呼叫，從 stdin 讀 hook JSON |
| 敏感資料（schema §10） | `uv run python -I tools/redact.py scan <路徑…> [--json]`：PDF 經 docsrc 讀文字，exit 1 表示有 `block` 級命中；`mask <檔> --out <檔>` 把 `block` 級命中換成 `[REDACTED:種類]`（不能寫進 `raw/`） |
| 協作（schema §10） | `uv run python -I tools/work.py claim <路徑…> --by <名稱> [--ttl 分鐘] [--note …]`（全部成功或全部不認領）、`release`、`status`、`snapshot <名稱>`（記下整個 repo 每個檔案的大小與修改時間，排除 `.git`、`.venv`、工具快取）、`changed <名稱> [--mine <路徑…>] [--allow <資料夾…>]`（准許寫入的資料夾列在 `allowed`，其他差異列在 `others`） |
| 信心分數（schema §9） | `uv run python -I tools/confidence.py <頁…> [--below X] [--json]`：每行「分數、標籤、頁:行、論點開頭」；`--summary [頁…]` 每頁一行（平均分數、論點數、低於 0.5 的條數），不給頁面時跑全部內容頁。只讀，不存檔 |
| 取代與過時（schema §9） | lint 的 `stale_pages` 列出引用了已被取代或過期來源的頁（狀態清單，不算缺陷）；`supersession_problems` 檢查「已被取代的說法」小節每條的日期與 source 頁連結 |
| 頁面關係（schema §3 relations：頂層的 `requires`、`extends`、`alternative_to`、`contradicts`） | `uv run python -I tools/wiki.py related <頁> [--depth N] [--type requires …]`：從一頁沿關係往外走，兩個方向都列（反方向標成 `required_by`、`extended_by`）；每行是「層數、關係、頁、標題、經由哪一頁」。lint 的 `relation_problems` 檢查關係的格式（舊的巢狀 `relations:` 報成 `nested_relations`）、頁面類型、目標、內文連結、雙向、`contradicts` 兩頁都是 disputed（轉換前也接受舊值 needs-review），以及 `requires` 循環 |
| 研究系譜（ingest 第 7 步產生） | `uv run python -I tools/wiki.py lineage [主題…]`：從各 source 頁「研究脈絡」的承先產生 `wiki/meta/lineage/<主題>.md`（不給主題就產生全部）；範圍是主題頁「## 來源」列的 source 頁，主題外的頁只往外畫一層。承先寫成 `- 接續 [A](…)：卡在 X（p.N）→ 本文改為 Y（p.N）` 或 `- 沿用 [A](…)：本文沿用它的 Z（p.N）` 時，箭頭附這段說明；其他連結標「無說明」。lint 的 `lineage_stale` 只檢查已經存在的系譜檔是否過期；`lineage_newer_targets` 列出承先連到年份較新的頁；`lineage_format` 檢查 `created` 在 `tools/lineage.py` 的 `FORMAT_SINCE` 以後的 source 頁，承先是否照固定寫法 |
| log 封存（schema §2） | `uv run python -I tools/wiki.py log-archive [--keep 60] [--apply]`：較舊的紀錄依月份原文搬到 `wiki/meta/log-archive/YYYY-MM.md`，`log.md` 留最新 60 筆，最後一筆 lint 和它之後的紀錄一律留下（lint 用它算 ingest 次數與下一次的編號）；預設只印計畫；寫入前先在記憶體核對每筆紀錄都還在，有讀不懂的 `## [` 標題就拒絕、什麼都不寫；封存檔已有的紀錄不再寫一次，所以中斷後重跑不會重複。`log` 在超過 150 筆時會提示 |
| log 與 index 檢查（schema §2） | `uv run python -I tools/wiki.py log-check [--json]`、`uv run python -I tools/wiki.py index-check [--json]`：每項一行 `<代號>	<違規|提醒>	<檔案>:<行號>	<說明>`，`--json` 輸出清單；有違規 exit 1，只有提醒或沒問題 exit 0，沒問題印一行通過。log-check 只對 `wiki/log.md` 檢查格式（L1：標題、類型、內文行數）與日期順序（L2）；封存檔只參與 L3 重複與日期銜接、L4 最新 lint 要在 log.md（超過 150 筆提醒封存）、L5 lint「第 N 次」編號連續不重複（只看阿拉伯數字、標題只有「第 N 次」的紀錄）；L6 提醒內文提到的 `.llm-wiki/review/` 報告不存在；L7 提醒 `docs(wiki): ingest／lint` commit 沒改到 `wiki/log.md`（標題含「補正」「修正」豁免）。index-check：I1 總 index 只連 overview 與子 index；I2 子 index 只連 topics/、synthesis/ 與總 index；I3 總 index 的「N 個主題」「N 篇跨主題整合」等於實際數量；I4 子 index 的主題連結文字等於主題頁 `title`；I5 成員 8 個以上的主題頁要有 3 到 6 項、都是成員的「## 先讀」 |
| 原始檔連結 | `uv run python -I tools/wiki.py link-raw [頁…]`：把 source 頁裡行內程式碼的 `raw/sources/<檔>` 轉成相對連結（省略頁面＝全部 source 頁；檔案不存在的不轉） |
| 頁面 `status` 五種值（schema §3） | `uv run python -I tools/status_migrate.py [--apply]`：把舊的 `draft`｜`needs-review` 轉成 `active`｜`draft`｜`disputed`｜`superseded`｜`archived`。有出處（或 topic 頁）的 `draft` 轉 `active`、`needs-review` 轉 `disputed`、帳本標為 superseded 的 source 頁轉 `superseded`（並補 `superseded_by`）；預設 dry-run，印每頁「舊值 -> 新值」與各類計數、例外頁；`--apply` 只改 `status` 那一行，不動 `updated` 與內文。轉換前 lint 的 `legacy_status` 列出待轉的頁 |
| 依領域分目錄（schema §8） | `uv run python -I tools/migrate_domains.py [--apply]`：依 frontmatter `domain` 把扁平的 `wiki/<類型>/` 搬到 `wiki/<domain>/<類型>/`，改寫相對連結、`sources`、畫布路徑與帳本，並拆出子 index；預設只印計畫。已分目錄時會拒絕 |
| 搬移或改名頁面 | `uv run python -I tools/move_pages.py <舊> <新> [<舊> <新> …] [--apply]`；批次用 `--map <TSV>`（每行「舊<TAB>新」）；換領域用 `--to-domain <領域> <頁…>`（領域不存在時會建立）。`git mv` 後改寫所有相對連結、frontmatter `sources`、畫布路徑與帳本，跨領域時自動改 `domain`；`wiki/log.md` 不動。預設只印計畫。主題頁的歸屬不會自動調整，跨領域搬移後要檢查 |

## 簡報（/wiki-slides）
| 用途 | 指令 |
|---|---|
| 檢查大綱 | `uv run python -I tools/slides.py check slides/<concept>/outline.md`：版型與必填欄位、圖檔存在、出處連結與標題錨點、公式（含 `$…$` 行內公式）能轉換、`$…$` 外面與備忘稿沒有 LaTeX、沒有打錯的欄位名、`焦點: x0, y0, x1, y1` 只能用在有圖、沒有影片的頁且座標在 0–1、前小後大（build 時框外蓋半透明白、畫深紅框線，不產生新圖檔）、名詞先定義再使用（frontmatter `名詞: {頁序: [名稱, 別名…]}`；只比對投影片上看得到的欄位，不含備忘稿與出處；英文名稱整個字相符、不分大小寫；定義頁本身要出現該詞）、`（p.N）` 只接在 source 頁連結後面且頁碼在原始 PDF 頁數內（原始檔不是單一 PDF 時只警告）、`反白` 的每一項都是該頁的要點；frontmatter 有 `文法: <名稱>` 時，另依 `.claude/skills/wiki-slides/references/grammars/<名稱>.yaml` 檢查講課結構（規則 S1、S3–S11，見同資料夾上一層的 `slide-grammar.md`），cover、roadmap、divider 不必附出處；「警告：」不影響結束碼；exit 0 表示通過。`check --sequence <序列.yaml>` 改為檢查一份參考序列本身的結構 |
| 和參考序列比對 | `uv run python -I tools/slides.py compare <outline> <序列.yaml> [--chapters a,b]`：章用 id 對應；每章的頁型序列先把漸進群組算一頁、拿掉自由與開頭頁型、依 `same_as` 換算（例如 ablation 算 result）、合併連續重複。要的章都在、順序和參考相同、每章序列相同才通過（exit 0）；`--chapters` 預設是參考序列的每一章。參考序列在 `.claude/skills/wiki-slides/references/sequences/`，不進公開版 |
| 原始頁影像 | `uv run python -I tools/slides.py pages <outline> --out <資料夾> [--dpi 80]`：把每個 `（p.N）` 引用的原始 PDF 頁存成 `<PDF 檔名>-pNNN.png`（已存在的不重做），給 `slide-reviewer` 看；找不到 PDF 或頁碼超出時 exit 1 |
| 抽樣核對 | `uv run python -I tools/slides.py sample <outline> [--n 10] [--seed 0] [--pages <資料夾>]`：從有出處的頁隨機抽 n 頁，印頁序、標題、主張與出處；給 `--pages` 時另印原頁影像路徑。同一個 seed 抽到同樣的頁 |
| 參考序列初稿 | `uv run python -I tools/slide_refseq.py draft <講義.pdf> --out <初稿.yaml> [--sheet <資料夾>] [--ignore <regex>]… [--threshold 0.035] [--dpi 20]`：把每頁轉成低解析度灰階圖，和前面某一頁相似（平均像素差小於 threshold）的頁併成一組，標題相同的頁另成一組，寫成初稿（`type` 留空）；`--sheet` 另存每組的縮圖總表；`--ignore` 的行不當標題（例如課號頁首）。自動分組會誤併章節分隔頁與 spotlight 頁，所以初稿放 `.llm-wiki/work/`，使用者逐頁確認頁型後才存進 `sequences/` |
| 產生 .pptx | `uv run python -I tools/slides.py build slides/<concept>/outline.md [-o 檔案] [--template x.potx]`：先跑檢查，有問題就不產生；預設輸出 `slides/<concept>/<concept>.pptx` |
| 匯出檢查圖 | `uv run python -I tools/slides.py render <pptx> [--out 資料夾] [--pdf [檔案]] [--outline 大綱]`：用 PowerPoint 把每頁匯出成 `render/slide-NN.png`；逾時代表 PowerPoint 跳出修復對話框（檔案格式有誤）。`--pdf` 另外匯出 PDF（預設和 pptx 同名），並核對 PDF 頁數等於大綱頁數（大綱預設是 pptx 旁的 `outline.md`），不同時 exit 1 |
| 核對反白 | `uv run python -I tools/slides.py highlights <pptx> <outline>`：讀 pptx 每個要點的粗體與顏色（不看像素），反白的要點要剛好等於大綱的 `反白`；不一致時逐頁列出、exit 1 |
| 做動畫 | `uv run python -I tools/slides.py animate slides/<concept>/animations/<名稱>.py`：用 Manim（3Blue1Brown 的動畫套件）把動畫原始檔（一個檔案只放一個 `Scene`）轉成同名的 `.mp4`，並把最後一格存成同名 `.png` 當封面。Manim 跑在專案外的暫用環境（uv、Python 3.12、manim 0.22.1），第一次會下載套件；需要 ffmpeg。沒有裝 LaTeX，所以動畫裡的字只能用 `Text`，不能用 `MathTex`、`Tex`、`DecimalNumber` |
| 試轉一條公式 | `uv run python -I tools/slides.py formula "<latex>"`：印出 OMML，或印出轉換失敗的原因 |
| 畫自繪圖 | `uv run python -I tools/diagram.py draw <spec.json>…`：用 open-diagram 讀 JSON 規格（`panels`、`links` 等），以微軟正黑體畫成規格檔旁的同名 `.png`，並裁掉白邊；需要 Edge 或 Chrome（環境變數 `CHROME` 可指定路徑）。`check <spec.json>…` 只檢查版面（重疊、字太小）不寫檔。有版面問題時 exit 1、不寫圖。每張圖另印「頁面字級」：放在圖片版型「有要點」（約 7.7 吋寬）與「全寬」（約 12.4 吋寬）版位時最小字的 pt 數（寬高都受限、以有重點句與圖說的頁計），低於 10 pt 印警告，不影響結束碼 |

公式先用 `latex2mathml` 轉成 MathML，再用 Office 內建的 `MML2OMML.XSL` 轉成 PowerPoint 原生方程式，所以需要裝 Office（路徑不同時用環境變數 `MML2OMML_XSL` 指定）。字體：英文 Helvetica、中文微軟正黑體、公式 Cambria Math；沒裝 Helvetica 的電腦會用替代字顯示。版面仿 MIT EfficientML 講義：深紅標題與左側色條、標題下的粗體重點句、符號表、右下角小字出處、底部深紅色帶（左邊 frontmatter 的 `footer`，右邊頁碼）。純文字欄位可用 `$…$` 寫行內公式。流程圖版型把 `流程` 欄位的箭頭鏈畫成 PowerPoint 原生的圓角方塊與箭頭：節點依最長路徑由左到右分欄，跨欄的箭頭在中間每一欄佔一個路過點、畫成折線；同一張圖的方塊用同一個字級，`check` 估算節點文字、英文單字（只在連字號後換行）與箭頭文字是否放得下。

## 外部 agent（agy）
只在使用者要求時用（例如 Claude 額度不足）。`agy -p` 不能詢問權限，會拒絕所有 shell 指令，所以改用這個工具透過 ACP 跑 agy：
| 用途 | 指令 |
|---|---|
| 跑一個 agy 任務 | `uv run python -I tools/agy_acp.py --prompt-file <說明檔> --write <資料夾> [--write …] [--allow-cmd <前綴>] [--model gemini-4-neon-high] [--out 回報檔] [--log 紀錄檔] [--timeout 秒]`：工具代答 agy 的每個權限請求。指令：整行只能用英數字、空白與 `_ - . / : = ,`（引號、括號、`$ % ^ @`、`..`、控制字元一律拒絕），開頭要是 `uv run python -I tools/` 的 `doc.py`、`pdf_tool.py`、`slides.py` 或 `--allow-cmd` 的前綴，工作目錄要是專案根目錄；`pdf_tool.py` 的 shot／figures／split 一定要帶 `--out`，`--out`／`-o` 的值與 `slides.py build/render` 的路徑要在 `--write` 資料夾內，其他 `-o`、`--o` 開頭的選項（argparse 縮寫）一律拒絕。寫檔：請求裡每個路徑欄位都要在 `--write` 資料夾內。讀檔與搜尋：限專案內，`.env`、`.mcp.json`、`settings.local.json` 不給讀。網路、刪除等其他類型一律拒絕。agy 遇到拒絕會結束這一輪，工具會在同一個 session 告訴它被拒的動作與原因，請它繼續，最多 5 次；最後仍有被拒的動作時 exit 1。紀錄檔每行是一個 ALLOW／DENY 決定；`--out` 收每一輪的回報 |

主對話不必自己跑這個指令：寫好說明檔（`.llm-wiki/extract/<名稱>/agy-prompt.md`）後派 `agy-runner`（`.claude/agents/agy-runner.md`，Haiku），prompt 給名稱、說明檔、`--write` 資料夾、任務類型（`閱讀`／`改寫`／`簡單`），選填已用完的額度組別（`argon`、`neon`＝Neon 與 Hydrogen、`claude`）與 `--allow-cmd`／`--model`／`--timeout`。模型依任務類型的候選表自動選（表在 agent 檔），額度用完時停下回報，不自動換模型。它存快照、在背景跑工具並等到結束（最多 2 小時，所以 `--timeout` 上限 7000 秒），回報結束碼、紀錄檔裡 `ALLOW [edit]`／`ALLOW [execute]` 與 DENY 的原文（`.llm-wiki/` 不進 git，agy 在那裡寫的檔只看得到這幾行），以及 `wiki/` 與 git 狀態的差異；agy 的回報在 `agy-out.md`，由主對話自己讀。

注意：agy 不會為每次讀檔送權限請求，所以讀檔範圍實際上由 agy 自己決定；寫入範圍仍要靠派工前的 `work.py snapshot` 比對確認。agy 的產出照 `docs/dispatch.md`「通用規則」驗收。

## 公開版
| 用途 | 指令 |
|---|---|
| 產生空白模板 | `uv run python -I tools/public_export.py <目標資料夾> [--ref HEAD] [--replace] [--commit <訊息>]`：從一個 commit 匯出（只讀已 commit 的檔案）空白的模板。只複製 `ALLOWED` 列出的規則、skill、agent、hook、工具與測試，不複製 `wiki/`、`slides/`、`PURPOSE.md`、`README.md` 等內容；再把 `template/` 的空白檔案蓋上去（`template/x` 變成 `x`）。寫入前掃描洩漏：這個 wiki 的頁名、頁面標題、別名、來源頁去掉年份與作者後的短名、領域名（整個字，不分大小寫）、來源檔名、信箱與本機路徑出現在任何檔案時 exit 1，不寫入。有沒歸類的檔案時也 exit 1（加進 `ALLOWED` 或 `EXCLUDED`）。目標資料夾要是空的或只有 `.git`，而且不能是這個 repo、在它裡面或包含它；`--replace` 先清掉 `.git` 以外的全部檔案。`--commit` 在目標 repo commit（`user.email` 要是 GitHub noreply 信箱），不 push |

## 注意
- 不要臨時寫 pymupdf 或其他腳本（hook 會擋）；工具不夠用時擴充工具，見 `docs/development.md`
