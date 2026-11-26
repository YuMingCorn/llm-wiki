// PreToolUse hook（Bash／PowerShell）：重複性操作改用專案工具。
// 1. 直接追加 wiki/log.md（>>、tee -a/--append、Add-Content、Out-File -Append）→ tools/wiki.py log
// 2. 臨時 python -c / python - <<heredoc 裡 import pymupdf（fitz）→ tools/doc.py grep/show
// 只看「會被執行的部分」：heredoc 內文與含空白的引號字串（commit 訊息、log 內文）不比對規則 1；
// 3. 刪除、搬移、覆寫 raw/ 底下的檔案（rm、mv、sed -i、> 重導向、Remove-Item…）→ 擋下，raw/ 唯讀
//    （複製新檔進 raw/ 不擋：raw/ 只增不改。使用者自己用 ! 執行的指令不經過 hook）
// 規則 2 只檢查 -c 的程式碼與餵給 python 的 heredoc。這是導向用，不是安全邊界。
let buf = '';
process.stdin.on('data', (c) => (buf += c));
process.stdin.on('end', () => {
  let cmd = '';
  try {
    cmd = JSON.parse(buf).tool_input?.command ?? '';
  } catch {
    process.exit(0);
  }

  // 拆出 heredoc：記下 << 所在那一行與內文，並從指令中移除內文
  const heredocs = [];
  const HEREDOC = /^([^\n]*?)<<-?\s*(['"]?)([A-Za-z_]\w*)\2[^\n]*\n([\s\S]*?)\n[ \t]*\3[ \t]*(?=\n|$)/gm;
  const shell = cmd.replace(HEREDOC, (m, head, _q, _tag, body) => {
    heredocs.push({ head, body });
    return head;
  });
  // 含空白的引號字串視為文字（訊息、內文），不是路徑
  const code = shell.replace(/"(?:[^"\\]|\\.)*\s(?:[^"\\]|\\.)*"|'[^']*\s[^']*'/g, '""');

  const LOG = String.raw`["']?[^\s"'|;&]*wiki[\\/]log\.md["']?(?![\w.])`;
  const appendsLog = [
    new RegExp(String.raw`>>\s*` + LOG),
    new RegExp(String.raw`\btee\b[^|;&\n]*?\s(?:-a|--append)\b[^|;&\n]*?\s` + LOG),
    new RegExp(String.raw`\btee\b[^|;&\n]*?\s` + LOG + String.raw`[^|;&\n]*?\s(?:-a|--append)\b`),
    new RegExp(String.raw`\bAdd-Content\b[^|;&\n]*?` + LOG, 'i'),
    new RegExp(String.raw`\bOut-File\b(?=[^|;&\n]*-Append\b)[^|;&\n]*?` + LOG, 'i'),
  ].some((re) => re.test(code));

  const usesPdfLib = (src) =>
    src.split(/[;\n]/).some((stmt) =>
      /^\s*import\s+([\w.]+\s*,\s*)*(fitz|pymupdf)\b/.test(stmt) ||
      /^\s*from\s+(fitz|pymupdf)\s+import\b/.test(stmt) ||
      /__import__\(\s*['"](fitz|pymupdf)['"]/.test(stmt));
  const PY = String.raw`\bpython3?(?:\.exe)?(?:\s+-[A-Za-z]\w*)*?`;
  const inlineCode = [...shell.matchAll(new RegExp(PY + String.raw`\s+-c\s+("(?:[^"\\]|\\.)*"|'[^']*')`, 'g'))]
    .map((m) => m[1].slice(1, -1));
  const stdinScripts = heredocs
    .filter((h) => new RegExp(PY + String.raw`(?:\s+-)?\s*$`).test(h.head.replace(/\s*$/, '')))
    .map((h) => h.body);
  const inlinePdf = [...inlineCode, ...stdinScripts].some(usesPdfLib);

  const RAW = String.raw`(?:^|[\s"'=(])(?:[^\s"'|;&<>]*[\\/])?raw[\\/]`;
  const segments = code.split(/&&|\|\||[;&|\n]/);
  const MUTATE = /^\s*(?:sudo\s+)?(?:rm|rmdir|unlink|shred|truncate|mv|del|erase|ri|rni|mi|Remove-Item|Move-Item|Rename-Item|Set-Content|Add-Content|Clear-Content|Out-File)(?=\s|$)/i;
  const INPLACE = /^\s*(?:sed|perl)\b[^\n]*?\s-[A-Za-z]*i/;
  const writesRaw =
    new RegExp(String.raw`>{1,2}\s*["']?(?:[^\s"'|;&<>]*[\\/])?raw[\\/]`, 'i').test(code) ||
    segments.some((s) => new RegExp(RAW, 'i').test(s) &&
      (MUTATE.test(s) || INPLACE.test(s) || /^\s*tee\b/.test(s)));

  const rules = [
    {
      hit: writesRaw,
      msg:
        'raw/ 是唯讀的原始資料（schema §1），不要用 shell 指令刪除、搬移或覆寫裡面的檔案。\n' +
        '真的要改（例如改檔名、刪重複檔），請把指令交給使用者，由使用者用 ! 自己執行。',
    },
    {
      hit: appendsLog,
      msg:
        '不要直接追加 wiki/log.md。改用：\n' +
        "  uv run python -I tools/wiki.py log <ingest|lint|schema> \"<標題>\" <<'EOF'\n" +
        '  - 內文\n  EOF\n（自動加日期、只追加；之後用 tools/wiki.py commit）',
    },
    {
      hit: inlinePdf,
      msg:
        '不要臨時寫 pymupdf 腳本讀 PDF。改用：\n' +
        '  uv run python -I tools/doc.py grep "<regex>" <檔案或目錄…> [-i] [-C 2] [--pages 3-8]\n' +
        '  uv run python -I tools/doc.py show <檔案> --pages 12-13\n' +
        '（切章節、截圖、圖表仍用 tools/pdf_tool.py；doc.py 不夠用時先說明原因再擴充工具）',
    },
  ];
  const r = rules.find((x) => x.hit);
  if (r) {
    process.stderr.write(r.msg);
    process.exit(2);
  }
  process.exit(0);
});
