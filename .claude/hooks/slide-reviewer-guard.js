// PreToolUse hook，只掛在 subagent（.claude/agents/slide-reviewer.md）上：
// 審查者可以讀整個專案（大綱、wiki 頁、原頁影像），但只能寫自己的 review.tsv。
// - Edit／Write／NotebookEdit／MultiEdit：只能寫 .llm-wiki/extract/<名稱>/review.tsv
// - mcp__*：一律擋；其他工具由 agent 的 tools 清單限制
// 路徑先解析符號連結、轉小寫（Windows 不分大小寫），結尾帶點或 8.3 短名（~）一律擋。
// 擋下時 exit 2，stderr 告訴 subagent 停下來回報，不要換方法繞過。
const fs = require('fs');
const path = require('path');

function block(msg) {
  process.stderr.write(`[slide-reviewer-guard] ${msg}\n停下來回報主對話，不要換方法繞過。`);
  process.exit(2);
}

const REVIEW = /^\.llm-wiki\/extract\/[^/]+\/review\.tsv$/;

function real(p) {
  let head = p, tail = '';
  while (!fs.existsSync(head)) {
    const parent = path.dirname(head);
    if (parent === head) return p;
    tail = path.join(path.basename(head), tail);
    head = parent;
  }
  return path.join(fs.realpathSync.native(head), tail);
}

function rel(root, file) {
  const r = path.relative(real(path.resolve(root)), real(path.resolve(root, file || '.')))
    .replace(/\\/g, '/').toLowerCase();
  if (r.startsWith('..') || path.isAbsolute(r)) block(`不准寫專案外的 ${r}。`);
  if (r.split('/').some((s) => s.endsWith('.') && s !== '.' || s.includes('~'))) {
    block(`路徑寫法不明確：${r}。`);
  }
  return r;
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
    const r = rel(root, ti.file_path || ti.notebook_path || '');
    if (!REVIEW.test(r)) block(`不准寫入 ${r}；只能寫 .llm-wiki/extract/<名稱>/review.tsv。`);
  }
  process.exit(0);
});
