// PreToolUse hook，只掛在 subagent（.claude/agents/concept-reader.md）上：
// 它扮演只讀 wiki 的學生，看不到原文與 source 頁，只能寫自己的 student.md。用白名單：
// - Read：wiki/ 底下（sources/ 除外）、GLOSSARY.md、PURPOSE.md、concept-writing.md、student.md
// - Grep／Glob：path 必填，且在 wiki/<domain>/ 的非 source 目錄底下；pattern 與 glob 不准跳目錄
// - Edit／Write／NotebookEdit：只能寫 .llm-wiki/extract/<名稱>/student.md
// - matcher 只攔上面這些工具；其他工具由 agent 的 tools 清單限制（交報告的工具不能擋）
// 路徑先解析符號連結、轉小寫（Windows 不分大小寫），結尾帶點或 8.3 短名（~）一律擋。
// 擋下時 exit 2，stderr 告訴 subagent 停下來回報，不要換方法繞過。
const fs = require('fs');
const path = require('path');

function block(msg) {
  process.stderr.write(`[reader-guard] ${msg}\n停下來回報主對話，不要換方法繞過。`);
  process.exit(2);
}

const STUDENT = /^\.llm-wiki\/extract\/[^/]+\/student\.md$/;
const READ_FILES = new Set(['glossary.md', 'purpose.md',
  '.claude/skills/wiki-ingest/references/concept-writing.md']);
const SEARCH_DIRS = /^wiki\/[^/]+\/(concepts|entities|comparisons|synthesis|topics|queries)(\/|$)/;

function real(p) {
  // 解析既有部分的符號連結；檔案不存在時往上找到存在的祖先再接回去
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
  if (r.startsWith('..') || path.isAbsolute(r)) block(`不准碰專案外的 ${r}。`);
  if (r.split('/').some((s) => s.endsWith('.') && s !== '.' || s.includes('~'))) {
    block(`路徑寫法不明確：${r}。`);
  }
  return r;
}

function checkRead(file, root) {
  const r = rel(root, file);
  if (STUDENT.test(r) || READ_FILES.has(r)) return;
  if (/^wiki\//.test(r) && !/^wiki\/[^/]+\/sources(\/|$)/.test(r)) return;
  block(`學生只能讀 wiki 的非 source 頁、GLOSSARY.md、PURPOSE.md、concept-writing.md；不准讀 ${r}。`);
}

function checkSearch(ti, root, tool) {
  if (!ti.path) block(`${tool} 要指定 path，例如 wiki/<領域>/concepts。`);
  const r = rel(root, ti.path);
  if (!SEARCH_DIRS.test(r)) block(`${tool} 的 path 只能在 wiki/<domain>/ 的 concepts、entities、comparisons、synthesis、topics、queries 底下。`);
  for (const p of [ti.glob, tool === 'Glob' ? ti.pattern : null]) {
    if (p && (/\.\./.test(p) || /^[\\/]|^[a-z]:/i.test(p))) block(`${tool} 的 pattern 不准跳出 path：${p}。`);
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
  if (tool === 'Read') {
    checkRead(ti.file_path, root);
  } else if (tool === 'Grep' || tool === 'Glob') {
    checkSearch(ti, root, tool);
  } else if (['Edit', 'Write', 'NotebookEdit', 'MultiEdit'].includes(tool)) {
    const r = rel(root, ti.file_path || ti.notebook_path || '');
    if (!STUDENT.test(r)) block(`不准寫入 ${r}；只能寫 .llm-wiki/extract/<名稱>/student.md。`);
  } else {
    block(`不准使用 ${tool}；學生只能 Read、Grep、Glob 與寫 student.md。`);
  }
  process.exit(0);
});
