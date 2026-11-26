# 講課結構（頁型文法）

大綱 frontmatter 寫 `文法: research` 時，`slides.py check` 會依這裡的規則檢查講課結構。沒有這一行的大綱只跑原本的檢查。機器讀的頁型表在 `grammars/<名稱>.yaml`；這份是給人看的說明，兩者不一致時以 yaml 為準，並改這份。

結構參考 MIT 6.5940（Song Han）的講義。參考序列放在 `sequences/`，是使用者確認過的頁碼與頁型，不含講義的文字和圖。

## 頁型

| 類型 | 做法 | 在哪裡 |
|---|---|---|
| `cover` | 封面 | 第一章之前 |
| `motivation` | 先展示實際應用或問題，引起動機 | 開頭；章內也可以 |
| `roadmap` | 大綱頁重複出現，反白目前的節（兩層時反白節加小節），其他灰掉 | 開頭的總覽（不反白）；每章第一頁 |
| `divider` | 章節分隔頁，只有章名 | 每章第一頁（代替 roadmap） |
| `background` | 回顧前置知識（例如位置編碼、快取） | 開頭；章內任何位置 |
| `spotlight` | 同一張架構圖重複出現，每次只亮一個元件（用 `焦點`） | 章內任何位置 |
| `figure` | 一般的圖 | 章內任何位置 |
| `prior` | 前作的做法 | 章內任何位置 |
| `headline` | 一頁講完這一章的結果，當開場 | 章內任何位置 |
| `problem` | 舊做法哪裡不夠 | 章內，順序 1 |
| `quantify` | 用公式加數字指出瓶頸 | 章內，順序 2 |
| `observation` | 實驗觀察到的現象 | 章內，順序 3 |
| `insight` | 一句洞見，加解法和示意圖 | 章內，順序 4 |
| `method` | 方法的做法與架構 | 章內，順序 5 |
| `steps` | 玩具例子，一步一頁（用 `漸進`） | 章內，順序 6 |
| `result` | 固定的評估指標，與基準方法並排 | 章內，順序 7 |
| `ablation`、`generalization` | 消融、推廣到其他設定；排順序時當作 result | 章內，順序 7 |
| `limitation` | 指出極限，轉到下一個方法 | 章內，順序 8 |
| `summary` | 依章的順序回顧 | 最後 |
| `appendix` | 附錄 | summary 之後 |

章內有順序的頁型（problem 到 limitation）只能往後走，每一型都可以跳過。例外：一章講好幾個方法元件時，可以從 method 到 result 之間回到 method 再走一輪（yaml 的 `repeat`）；limitation 之後不能回頭。不必附出處的頁型：cover、roadmap、divider。

## 大綱欄位

每頁多四個欄位，全部選填，但有 `文法` 時每頁都要寫 `類型`：

| 欄位 | 寫法 | 說明 |
|---|---|---|
| `類型` | `類型: problem` | 上表的頁型 |
| `章` | `章: streaming` | frontmatter `章` 裡的 id；第一章之前和最後一章之後的頁不寫 |
| `漸進` | `漸進: sink-steps` | 同一組漸進頁寫同一個名稱，要連續、至少 2 頁；全是 spotlight 的組不受限 |
| `反白` | `反白: Efficient Attention, AlphaCache` | 用逗號分開；每一項都要是這頁的 `要點` |

frontmatter：

```yaml
文法: research
章:
  - id: streaming
    名稱: AlphaCache
    節: Efficient Attention          # 選填；有節時 roadmap 要反白節加名稱
  - id: duo
    名稱: BetaCache
    節: Efficient Attention
    銜接: "推論：AlphaCache 對所有 head 一視同仁……（[BetaCache](../../wiki/demo/sources/….md#…)）"
  - id: beyond
    名稱: Beyond Transformers
    子節: ["State-Space Models (SSMs): Mamba", "Hybrid Models: Jamba"]   # 選填；一次講完整節時，roadmap 也要反白每個子節
    銜接: "推論：……"
```

第 2 章起要有 `銜接`：附出處連結，或以「推論：」開頭。跨來源重排時，銜接的推理也寫進講者備忘稿。

## 規則代號

| 代號 | 規則 | 嚴重度 |
|---|---|---|
| S1 | 出處：cover、roadmap、divider 以外的頁要有出處 | 錯誤 |
| S3 | `（p.N）` 只能接在 source 頁連結後面；頁碼要在原始 PDF 的頁數內 | 錯誤（原始檔不是單一 PDF 時只警告） |
| S4 | `類型` 要在頁型表裡 | 錯誤 |
| S5 | 章內頁型順序不可倒退（`repeat` 範圍內可以回到開頭） | 錯誤 |
| S6 | 每章第一頁是 roadmap 或 divider；章要連續、照 frontmatter 順序 | 錯誤 |
| S7 | 第 2 章起的銜接要有出處或標「推論：」 | 錯誤 |
| S8 | 漸進群組至少 2 頁且連續 | 錯誤 |
| S9 | 最後是 summary（後面只能接 appendix）；開頭不是 motivation 只警告 | 錯誤／警告 |
| S10 | summary 的要點依章的順序 | 錯誤（漏掉某章只警告） |
| S11 | roadmap 的反白等於所屬的節、名稱加 `子節`；開頭的總覽不反白 | 錯誤 |
