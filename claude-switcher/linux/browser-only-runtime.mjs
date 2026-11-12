// Sandbox supervisor and the transport bound to the dedicated private browser.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn, spawnSync} from 'node:child_process';
import net from 'node:net';

const base = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const manifest = JSON.parse(fs.readFileSync(path.join(base, 'config/manifest.json'), 'utf8'));
const mode = process.argv[2];
const mcpBin = path.join(base, 'runtime/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js');

function mcpArgs() {
  for (const [name, host] of Object.entries(manifest.host_namespaces)) {
    if (fs.readlinkSync('/proc/self/ns/' + name) === host) throw new Error('Required OS isolation is missing');
  }
  const endpoint = new URL(process.env.CLAUDE_BROWSER_ONLY_WS || 'invalid:');
  if (endpoint.protocol !== 'ws:' || endpoint.hostname !== '127.0.0.1' || Number(endpoint.port) !== manifest.port || !endpoint.pathname.startsWith('/devtools/browser/')) {
    throw new Error('No dedicated sandbox browser endpoint');
  }
  const roots = [path.join(base, 'state/workspace'), ...JSON.parse(process.env.CLAUDE_WORKSPACES || '[]')];
  return [mcpBin, '--wsEndpoint=' + endpoint.href, '--no-usage-statistics', '--no-performance-crux', ...roots.map(root => '--filesystem-root=' + root)];
}

function quote(value) { return "'" + value.replaceAll("'", "'\\''") + "'"; }

function mcpChild(options) {
  const env = {...process.env};
  for (const key of JSON.parse(fs.readFileSync(path.join(base, 'config/auth-keys.json'), 'utf8'))) delete env[key];
  const container = JSON.parse(fs.readFileSync(path.join(base, 'config/tool-container.json'), 'utf8'));
  return spawn(container[0], [...container.slice(1), manifest.node, ...mcpArgs()], {...options, env});
}

