# HackMD 操作

動到 HackMD（pull／push）時讀這份。可以推上 HackMD 的範圍見 `SCHEMA.md` §1，筆記擺放規則見 `hackmd_note/README.md`。

## 規則
- **`wiki/` 不上 HackMD**；唯一可以推上 HackMD 的是 `hackmd_note/` 目錄裡的檔案
- 每次推送前，先在對話中貼出 diff（寫入工具都會跳確認框，確認框只顯示路徑）
- HackMD API 有次數限制：只拉人類指定的筆記，不做整批掃描
- 不使用 `discard_local_changes: true`，除非使用者明確同意
- 資訊只從 wiki 流向筆記；筆記內容要進 wiki，必須由使用者提供原始來源走 `/wiki-ingest`

## MCP 設定
- 設定在 `.mcp.json`（含 token，已 gitignore）
- pull／push 的本機路徑必須在 repo 資料夾底下，且用絕對路徑
- HackMD 筆記拉到 `hackmd_note/`，依 HackMD 資料夾結構擺放

## HackMD 權限（`.claude/settings.local.json`）
- 免授權（讀）：`get_me`、`list_folders`、`list_notes`、`get_note`、`pull_note`
- 每次跳確認框（寫）：`push_note`、`update_note`、`create_note`、`delete_note`、`upload_note_image`、資料夾的 create／update／delete、`untrack_note`
