/** Installed-version native browser adapter. No timers, model calls or payload logs. */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID, createHash } from 'node:crypto';
import { o as readBrowserRegistry } from '/app/dist/registry-D02eWy_Z.mjs';
const executeFile = promisify(execFile);
const BASE = process.env.BROWSER_REAPER_STATE_DIR ?? '/home/node/.openclaw/maintenance/browser-idle-reaper';
const IMAGE = 'openclaw-sandbox-browser:local-2026.9.6-gh';
const IMAGE_ID = 'sha256:fa7746ce66162b150764e6821467d0666041a4041e46199e9f2d7b60c72cbd7e';
const safeOwner = owner => typeof owner === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(owner);
const ownerOf = key => /^agent:([a-zA-Z0-9_-]{1,128})(?::|$)/.exec(key ?? '')?.[1];

export async function resolveNativeBrowser({owner, workspace, containerName}) {
  if (!safeOwner(owner)) throw new Error('Browser maintenance ownership unavailable');
  const entries = (await readBrowserRegistry()).entries.filter(e =>
    ownerOf(e.sessionKey) === owner && e.image === IMAGE &&
    (workspace === undefined || e.workspaceDir === workspace) &&
    (containerName === undefined || e.containerName === containerName));
  if (entries.length !== 1) throw new Error('Browser maintenance ownership ambiguous');
  const entry = entries[0];
  const {stdout} = await executeFile('/usr/local/bin/docker', ['inspect', entry.containerName], {timeout:4000,maxBuffer:2*1024*1024});
  const c = JSON.parse(stdout)[0];
  if (!/^[a-f0-9]{64}$/.test(c?.Id) || c.Name !== '/'+entry.containerName ||
      !entry.containerName.startsWith('openclaw-sbx-browser-') ||
      c.Config?.Labels?.['openclaw.sandboxBrowser'] !== '1' || c.Config?.Image !== IMAGE || c.Image !== IMAGE_ID ||
      c.Config?.Labels?.['openclaw.sessionKey'] !== entry.sessionKey) {
    throw new Error('Browser maintenance runtime identity mismatch');
  }
  return {id:c.Id,owner,running:c.State?.Running === true};
}

export async function emitNativeEvent(kind, item) {
  const args = [BASE+'/browser_idle_reaper.py','--event',kind,'--browser-id',item.id,'--owner',item.owner];
  if (item.operation) args.push('--operation-id',item.operation);
  await executeFile('/opt/ai-job-search-venv/bin/python3',args,{
    timeout:4000,maxBuffer:8192,env:{...process.env,BROWSER_REAPER_STATE_DIR:BASE}
  });
}

export async function recordNativeBrowserStarted(containerName, scopeKey, restarted=false) {
  const item = await resolveNativeBrowser({owner:ownerOf(scopeKey),containerName});
  if (item.running) await emitNativeEvent(restarted?'restarted':'started',item);
}

export async function trackedBrowserExecution(opts, execute, ...args) {
  // Host/node browsers retain native policy; this adapter only owns sandbox browsers.
  if (!opts?.sandboxBridgeUrl) return await execute(...args);
  const item = await resolveNativeBrowser({owner:opts.agentId,workspace:opts.workspaceDir});
  item.operation = createHash('sha256').update(randomUUID()).digest('hex');
  await emitNativeEvent('begin',item);
  try { return await execute(...args); }
  finally {
    try { await emitNativeEvent('finish',item); }
    catch { console.error('browser-idle-reaper: completion tracking uncertain; retain runtime'); }
  }
}
