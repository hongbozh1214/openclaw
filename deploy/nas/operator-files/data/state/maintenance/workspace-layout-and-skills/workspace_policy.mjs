// Reuse the agent's primary sandbox for its own project and Workshop tasks.
// Keep guest/profile/session and managed-worktree isolation decisions native.
import fs from 'node:fs';
import path from 'node:path';
import {l as resolveWorkspace} from '/app/dist/agent-scope-config-IQKOEtZ4.mjs';
import {t as resolveWorkshop} from '/app/dist/skills-root-Bd-leLaO.mjs';
function inside(root,value){const rel=path.relative(root,value);return rel===''||(!rel.startsWith('..'+path.sep)&&rel!=='..'&&!path.isAbsolute(rel));}
function canonical(value){try{return fs.realpathSync(value);}catch(e){if(e.code!=='ENOENT')throw e;const parent=path.dirname(value);return parent===value?value:path.join(canonical(parent),path.basename(value));}}
export function normalizeOwnWorkspace(params,resolved){
 const id=resolved?.runtime?.agentId;const cfg=params.config;
 if(!id||!cfg?.agents?.entries?.[id])return params;
 if(params.agentId&&params.agentId!==id)throw new Error('Workspace agent identity mismatch.');
 const requested=path.resolve(params.workspaceDir?.trim()||resolveWorkspace(cfg,id));
 for(const other of Object.keys(cfg.agents.entries))if(other!==id){
  for(const root of [resolveWorkspace(cfg,other),resolveWorkshop(cfg,other)])if(inside(canonical(root),canonical(requested)))throw new Error('Foreign agent workspace/Workshop is unavailable.');
 }
 if(resolved.cfg.scope!=='agent'||resolved.cfg.workspaceAccess!=='rw'||resolved.runtime.isolationSubject)return params;
 const own=resolveWorkspace(cfg,id);const workshop=resolveWorkshop(cfg,id);
 if(inside(path.resolve(own),requested)||inside(path.resolve(workshop),requested)){
  if(!inside(canonical(own),canonical(requested))&&!inside(canonical(workshop),canonical(requested)))throw new Error('Workspace alias escapes its owner.');
  return {...params,workspaceDir:own};
 }
 return params;
}
