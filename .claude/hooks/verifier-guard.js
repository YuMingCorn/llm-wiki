// PreToolUse hook，只掛在 subagent（.claude/agents/source-verifier.md）上，強制禁區：
// - Edit／Write／NotebookEdit：只能寫 .llm-wiki/extract/<名稱>/verify.md
// - Bash／PowerShell：白名單。唯讀指令、git 的唯讀子指令、uv run python tools/doc.py；
//   不准重導向寫檔、heredoc、指令替換、git 寫入
// - mcp__*：一律擋
// 擋下時 exit 2，stderr 告訴 subagent 停下來回報，不要換方法繞過。
const path = require('path');

function block(msg) {
  process.stderr.write(`[verifier-guard] ${msg}\n停下來回報主對話，不要換方法繞過。`);
  process.exit(2);
}

// 依 ; & | 換行切段，引號內不切；回傳每段的字詞（去掉引號）
function segments(cmd) {
  const out = [];
  let words = [], word = '', quote = null, inWord = false;
  const endWord = () => { if (inWord) words.push(word); word = ''; inWord = false; };
  const endSeg = () => { endWord(); if (words.length) out.push(words); words = []; };
  for (const ch of cmd) {
    if (quote) {
      if (ch === quote) quote = null; else word += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch; inWord = true;
    } else if (';&|\n'.includes(ch)) {
      endSeg();
    } else if (/\s/.test(ch)) {
      endWord();
    } else {
      word += ch; inWord = true;
    }
  }
  endSeg();
  return out;
}

const READ_ONLY = new Set([
  'ls', 'cat', 'head', 'tail', 'wc', 'grep', 'rg', 'sed', 'sort', 'uniq', 'diff', 'echo',
  'printf', 'pwd', 'cd', 'test', 'true', 'jq', 'find', 'cut', 'tr', 'basename', 'dirname',
  'Get-Content', 'Get-ChildItem', 'Select-String', 'Test-Path', 'Measure-Object',
  'Select-Object', 'Where-Object', 'Format-List', 'Format-Table', 'Write-Output',
]);
const GIT_READ = new Set(['status', 'diff', 'log', 'show', 'rev-parse', 'ls-files', 'blame', 'grep']);
const TOOLS = new Set(['doc.py']);

function checkPython(words) {
  // words: python 之後的字詞
  let i = 0;
  while (i < words.length && words[i].startsWith('-')) {
    if (words[i] === '-c' || words[i] === '-') block('不准執行臨時 python 程式碼，只能用 tools/ 的工具。');
    i += words[i] === '-X' ? 2 : 1;
  }
  const script = (words[i] || '').replace(/\\/g, '/');
  const m = script.match(/^(?:\.\/)?tools\/([\w.]+\.py)$/);
  if (!m || !TOOLS.has(m[1])) block(`不准執行 ${script || 'python'}；允許的工具：${[...TOOLS].join(', ')}`);
}

function checkShell(cmd) {
  const unquoted = cmd.replace(/'[^']*'/g, "''");
  if (/\$\(|`/.test(unquoted)) block('不准用指令替換（$( ) 或反引號）。');
  if (/<</.test(unquoted)) block('不准用 heredoc；要寫檔用 Edit／Write 工具。');
  const SAFE_REDIRECT = /\d?>&\d|\d?>\s*(?:\/dev\/null|\$null)(?![\w/])/g;
  const bare = unquoted.replace(/"(?:[^"\\]|\\.)*"/g, '""').replace(SAFE_REDIRECT, '');
  if (/>/.test(bare)) block('不准用重導向寫檔；要寫檔用 Edit／Write 工具。');

  for (let words of segments(cmd.replace(SAFE_REDIRECT, ' '))) {
    while (words.length && /^[A-Za-z_]\w*=/.test(words[0])) words = words.slice(1);
    if (!words.length) continue;
    const [head, ...rest] = words;
    if (head === 'git') {
      const sub = rest.find((w) => !w.startsWith('-'));
      if (!GIT_READ.has(sub)) block(`不准 git ${sub || ''}；commit／add／push 等只由主對話做。`);
      continue;
    }
    if (head === 'uv' && rest[0] === 'run' && /^python(?:3)?(?:\.exe)?$/.test(rest[1] || '')) {
      checkPython(rest.slice(2));
      continue;
    }
    if (head === 'sed' && rest.some((w) => /^-[a-zA-Z]*i/.test(w) || w === '--in-place')) {
      block('不准 sed -i；要改檔用 Edit 工具。');
    }
    if (head === 'find' && rest.some((w) => ['-exec', '-execdir', '-delete', '-ok'].includes(w))) {
      block('find 不准帶 -exec／-delete。');
    }
    if (!READ_ONLY.has(head)) block(`不准執行 ${head}；只能用唯讀指令和 tools/ 的工具。`);
  }
}

// 只准寫 .llm-wiki/extract/<名稱>/verify.md
function checkWrite(file, root) {
  const rel = path.relative(root, path.resolve(root, file)).replace(/\\/g, '/');
  if (!/^\.llm-wiki\/extract\/[^/]+\/verify\.md$/.test(rel)) {
    block(`不准寫入 ${rel}；只能寫 .llm-wiki/extract/<名稱>/verify.md。`);
  }
}

let buf = '';
process.stdin.on('data', (c) => (buf += c));
process.stdin.on('end', () => {
  let input;
  try {
    input = JSON.parse(buf);
  } catch {
    block('hook 讀不到輸入。');
  }
  const root = process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd();

  const tool = input.tool_name || '';
  const ti = input.tool_input || {};
  if (tool.startsWith('mcp__')) block(`不准使用 ${tool}。`);
  if (['Edit', 'Write', 'NotebookEdit', 'MultiEdit'].includes(tool)) {
    checkWrite(ti.file_path || ti.notebook_path || '', root);
  } else if (tool === 'Bash' || tool === 'PowerShell') {
    checkShell(ti.command || '');
  }
  process.exit(0);
});
