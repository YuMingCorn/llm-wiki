# hackmd_note — HackMD 筆記本機鏡像（選用）

從 HackMD 拉下來（`hackmd_pull_note`）的個人筆記放在這裡，目錄結構對應 HackMD 上的資料夾。這個資料夾裡只有這份 README 進 git（見 `.gitignore`）。不用 HackMD 的話，可以忽略這個資料夾。

## 擺放規則
- 筆記放進與它在 HackMD 上**所屬資料夾**對應的目錄（看 note 的 `folder_ids` / `parent_folder_id`）
- 沒有資料夾的筆記直接放在 `hackmd_note/` 根目錄
- 檔名：`<HackMD 標題>.md`，Windows 不允許的字元換成全形（`/`→`／`、`|`→`｜`、`:`→`：`、`?`→`？`、`*`→`＊`、`"`→`＂`、`<>`→`＜＞`）
- pull 一律使用絕對路徑，而且必須在 repo 資料夾底下
- HackMD 新增或改名資料夾時，這裡跟著建立或改名
- 對應本機 `wiki/` 的 HackMD 資料夾**不**放這裡（見 `SCHEMA.md` §6）
