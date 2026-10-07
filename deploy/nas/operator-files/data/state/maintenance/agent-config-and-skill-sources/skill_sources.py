"""Trusted NAS metadata audit and source maps. Never imports browser/model tools."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone

ROOT = Path('/volume1/docker/openclaw-prod')
STATE = ROOT / 'data/state'
GATEWAY = 'openclaw-new-openclaw-gateway-1'
MAINTENANCE_RULES = '### 同名技能优先级\n\n当公共技能与私人技能同名时，以公共技能为准。发现结果、来源表、恢复操作及后续维护均按该规则解释。除非明确调整发现机制，否则不得依赖同名私人技能覆盖公共技能的行为。\n\n### 删除公共技能\n\n删除公共技能前，应先审阅各 Agent 的来源表及实际发现结果，确认不存在仍需使用的引用。公共技能删除属于影响所有 Agent 的变更，应避免仅依据单个 Agent 的使用情况作出判断。\n\n### 来源表的定位\n\n`skills/SOURCES.md` 与 `SOURCES.json` 用于维护记录和来源盘点，不保证与当前状态实时同步。发生差异或存在疑问时，以原生技能发现结果和实际目录状态为最终依据。\n\n### 恢复与重新启用\n\n恢复备份文件仅表示恢复历史内容，不代表自动恢复原运行行为。恢复后是否重新进入技能发现范围、是否重新参与加载、是否重新形成同名遮蔽关系，应单独核对并明确确认。恢复资料与重新启用技能是两个独立操作。'
POLICY_CLARIFICATION = '本次增加维护规则，不调整原生技能发现机制。来源表如发现原生选中同名私人技能，须如实记录实际来源并标记与公共优先规则的冲突，不得把结果改记为公共技能。私人定制使用不同技能名称，不能依赖同名覆盖。\n\n删除公共技能的跨 Agent 核对由可信维护端汇总各自来源表、实际发现结果及目录状态，保留私人数据边界；单个 Agent 无权读取其他 Agent 的私人来源表。备份资料可保留在发现范围之外；重新启用须另行核对上述加载与遮蔽状态。'

SOURCE_TYPES = {'private': 'openclaw-workspace', 'projectAgent': 'agents-skills-project', 'workshop': 'openclaw-workshop', 'managed': 'openclaw-managed'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cli(*args):
    return json.loads(subprocess.check_output(['docker', 'exec', GATEWAY, 'openclaw', 'skills', *args, '--json'], stderr=subprocess.PIPE, text=True, timeout=40))


def tree_manifest(root):
    result = {}
    for f in sorted(Path(root).rglob('*')):
        relative = str(f.relative_to(root))
        if f.is_symlink():
            result[relative] = {'symlink': os.readlink(f)}
        elif f.is_file():
            result[relative] = sha(f)
    return result


def scan(root):
    rows = []
    root = Path(root)
    if not root.exists():
        return rows
    for p in sorted(root.rglob('SKILL.md')):
        if any(n in ['.git', 'node_modules', '.venv', 'fixtures', '__pycache__'] for n in p.relative_to(root).parts):
            continue
        text = p.read_text(errors='replace')
        header = text.split('---', 2)[1] if text.startswith('---') and text.count('---') >= 2 else ''
        match = re.search(r'^name:\s*(.*?)\s*$', header, re.M)
        name = match.group(1).strip('"\'') if match else p.parent.name
        tree = tree_manifest(p.parent)
        rows.append({'name': name, 'skillFile': str(p), 'realSkillFile': str(p.resolve()), 'fileHash': sha(p), 'treeHash': hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest(), 'files': len(tree)})
    return rows


def audit(local_status=False):
    config = json.loads((STATE / 'openclaw.json').read_text())
    managed = scan(STATE / 'skills')
    local = json.loads(subprocess.check_output(['docker', 'exec', GATEWAY, 'node', '/home/node/.openclaw/maintenance/agent-config-and-skill-sources/native_skill_status.mjs'], stderr=subprocess.PIPE, text=True, timeout=40)) if local_status else None
    result = {'schema': 1, 'auditedAtUTC': datetime.now(timezone.utc).isoformat(), 'managed': managed, 'agents': {}, 'modelCalls': 0, 'browserCalls': 0}
    for aid, agent in config['agents']['entries'].items():
        root = ROOT / 'data/workspace' if aid == 'main' else STATE / Path(agent['workspace']).relative_to('/home/node/.openclaw')
        workshop = Path(agent.get('agentDir', '/home/node/.openclaw/agents/' + aid + '/agent'))
        workshop = STATE / workshop.relative_to('/home/node/.openclaw') / 'workshop-skills'
        rows = local[aid] if local is not None else cli('list', '--agent', aid)
        rows = rows if isinstance(rows, list) else rows['skills']
        # No descriptions, private task bodies or credentials are exported.
        loaded = [{'name': r['name'], 'source': r.get('source'), 'eligible': r.get('eligible'), 'modelVisible': r.get('modelVisible')} for r in rows]
        proposals = cli('workshop', 'list', '--agent', aid)
        proposals = proposals if isinstance(proposals, list) else proposals['proposals']
        result['agents'][aid] = {
            'workspace': str(root), 'configuredWorkspace': agent['workspace'],
            'private': scan(root / 'skills'), 'projectAgent': scan(root / '.agents/skills'), 'workshop': scan(workshop),
            'loaded': loaded,
            'proposals': [{'id': p['id'], 'status': p['status'], 'skillName': p.get('skillName')} for p in proposals],
        }
    return result


def maps(inventory):
    snapshot = {'auditedAtUTC': inventory.get('auditedAtUTC') or datetime.now(timezone.utc).isoformat(), 'realtime': False, 'selectionFieldsReferTo': 'audit-time-native-result', 'authority': 'native-discovery-and-actual-directories'}
    policy = {'sameNamePriority': 'public', 'nativeDiscoveryChanged': False, 'nativeConflictHandling': 'record-actual-selection-and-flag-policy-conflict', 'rulesMarkdown': MAINTENANCE_RULES}
    public = {'snapshot': snapshot, 'maintenancePolicy': policy, 'schema': 1, 'scope': 'common-only', 'newSkillDefault': '/shared/skills/<skill>/SKILL.md', 'skills': []}
    for skill in inventory['managed']:
        path = Path(skill['skillFile']).relative_to(STATE / 'skills/shared-toolkit')
        public['skills'].append({'name': skill['name'], 'source': '/shared/skills/' + str(path), 'treeHashAtAudit': skill['treeHash']})
    private = {}
    for aid, agent in inventory['agents'].items():
        selected = {r['name']: r for r in agent['loaded']}
        entries = []
        for kind, skills in [('private', agent['private']), ('projectAgent', agent['projectAgent']), ('workshop', agent['workshop']), ('managed', inventory['managed'])]:
            for skill in skills:
                winner = selected.get(skill['name'], {})
                if kind == 'managed':
                    source = '/shared/skills/' + str(Path(skill['skillFile']).relative_to(STATE / 'skills/shared-toolkit'))
                elif kind == 'workshop':
                    source = '/workspace/workshop/' + Path(skill['skillFile']).parent.name + '/SKILL.md'
                else:
                    source = str(Path(skill['skillFile']).relative_to(agent['workspace']))
                entries.append({'name': skill['name'], 'scope': 'shared' if kind == 'managed' else 'private', 'source': source,
                                'nativeSource': SOURCE_TYPES[kind], 'currentlySelected': winner.get('source') == SOURCE_TYPES[kind],
                                'eligible': winner.get('eligible'), 'modelVisible': winner.get('modelVisible'), 'treeHashAtAudit': skill['treeHash']})
        shared_names = {r['name'] for r in inventory['managed']}
        private_names = {r['name'] for kind in ['private', 'projectAgent', 'workshop'] for r in agent[kind]}
        conflicts = [{'name': name, 'requiredSource': SOURCE_TYPES['managed'], 'actualNativeSourceAtAudit': selected.get(name, {}).get('source'), 'reason': 'public-priority-policy-conflict'} for name in sorted(shared_names & private_names) if selected.get(name, {}).get('source') != SOURCE_TYPES['managed']]
        private[aid] = {'snapshot': snapshot, 'maintenancePolicy': policy, 'priorityConflicts': conflicts, 'schema': 1, 'agent': aid, 'newPrivateSkillDefault': 'skills/<skill>/SKILL.md', 'skills': entries,
                        'notes': ['Keep private Workshop proposal/apply flow.', 'Maps are audited routing references, not a second loader or allowlist.', 'Refresh through the trusted audit after source changes; native discovery remains authoritative.']}
    return public, private


def private_guide(aid, mapping):
    lines = ['# 技能主要维护来源', '', '本表只包含本 agent 私有技能与公共技能，不包含其他 agent 私有资料。', '',
             '新建普通私人技能默认放 `skills/<skill>/`；已有 `.agents/skills/` 的独有技能继续在原处维护。私人 Workshop 保留现有提案、检查、apply 和删除恢复流程。', '',
             '公共技能统一修改 `/shared/skills/<skill>/`。避免在工坊再创建同名副本；源文件 `docs/` 继续只读。此表是来源指引，不是固定技能名单或加载配置，实际来源以原生发现结果为准。', '',
             '盘点时间（UTC）：`' + mapping['snapshot']['auditedAtUTC'] + '`；下表选中状态仅代表该次盘点，不保证实时同步。', '', '| 技能 | 主要维护文件 | 盘点时原生选中 |', '| --- | --- | --- |']
    for skill in sorted(mapping['skills'], key=lambda x: (x['scope'], x['name'])):
        lines.append('| ' + skill['name'] + ' | `' + skill['source'] + '` | ' + ('是' if skill['currentlySelected'] else '否，需查原生发现') + ' |')
    lines += ['', '私人定制使用不同技能名称，避免误将私有内容写入公共库。内置技能需要定制时创建自有版本，不编辑运行投影。', '',
              '来源有变化时由可信维护端重新生成本表；agent 自己更新技能不依赖本表刷新，也不需要模型驱动的同步服务。', '']
    lines += [MAINTENANCE_RULES, '', POLICY_CLARIFICATION, '']
    if mapping['priorityConflicts']:
        lines += ['### 盘点发现的优先级冲突', '']
        for conflict in mapping['priorityConflicts']:
            lines.append('- `' + conflict['name'] + '`：盘点时原生来源 `' + str(conflict['actualNativeSourceAtAudit']) + '`；公共优先规则要求核对并解决冲突，不能据此依赖私人覆盖。')
        lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['audit', 'refresh-maps'])
    args = parser.parse_args()
    inventory = audit()
    if args.operation == 'audit':
        print(json.dumps(inventory, ensure_ascii=False, indent=2))
    else:
        public, private = maps(inventory)
        shared = STATE / 'skills/shared-toolkit/SOURCES.json'
        shared.write_text(json.dumps(public, ensure_ascii=False, indent=2) + '\n')
        for aid, mapping in private.items():
            folder = Path(inventory['agents'][aid]['workspace']) / 'skills'
            folder.mkdir(exist_ok=True)
            (folder / 'SOURCES.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n')
            (folder / 'SOURCES.md').write_text(private_guide(aid, mapping))
        print(json.dumps({'refreshed': True, 'agents': len(private), 'sharedSkills': len(public['skills']), 'modelCalls': 0, 'browserCalls': 0}))


if __name__ == '__main__':
    main()
