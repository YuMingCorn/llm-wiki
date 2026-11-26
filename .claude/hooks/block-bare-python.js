// PreToolUse hook（Bash）：擋下單獨的 python／python3。
// Git Bash 的 python 指向 WindowsApps 空殼，不執行也不報錯（memory: shell-python-stub）。
// 只看「指令位置」的 python：行首，或接在 ; & | ( ` $( 之後，可帶 VAR=x 前綴；
// `uv run python`、`.venv/Scripts/python`、引號裡的文字都不會被擋。
let buf = '';
process.stdin.on('data', (c) => (buf += c));
process.stdin.on('end', () => {
  let cmd = '';
  try {
    cmd = JSON.parse(buf).tool_input?.command ?? '';
  } catch {
    process.exit(0);
  }
  const re = /(^|[;&|(`]|\$\()\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(python3?)(?:\.exe)?(?=\s|$|;|\))/m;
  const m = cmd.match(re);
  if (m) {
    process.stderr.write(
      `已擋下單獨的 \`${m[2]}\`：這台機器 Bash 的 python 是 WindowsApps 空殼，不會執行也不會報錯。` +
        '請改用 `uv run python …`（讀外部檔案時加 -I）。'
    );
    process.exit(2);
  }
  process.exit(0);
});
