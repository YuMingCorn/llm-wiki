---
title: Purpose
updated: YYYY-MM-DD
---

# Purpose

> 這份文件定義這個 wiki「為什麼存在」。LLM 在 ingest、query、lint 時都要先讀這份，判斷什麼值得記、什麼可以略過。開始用之前，把下面每一節的（填寫）換成你自己的內容。

## 目標（Goals）

- （填寫：這個 wiki 要累積哪些知識、用來做什麼）
- 供使用者查詢、學習與整理，寫自己的筆記時參考；資訊只從 wiki 流向筆記。

## 核心問題（Key Questions）

1. （填寫：你希望這個 wiki 能回答的問題。ingest 時用來判斷一份來源值不值得記）

## 研究範圍（Scope）

**納入：**
- （填寫：主題）
- 來源類型：（填寫，例如學術論文、書籍、官方文件與技術報告、可信機構的研究報告）

**排除：**
- 沒有可查證來源的論點（可以記成「待查證」，但不寫成定論）
- （填寫）

## 領域與審閱檢查表

領域就是 `wiki/` 底下的目錄（schema §8）。新增領域的步驟見 `README.md`「建立領域」。這個 wiki 的領域：

- （填寫：`<領域>`，一句話說明）
- `cross`：跨領域的交會主題（用到時才建）

source 頁「審閱註記」的檢查表（`.claude/skills/wiki-ingest/references/source-writing.md`「檢查表」）依 `domain` 使用下列項目：

| domain | 項目 |
|---|---|
| （填寫：領域） | （填寫：審閱這個領域的來源時要檢查的事，例如樣本大小、對照組、能否重現） |

## 新增來源的節奏

使用者放進 `raw/sources/` 的來源，只要符合上面的「納入」範圍，都可以 ingest。session 開始時 hook 會列出新來源，並自動開始 ingest，停在確認點（schema §10）。不符合「納入」範圍時，在確認點告訴使用者。

想要但還沒取得的來源記在 `MISSING_SOURCES.md`（用到時再建）。

## 讀者與協作

- 本機：LLM agent（Claude Code）負責整理、交叉連結、維護一致性
- HackMD（選用）：人類閱讀、評論、協作編輯（見 `SCHEMA.md` §6）
