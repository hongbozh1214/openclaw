// The installed native status builder; no Gateway RPC, tool construction or model call.
import fs from 'node:fs';
import {n as buildWorkspaceSkillStatus} from '/app/dist/status-BSyTpTh2.mjs';
const config=JSON.parse(fs.readFileSync('/home/node/.openclaw/openclaw.json','utf8'));
const reports={};
for(const [agentId,agent] of Object.entries(config.agents.entries)){
 const report=buildWorkspaceSkillStatus(agent.workspace,{config,agentId});
 reports[agentId]=report.skills.map(r=>({name:r.name,source:r.source,eligible:r.eligible,modelVisible:r.modelVisible}));
}
console.log(JSON.stringify(reports));
process.exit(0);
