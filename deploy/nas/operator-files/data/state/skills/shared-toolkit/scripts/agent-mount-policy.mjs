// Trusted operator policy. Pure planning; never provisions a browser or sandbox.
import path from 'node:path';

const unique = values => [...new Set(values)];
const clone = value => JSON.parse(JSON.stringify(value));
const inside = (root, value) => value === root || value.startsWith(root + '/');
function parseBind(value) {
  if (typeof value !== 'string') throw new Error('Bind must be a string');
  const pieces = value.split(':');
  if (pieces.length !== 3 || !path.isAbsolute(pieces[0]) || !path.isAbsolute(pieces[1]) || !['ro', 'rw'].includes(pieces[2])) throw new Error('Explicit absolute source:target:mode bind required');
  return {source: pieces[0], target: pieces[1], mode: pieces[2]};
}

export function assertIsolation(config) {
  const d = config.agents?.defaults?.sandbox;
  if (d?.mode !== 'all' || d?.scope !== 'agent' || d?.backend !== 'docker' || d?.workspaceAccess !== 'rw' ||
      config.tools?.exec?.host !== 'sandbox' || config.tools?.elevated?.enabled !== false ||
      config.tools?.sessions?.visibility !== 'agent' || config.tools?.agentToAgent?.enabled !== false ||
      config.tools?.codeMode?.executor !== 'quickjs' || config.tools?.fs?.workspaceOnly !== true ||
      d?.browser?.allowHostControl !== false) throw new Error('Global isolation policy requires operator review');
  for (const [id, a] of Object.entries(config.agents?.entries || {})) {
    if (a.sandbox?.mode && a.sandbox.mode !== 'all' || a.sandbox?.scope && a.sandbox.scope !== 'agent' ||
        a.sandbox?.backend && a.sandbox.backend !== 'docker' || a.sandbox?.workspaceAccess && a.sandbox.workspaceAccess !== 'rw' ||
        a.tools?.exec?.host && a.tools.exec.host !== 'sandbox' || a.tools?.elevated?.enabled === true ||
        a.tools?.fs?.workspaceOnly === false || a.tools?.sessions?.visibility && a.tools.sessions.visibility !== 'agent' ||
        a.tools?.agentToAgent?.enabled === true || a.sandbox?.browser?.allowHostControl === true)
      throw new Error('Agent ' + id + ' overrides isolation; no automatic repair');
  }
}

