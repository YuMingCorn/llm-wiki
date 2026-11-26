# 開發 tools/

修改或新增 `tools/` 的程式時讀這份。

- 工具不夠用時先擴充工具，不要每次臨時寫腳本
- 風格：Google Python Style，`uv run ruff check`
- 測試：`uv run pytest -q`
- `-I` 會忽略環境變數，所以 `tools/` 的工具要在 `main()` 自己呼叫 `sys.stdout.reconfigure(encoding="utf-8")`，新工具照做
- Bash 的 `python` 是空殼，一律用 `uv run python`
- 資源上限：Python 最多 4 個執行緒、記憶體最多 8 GB。8 GB 是每次執行的整個行程樹（含它開的子行程），不是同時跑的多個 Python 加總；同時跑兩個重工作（例如兩個向量索引）會各有 8 GB，要避免。`tools/_limits.py` 在 import 時套用（數值庫的執行緒環境變數，Windows 用 Job Object 限制整個行程樹的記憶體）；`_paths`、`_common`、`docsrc` 會帶入它，新工具在 import numpy 等原生程式庫前 `import _limits`。自己開執行緒池的程式庫（onnxruntime、fastembed）要明確傳 `_limits.THREADS`；大量計算要分小批次，不要靠上限擋
- 新增的 agent（`.claude/agents/`）要重開 session 才會載入