if (mode === 'sandbox') {
  const {SandboxManager} = await import('../runtime/node_modules/@anthropic-ai/sandbox-runtime/dist/index.js');
  const policy = JSON.parse(fs.readFileSync(path.join(base, 'config/policy.json'), 'utf8'));
  policy.filesystem.allowWrite.push(...JSON.parse(process.env.CLAUDE_WORKSPACES || '[]'));
  // Browsing public sites is allowed. The upstream runtime still checks every
  // resolved address and refuses host/loopback/private/metadata destinations.
  // No external parent proxy, policy control FD, or unsandboxed retry is used.
  await SandboxManager.initialize(policy, async () => true);
  try {
    const command = ['/usr/bin/python3', path.join(base, 'code/browser-only.py'), process.argv[3]].map(quote).join(' ');
    const wrapped = await SandboxManager.wrapWithSandbox(command);
    const child = spawn(wrapped, {shell: '/bin/bash', stdio: 'inherit'});
    process.on('SIGTERM', () => child.kill('SIGTERM'));
    process.on('SIGINT', () => child.kill('SIGINT'));
    const code = await new Promise((resolve, reject) => {
      child.on('error', reject);
      child.on('exit', code => resolve(code ?? 1));
    });
    process.exitCode = code;
  } finally {
    await SandboxManager.reset();
  }
} else if (mode === 'mcp') {
  // The browser service has no host project mounts. Stage approved uploads in
  // its shared artifact directory rather than exposing projects to web pages.
  const {createInterface} = await import('node:readline');
  const {randomUUID} = await import('node:crypto');
  const reader = createInterface({input: process.stdin});
  const child = mcpChild({stdio: ['pipe', 'inherit', 'inherit']});
  child.on('error', () => { process.exitCode = 1; reader.close(); });
  child.on('exit', code => { process.exitCode = code ?? 1; reader.close(); process.stdin.destroy(); });
  const roots = [path.join(base, 'state/workspace'), ...JSON.parse(process.env.CLAUDE_WORKSPACES || '[]')];
  function approved(file) { return roots.some(root => file.startsWith(root + path.sep)); }
  try {
    for await (const line of reader) {
      let message;
      try { message = JSON.parse(line); } catch { child.stdin.write(line + '\n'); continue; }
      if (message.method === 'tools/call' && message.params?.name === 'upload_file') {
        try {
          const files = message.params.arguments.filePaths;
          if (!Array.isArray(files) || !files.length) throw new Error('invalid upload');
          const folder = path.join(base, 'state/workspace/.browser-uploads', randomUUID());
          fs.mkdirSync(folder, {recursive: true, mode: 0o700});
          message.params.arguments.filePaths = files.map((file, index) => {
            const real = fs.realpathSync(file);
            if (!approved(real)) throw new Error('outside project');
            const fd = fs.openSync(real, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
            try {
              if (!fs.fstatSync(fd).isFile() || !approved(fs.realpathSync('/proc/self/fd/' + fd))) throw new Error('outside project');
              const target = path.join(folder, index + '-' + path.basename(real));
              fs.copyFileSync('/proc/self/fd/' + fd, target);
              fs.chmodSync(target, 0o600);
              return target;
            } finally { fs.closeSync(fd); }
          });
        } catch {
          process.stdout.write(JSON.stringify({jsonrpc: '2.0', id: message.id, result: {content: [{type: 'text', text: 'Browser uploads are restricted to approved project/artifact files.'}], isError: true}}) + '\n');
          continue;
        }
      }
      child.stdin.write(JSON.stringify(message) + '\n');
    }
  } finally { child.stdin.end(); }
} else if (mode === 'verify') {
  const canary = JSON.parse(fs.readFileSync(path.join(base, 'config/canary.json'), 'utf8'));
  const checks = {};
  function requireCheck(name, condition) {
    checks[name] = Boolean(condition);
    if (!condition) throw new Error('Boundary check failed: ' + name);
  }
  function cannotOpen(file) {
    try { const fd = fs.openSync(file, 'r'); fs.closeSync(fd); return false; }
    catch { return true; }
  }
  requireCheck('host_file_denied', cannotOpen(canary.file));
  requireCheck('daily_chrome_hidden', !fs.existsSync(path.join(manifest.host_home, '.config/google-chrome')));
  requireCheck('other_profile_parent_hidden', !fs.existsSync(canary.other_profile));
  requireCheck('host_process_root_hidden', !fs.existsSync('/proc/' + canary.host_pid + '/root'));
  requireCheck('host_docker_socket_hidden', !fs.existsSync('/var/run/docker.sock'));
  requireCheck('host_session_bus_hidden', !fs.existsSync('/run/user/1000/bus'));
  requireCheck('host_display_authority_hidden', !fs.existsSync('/parent-Xauthority') && (!canary.host_display_authority || !fs.existsSync(canary.host_display_authority)));
  requireCheck('host_display_socket_hidden', !canary.host_display_socket || !fs.existsSync(canary.host_display_socket));
  if (process.env.DISPLAY) {
    const actualDisplay = spawnSync('/usr/bin/xdpyinfo', [], {encoding: 'utf8', timeout: 5000});
    requireCheck('private_display_authenticated', actualDisplay.status === 0);
    const noCookie = spawnSync('/usr/bin/xdpyinfo', [], {encoding: 'utf8', timeout: 5000, env: {...process.env, XAUTHORITY: '/nonexistent-display-authority'}});
    requireCheck('private_display_requires_cookie', noCookie.status !== 0);
  }
  const artifact = path.join(base, 'state/workspace/browser-only-probe.txt');
  fs.writeFileSync(artifact, 'allowed-browser-artifact');
  requireCheck('own_artifact_writable', fs.readFileSync(artifact, 'utf8') === 'allowed-browser-artifact');
  const alias = path.join(base, 'state/workspace/host-file-alias');
  try { fs.unlinkSync(alias); } catch {}
  fs.symlinkSync(canary.file, alias);
  requireCheck('symlink_read_denied', cannotOpen(alias));
  fs.unlinkSync(alias);

  const tcpReached = await new Promise(resolve => {
    const socket = net.connect({host: '127.0.0.1', port: canary.host_port});
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('error', () => resolve(false));
    socket.setTimeout(1500, () => { socket.destroy(); resolve(false); });
  });
  requireCheck('host_loopback_denied', !tcpReached);
  const unixReached = await new Promise(resolve => {
    const socket = net.connect(canary.unix_socket);
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('error', () => resolve(false));
    socket.setTimeout(1500, () => { socket.destroy(); resolve(false); });
  });
  requireCheck('host_unix_socket_denied', !unixReached);
  const proxyAttempt = spawnSync('/usr/bin/curl', ['-s', '--max-time', '5', '--noproxy', '', '-o', '/dev/null', '-w', '%{http_code}', 'http://127.0.0.1:' + canary.host_port], {encoding: 'utf8'});
  requireCheck('proxy_cannot_reach_host_loopback', proxyAttempt.stdout === '403');
  const publicAttempt = spawnSync('/usr/bin/curl', ['-sS', '--max-time', '10', '-o', '/dev/null', '-w', '%{http_code}', 'https://example.com/'], {encoding: 'utf8'});
  requireCheck('public_https_reachable', publicAttempt.status === 0 && publicAttempt.stdout === '200');
  const container = JSON.parse(fs.readFileSync(path.join(base, 'config/tool-container.json'), 'utf8'));
  const toolEnv = {...process.env};
  for (const key of JSON.parse(fs.readFileSync(path.join(base, 'config/auth-keys.json'), 'utf8'))) delete toolEnv[key];
  const authMarker = process.env.ANTHROPIC_AUTH_TOKEN;
  const probeScript = `const fs=require('fs'); const marker=${JSON.stringify(authMarker)}; const results={};
    results.no_auth_in_tool_environment=!Object.values(process.env).includes(marker);
    let parent='';try{parent=fs.readFileSync('/proc/${process.pid}/environ','utf8')}catch{}
    results.cli_auth_proc_hidden=!parent.includes(marker);
    results.cli_home_hidden=!fs.existsSync(${JSON.stringify(path.join(base, 'state/home/cli-secret-canary'))});
    console.log(JSON.stringify(results));`;
  const authProbe = spawnSync(container[0], [...container.slice(1), manifest.node, '-e', probeScript], {env: toolEnv, encoding: 'utf8', timeout: 10000});
  requireCheck('credential_probe_runs_in_tool_container', authProbe.status === 0);
  for (const [name, result] of Object.entries(JSON.parse(authProbe.stdout))) requireCheck(name, result);

  const child = spawn(manifest.node, [path.join(base, 'code/browser-only-runtime.mjs'), 'mcp'], {stdio: ['pipe', 'pipe', 'pipe']});
  let sequence = 0;
  let buffer = '';
  const waiting = new Map();
  child.stdout.on('data', chunk => {
    buffer += chunk.toString();
    while (buffer.includes('\n')) {
      const end = buffer.indexOf('\n');
      const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
      if (!line.trim()) continue;
      const message = JSON.parse(line);
      const pending = waiting.get(message.id);
      if (!pending) continue;
      waiting.delete(message.id); clearTimeout(pending.timer);
      message.error ? pending.reject(new Error('MCP request refused')) : pending.resolve(message.result);
    }
  });
  // Drain, but never echo, browser/tool stderr or URL-bearing diagnostic text.
  child.stderr.on('data', () => {});
  function request(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { waiting.delete(id); reject(new Error('MCP verification timeout')); }, 20000);
      waiting.set(id, {resolve, reject, timer});
      child.stdin.write(JSON.stringify({jsonrpc: '2.0', id, method, params}) + '\n');
    });
  }
  function text(result) { return (result.content || []).filter(x => x.type === 'text').map(x => x.text).join('\n'); }
  try {
    await request('initialize', {protocolVersion: '2024-11-05', capabilities: {}, clientInfo: {name: 'browser-only-canary', version: '1'}});
    child.stdin.write(JSON.stringify({jsonrpc: '2.0', method: 'notifications/initialized'}) + '\n');
    const tools = await request('tools/list');
    requireCheck('browser_mcp_available', tools.tools.some(x => x.name === 'list_pages'));
    const url = 'data:text/html,<title>Browser-only probe</title><button onclick="this.textContent=\'Clicked\'">Probe</button><input type="file">';
    const opener = path.join(base, 'code/browser-only.py');
    // Use the same HTTP link handler the real CLI will invoke. It must create a
    // target in this browser, not dispatch to the host's default browser.
    const link = spawnSync('/usr/bin/xdg-open', ['https://example.com/?browser-only-link-canary'], {encoding: 'utf8', timeout: 10000});
    requireCheck('cli_link_handler_succeeded', link.status === 0);
    const linkPages = JSON.parse(spawnSync('/usr/bin/python3', ['-c', 'import urllib.request; print(urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:19291/json/list").read().decode())'], {encoding: 'utf8'}).stdout);
    requireCheck('cli_link_created_in_private_browser', linkPages.some(p => p.url.includes('browser-only-link-canary')));
    const publicPage = await request('tools/call', {name: 'new_page', arguments: {url: 'https://example.com/'}});
    requireCheck('browser_public_https_navigation', !publicPage.isError);
    const publicId = Number(text(publicPage).split('\n').find(line => line.includes('[selected]'))?.match(/^\s*(\d+):/)?.[1]);
    const publicTitle = await request('tools/call', {name: 'evaluate_script', arguments: {pageId: publicId, function: '() => document.title'}});
    requireCheck('browser_public_page_rendered', text(publicTitle).includes('Example Domain'));
    for (const [name, file] of [['browser_environment_has_no_cli_auth', '/proc/self/environ'], ['browser_cannot_read_cli_auth_proc', '/proc/' + process.pid + '/environ'], ['browser_cannot_read_cli_credentials_home', path.join(base, 'state/home/cli-secret-canary')]]) {
      const opened = await request('tools/call', {name: 'new_page', arguments: {url: 'file://' + file}});
      if (opened.isError) { requireCheck(name, true); continue; }
      const id = Number(text(opened).split('\n').find(line => line.includes('[selected]'))?.match(/^\s*(\d+):/)?.[1]);
      const body = await request('tools/call', {name: 'evaluate_script', arguments: {pageId: id, function: '() => document.body.innerText'}});
      requireCheck(name, !body.isError && !text(body).includes(authMarker));
    }
    const created = await request('tools/call', {name: 'new_page', arguments: {url}});
    requireCheck('automation_can_open_page', !created.isError);
    const pages = await request('tools/call', {name: 'list_pages', arguments: {}});
    const lines = text(pages).split('\n');
    const ownPage = lines.find(line => line.includes('data:text/html'));
    const pageId = Number(ownPage?.match(/^\s*(\d+):/)?.[1]);
    requireCheck('only_own_browser_page_list', Number.isInteger(pageId));
    const clicked = await request('tools/call', {name: 'evaluate_script', arguments: {pageId, function: "() => { document.querySelector('button').click(); return document.querySelector('button').textContent; }"}});
    requireCheck('automation_can_click', !clicked.isError && text(clicked).includes('Clicked'));
    const snapshot = await request('tools/call', {name: 'take_snapshot', arguments: {pageId}});
    const uploadLine = text(snapshot).split('\n').find(line => /Choose [Ff]ile|No file chosen/.test(line));
    const uid = uploadLine?.match(/uid=([^\s]+)/)?.[1];
    requireCheck('upload_input_identified', Boolean(uid));
    const upload = await request('tools/call', {name: 'upload_file', arguments: {pageId, uid, filePaths: [artifact]}});
    requireCheck('own_artifact_upload_allowed', !upload.isError);
    const authUpload = await request('tools/call', {name: 'upload_file', arguments: {pageId, uid, filePaths: ['/proc/self/environ']}});
    if (authUpload.isError) requireCheck('process_environment_upload_has_no_cli_auth', true);
    else {
      const content = await request('tools/call', {name: 'evaluate_script', arguments: {pageId, function: "async () => await document.querySelector('input').files[0].text()"}});
      requireCheck('process_environment_upload_has_no_cli_auth', !content.isError && !text(content).includes(authMarker));
    }
    const parentUpload = await request('tools/call', {name: 'upload_file', arguments: {pageId, uid, filePaths: ['/proc/' + process.pid + '/environ']}});
    requireCheck('cli_process_environment_upload_denied', Boolean(parentUpload.isError));
    const screenshot = path.join(base, 'state/workspace/browser-only-probe.png');
    const shot = await request('tools/call', {name: 'take_screenshot', arguments: {pageId, format: 'png', filePath: screenshot}});
    requireCheck('own_browser_screenshot', !shot.isError && fs.existsSync(screenshot) && fs.readFileSync(screenshot).subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])));
    const denied = await request('tools/call', {name: 'evaluate_script', arguments: {pageId, function: '() => "not-a-host-file"', filePath: canary.file}});
    requireCheck('mcp_cannot_write_host_file', Boolean(denied.isError));
    const args = JSON.parse(fs.readFileSync(path.join(base, 'config/claude-command.json'), 'utf8'));
    requireCheck('native_development_tools_available', !args.includes('--tools') && args.includes('--dangerously-skip-permissions'));
    const shell = spawnSync('/bin/bash', ['-c', 'set -e; d=$(mktemp -d "$HOME/.claude/tool-smoke.XXXXXX"); printf working > "$d/check"; test "$(cat "$d/check")" = working; mv "$d/check" "$d/renamed"; rm "$d/renamed"; rmdir "$d"'], {encoding: 'utf8', timeout: 5000});
    requireCheck('bash_file_read_write_rename_remove', shell.status === 0);
    const version = spawnSync(args[0], [...args.slice(1), '--version'], {encoding: 'utf8', timeout: 15000});
    requireCheck('real_cli_starts_inside_boundary', version.status === 0 && /Claude Code/.test(version.stdout));
    console.log(JSON.stringify({verified: checks, inferenceCallsSubmitted: 0}, null, 2));
  } finally {
    child.stdin.end();
    child.kill('SIGTERM');
    for (const pending of waiting.values()) clearTimeout(pending.timer);
  }
} else {
  throw new Error('Unknown browser-only runtime mode');
}