export function planAgentMounts(config, ids, context) {
  assertIsolation(config);
  const {state, hostPath, canonical, isRealDirectory} = context;
  const stateHost = hostPath(state), dataHost = path.dirname(stateHost);
  const commonHost = path.join(stateHost, 'skills/shared-toolkit');
  const common = [dataHost + '/shared/reference:/shared/reference:ro', dataHost + '/shared/handoff:/shared/handoff:rw',
    commonHost + ':/shared/skills:rw', ...['scripts', '.git', 'SHARED_TOOLKIT_MAINTENANCE.md'].map(n => commonHost + '/' + n + ':/shared/skills/' + n + ':ro')];
  const commonTargets = new Set(common.map(b => parseBind(b).target));
  const currentDefaults = config.agents.defaults.sandbox.docker?.binds || [];
  // Unknown defaults must be reviewed, rather than copied into every browser.
  if (currentDefaults.some(b => !commonTargets.has(parseBind(b).target))) throw new Error('Unmanaged default binds require operator review');
  for (const b of currentDefaults) {
    const current = parseBind(b), expected = common.map(parseBind).find(x => x.target === current.target);
    if (canonical(current.source) !== canonical(expected.source)) throw new Error('Shared bind source changed; operator review required');
  }
  const next = clone(config), changes = [], plans = {};
  const record = (field, before, after) => { if (JSON.stringify(before) !== JSON.stringify(after)) changes.push({field, before, after}); };
  record('agents.defaults.sandbox.docker.binds', currentDefaults, common);
  if (changes.length && ids.length !== Object.keys(config.agents.entries).length) throw new Error('Shared bind update requires --all');
  next.agents.defaults.sandbox.docker.binds = common;
  const owners = Object.entries(config.agents.entries).map(([id, a]) => ({id, workspace: canonical(a.workspace), workshop: canonical(path.join(a.agentDir || path.join(state, 'agents', id, 'agent'), 'workshop-skills')), cookie: canonical(path.join(state, 'browser', id === 'main' ? 'openclaw' : id, 'user-data'))}));
  for (const id of ids) {
    if (!/^[a-z][a-z0-9-]{0,63}$/.test(id) || !config.agents.entries[id]) throw new Error('Unknown or invalid agent id');
    const a = next.agents.entries[id], workspace = a.workspace;
    if (!path.isAbsolute(workspace)) throw new Error('Workspace must be absolute');
    const workshop = path.join(a.agentDir || path.join(state, 'agents', id, 'agent'), 'workshop-skills');
    const own = owners.find(o => o.id === id);
    for (const other of owners.filter(o => o.id !== id)) {
      if ([own.workspace, own.workshop, own.cookie].some(root => [other.workspace, other.workshop, other.cookie].some(foreign => inside(root, foreign) || inside(foreign, root)))) throw new Error('Private roots overlap for ' + id);
    }
    if (context.checkDirectories !== false) {
      for (const dir of [workspace, ...['docs', 'processed', 'memory', 'skills'].map(n => path.join(workspace, n)), workshop]) {
        if (!isRealDirectory(dir)) throw new Error(id + ': required directory is missing, symlinked or not a directory');
      }
    }
    const hostWorkspace = hostPath(workspace), hostWorkshop = hostPath(workshop);
    const cookie = path.join(state, 'browser', id === 'main' ? 'openclaw' : id, 'user-data');
    const hostCookie = hostPath(cookie);
    if (context.checkDirectories !== false && !isRealDirectory(cookie)) throw new Error(id + ': browser profile boundary needs review');
    const ownBinds = [hostWorkspace + ':' + workspace + ':rw', hostWorkspace + '/docs:' + workspace + '/docs:ro', hostWorkshop + ':/workspace/workshop:rw', hostWorkshop + ':' + workshop + ':rw'];
    const browserBinds = [hostCookie + ':/tmp/openclaw-home/.chrome:rw', ...ownBinds, ...common];
    const known = new Set([...ownBinds, ...browserBinds, ...common].map(b => parseBind(b).target));
    for (const kind of ['docker', 'browser']) {
      for (const b of a.sandbox?.[kind]?.binds || []) {
        const bind = parseBind(b);
        if (!known.has(bind.target)) throw new Error(id + ': unmanaged ' + kind + ' bind requires review');
        const source = canonical(bind.source);
        if (owners.filter(o => o.id !== id).some(o => [hostPath(o.workspace), hostPath(o.workshop), hostPath(o.cookie)].some(root => inside(canonical(root), source) || inside(source, canonical(root))))) throw new Error(id + ': foreign private bind detected; stop for review');
      }
    }
    a.sandbox ??= {}; a.sandbox.docker ??= {}; a.sandbox.browser ??= {};
    for (const [kind, binds] of [['docker', ownBinds], ['browser', browserBinds]]) {
      record('agents.entries.' + id + '.sandbox.' + kind + '.binds', a.sandbox[kind].binds || [], binds);
      a.sandbox[kind].binds = unique(binds);
    }
    // Resource limits, networks, models, roles, tools, auth and schedules are untouched.
    plans[id] = {workspace, hostWorkspace, workshop, hostWorkshop, cookieHost: hostCookie, dockerBinds: ownBinds, browserBinds, docsReadonly: true, workshopSharedPrimarySandbox: true};
  }
  return {config: next, changes, plans, sharedBinds: common};
}
