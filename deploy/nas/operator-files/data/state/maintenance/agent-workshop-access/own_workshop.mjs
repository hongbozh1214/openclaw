// Gateway-side authority for agent-owned workshops. Never grants host exec or
// exposes another agent's root. Dependencies are the installed native services.
import fs from 'node:fs/promises';
import syncFs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

function inside(a, b) {
  const relative = path.relative(a, b);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

export function resolveOwnWorkshopAuthority(params, deps) {
  const { config, agentId, sessionKey } = params;
  if (!config || typeof agentId !== 'string' || !/^[a-z0-9][a-z0-9_-]*$/.test(agentId)) return null;
  const entries = deps.listAgentEntries(config);
  if (!entries.some(entry => entry.id === agentId)) return null;
  const sandbox = deps.resolveSandboxConfigForAgent(config, agentId);
  if (sandbox.mode !== 'all' || sandbox.scope !== 'agent') return null;
  if (sessionKey !== undefined && (!sessionKey.startsWith(`agent:${agentId}:`) || sessionKey.length <= `agent:${agentId}:`.length)) return null;
  const roots = entries.map(entry => ({ id: entry.id, root: path.resolve(deps.resolveWorkshopSkillsDir(config, entry.id)) }));
  const own = roots.find(entry => entry.id === agentId);
  if (!own || roots.some(entry => entry.id !== agentId && (inside(own.root, entry.root) || inside(entry.root, own.root)))) return null;
  const revision = params.run?.proposalRevision;
  const workspaceDir = path.resolve(deps.resolveAgentWorkspaceDir(config, agentId));
  if (revision && (revision.agentId !== agentId || (revision.workspaceDir && ![workspaceDir, own.root].includes(path.resolve(revision.workspaceDir))))) return null;
  if (params.run?.origin?.agentId && params.run.origin.agentId !== agentId) return null;
  return { agentId, root: own.root, roots, workspaceDir, config };
}

async function assertRootAuthority(authority) {
  // Reject workshop/agent-dir aliases, including symlinks introduced after the
  // session was prepared. Existing higher-level deployment aliases stay valid.
  for (const entry of authority.roots) {
    for (const target of [path.dirname(entry.root), entry.root]) {
      try {
        if ((await fs.lstat(target)).isSymbolicLink()) throw new Error('Workshop root authority cannot use symlinks.');
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
  function projectedRealPath(target) {
    try { return syncFs.realpathSync(target); }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      const parent = path.dirname(target);
      if (parent === target) throw error;
      return path.join(projectedRealPath(parent), path.basename(target));
    }
  }
  const roots = authority.roots.map(entry => ({id: entry.id, root: projectedRealPath(entry.root)}));
  const own = roots.find(entry => entry.id === authority.agentId);
  if (roots.some(entry => entry.id !== own.id && (inside(own.root, entry.root) || inside(entry.root, own.root)))) throw new Error('Workshop roots overlap.');
}

async function assertPlainTree(directory) {
  const rootStat = await fs.lstat(directory);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error('Only a real own-workshop skill directory may be removed.');
  for (const name of await fs.readdir(directory)) {
    const target = path.join(directory, name);
    const stat = await fs.lstat(target);
    if (stat.isSymbolicLink() || (!stat.isDirectory() && !stat.isFile()) || (stat.isFile() && stat.nlink !== 1)) throw new Error('Workshop removal refuses links and special files.');
    if (stat.isDirectory()) await assertPlainTree(target);
  }
}

function result(details, text) { return { content: [{ type: 'text', text }], details }; }

export function wrapOwnWorkshopTool(nativeTool, params, deps) {
  const authority = resolveOwnWorkshopAuthority(params, deps);
  if (!authority || !params.sessionKey) throw new Error('No session-bound own-workshop authority.');
  const restricted = params.run?.proposalOnly === true || params.run?.proposalRevision !== undefined;
  const originalActions = nativeTool.parameters.properties.action.enum;
  if (!Array.isArray(originalActions)) throw new Error('Unsupported native Workshop action schema.');
  const allowedFields = new Set(Object.keys(nativeTool.parameters.properties));
  const receipts = new Map();
  return {
    ...nativeTool,
    description: nativeTool.description + `\nThis tool operates ONLY agent ${authority.agentId}'s own workshop. create/update stage a proposal; apply makes it live. list_skills lists own live workshop skills. remove deletes an own live skill after read, retains a recoverable backup outside skill loading, and never accepts a target agent or directory.`,
    parameters: {
      ...nativeTool.parameters,
      properties: {
        ...nativeTool.parameters.properties,
        action: { ...nativeTool.parameters.properties.action, enum: [...originalActions, ...restricted ? [] : ['list_skills', 'remove']], description: nativeTool.parameters.properties.action.description + ' list_skills = own live skill names; remove = remove skill_name after read, retaining a recovery backup.' }
      }
    },
    execute: async (callId, args, ...rest) => {
      if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Workshop arguments must be an object.');
      for (const key of Object.keys(args)) if (!allowedFields.has(key)) throw new Error(`Unsupported Workshop argument: ${key}. Owner and paths are session-bound.`);
      if (![...originalActions, ...restricted ? [] : ['list_skills', 'remove']].includes(args.action)) throw new Error('Workshop action not authorized for this run.');
      await assertRootAuthority(authority);
      const nativeOptions = { config: authority.config, agentId: authority.agentId };
      if (args.action === 'list_skills') {
        const skills = await deps.listWritableWorkshopSkillSummaries(nativeOptions);
        return result({ agentId: authority.agentId, skills: skills.map(({name, skillKey, description}) => ({name, skillKey, description})) }, skills.length ? skills.map(skill => `${skill.name}: ${skill.description}`).join('\n') : 'No live own-workshop skills.');
      }
      if (args.action === 'remove') {
        if (typeof args.skill_name !== 'string' || !args.skill_name.trim()) throw new Error('skill_name is required.');
        let before;
        const removed = await deps.withSkillCollectionLock(async () => {
          await assertRootAuthority(authority);
          const skill = await deps.readWritableWorkshopSkill(args.skill_name, nativeOptions);
          const relative = path.relative(authority.root, skill.baseDir);
          if (!relative || relative.startsWith('.') || relative.includes(path.sep) || !inside(authority.root, skill.baseDir)) throw new Error('Removal target must be one direct own-workshop skill.');
          await assertPlainTree(skill.baseDir);
          const currentHash = await deps.readSkillProposalTargetTreeSha256(skill.baseDir);
          if (!receipts.has(skill.skillKey) || receipts.get(skill.skillKey) !== currentHash) throw new Error('Read the current own-workshop skill before removing it; it may have changed.');
          const recoveryRoot = path.join(path.dirname(authority.root), 'workshop-removed');
          try { if ((await fs.lstat(recoveryRoot)).isSymbolicLink()) throw new Error('Recovery directory cannot be a symlink.'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
          await fs.mkdir(recoveryRoot, { recursive: true, mode: 0o700 });
          const removalId = randomUUID();
          const recoveryPath = path.join(recoveryRoot, removalId);
          await fs.mkdir(recoveryPath, { mode: 0o700 });
          const manifest = { version: 1, action: 'remove', state: 'prepared', agentId: authority.agentId, sessionKey: params.sessionKey, removalId, skillName: skill.skillName, skillKey: skill.skillKey, relativeDirectory: relative, treeSha256: currentHash, at: new Date().toISOString(), reason: typeof args.reason === 'string' ? args.reason.slice(0, 1000) : '' };
          const manifestPath = path.join(recoveryPath, 'manifest.json');
          await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
          if (deps.hasCommittedSkillChangeHooks()) before = await deps.snapshotCommittedSkillArtifactBestEffort({ skillDir: skill.baseDir, skillKey: skill.skillKey, source: 'workshop' });
          if (await deps.readSkillProposalTargetTreeSha256(skill.baseDir) !== currentHash) throw new Error('Skill changed while preparing removal. Read it again.');
          await fs.rename(skill.baseDir, path.join(recoveryPath, 'skill'));
          deps.bumpSkillsSnapshotVersion({ reason: 'workshop' });
          receipts.delete(skill.skillKey);
          manifest.state = 'removed';
          try { await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n', { mode: 0o600 }); }
          catch (error) { manifest.auditWarning = String(error.code ?? 'manifest update failed'); }
          return { agentId: authority.agentId, skillName: skill.skillName, skillKey: skill.skillKey, removed: true, removalId, recoveryPath, ...(manifest.auditWarning ? { auditWarning: manifest.auditWarning } : {}) };
        }, { agentId: authority.agentId });
        if (before) await deps.dispatchCommittedSkillChangeBestEffort({ action: 'removed', before, source: 'workshop', workspaceDir: authority.workspaceDir });
        return result(removed, `Removed own-workshop skill ${removed.skillName}. Recovery copy: ${removed.recoveryPath}`);
      }
      const response = await nativeTool.execute(callId, args, ...rest);
      if (args.action === 'read') {
        const skill = await deps.readWritableWorkshopSkill(args.skill_name, nativeOptions);
        receipts.set(skill.skillKey, await deps.readSkillProposalTargetTreeSha256(skill.baseDir));
      }
      return response;
    }
  };
}
