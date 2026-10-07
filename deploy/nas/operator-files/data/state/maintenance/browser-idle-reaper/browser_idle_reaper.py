#!/usr/bin/env python3
"""Event-armed hourly NAS job. No permanent active timer or resident checker.

Native browser hooks write begin/end events. NAS's existing systemd path facility
runs --sync-schedule on an arm/disarm event; that command starts/stops
one hourly timer in the NAS's existing systemd user manager.
"""
import argparse,contextlib,fcntl,hashlib,json,math,os,re,subprocess,time,uuid
from pathlib import Path
from event_state import initial,begin,finish,observe_started,confirm_stopped,eligible_ids,due,advance,reconcile_schedule,InvalidState

BASE=Path(os.environ.get('BROWSER_REAPER_STATE_DIR','/volume1/docker/openclaw-prod/data/state/maintenance/browser-idle-reaper'))
HOST_BASE=Path('/volume1/docker/openclaw-prod/data/state/maintenance/browser-idle-reaper')
GATEWAY='openclaw-new-openclaw-gateway-1'
GATEWAY_IMAGE='sha256:8c9896f8b7c9ac05d6d0f7ffbc7f45e3a9e4ca65d155fcb1d992f388d4413f4e'
BROWSER_IMAGE='openclaw-sandbox-browser:local-2026.9.6-gh'
BROWSER_IMAGE_ID='sha256:fa7746ce66162b150764e6821467d0666041a4041e46199e9f2d7b60c72cbd7e'
TIMER='openclaw-browser-idle-hourly.timer'
class UnsafeState(Exception):pass

def invoke(a,timeout=15):
 r=subprocess.run(a,capture_output=True,text=True,timeout=timeout)
 if r.returncode:raise UnsafeState('command_failed:'+a[0])
 return r.stdout

def decode(s):
 try:return json.loads(s)
 except (ValueError,TypeError) as e:raise UnsafeState('non_json_response') from e

def now():return int(time.time()*1000)

def atomic(path,value):
 tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
 with tmp.open('w') as f:
  os.chmod(tmp,0o600);json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)

def load():
 p=BASE/'activity.json'
 if not p.exists():return initial()
 s=decode(p.read_text())
 if s.get('version')!=2:raise UnsafeState('unknown_state_version')
 return s

@contextlib.contextmanager
def locked():
 if not BASE.is_dir():raise UnsafeState('maintenance_directory_missing')
 with (BASE/'state.lock').open('a') as f:
  os.chmod(BASE/'state.lock',0o600);fcntl.flock(f,fcntl.LOCK_EX);yield

def save(s,old_armed,force_schedule=False):
 atomic(BASE/'activity.json',s)
 # Skip even the boot oneshot when the last recorded state is all-off.
 flag=BASE/'detector-armed.flag'
 if s['armed']:
  if not flag.exists():atomic(flag,{'version':3,'armed':True})
 else:flag.unlink(missing_ok=True)
 if old_armed!=s['armed'] or force_schedule:
  atomic(BASE/'schedule-request.json',{'version':2,'enabled':s['armed'],'first_due_ms':s['next_due_ms']})

def event(kind,browser_id,owner,operation_id):
 if not re.fullmatch('[a-f0-9]{64}',browser_id or ''):raise UnsafeState('invalid_container_identity')
 if kind in ('begin','finish') and not re.fullmatch('[a-f0-9]{64}',operation_id or ''):raise UnsafeState('invalid_operation_identity')
 if kind in ('begin','started','restarted') and not re.fullmatch('[a-zA-Z0-9_-]{1,128}',owner or ''):raise UnsafeState('invalid_owner')
 with locked():
  s=load();previous=s['armed']
  if kind=='begin':begin(s,browser_id,owner,operation_id,now())
  elif kind in ('started','restarted'):observe_started(s,browser_id,owner,now(),kind=='restarted')
  elif kind=='finish':finish(s,browser_id,operation_id,now())
  elif kind=='stopped':confirm_stopped(s,browser_id,now())
  elif kind=='degraded':s['healthy']=False;s['revision']+=1
  else:raise UnsafeState('unknown_event')
  save(s,previous,kind in ('begin','restarted'))

def inspect(ids):return decode(invoke(['docker','inspect',*ids])) if ids else []

def gateway_identity():
 c=inspect([GATEWAY])[0]
 if not c['State']['Running'] or c['Image']!=GATEWAY_IMAGE or c['State'].get('Health',{}).get('Status')!='healthy':raise UnsafeState('gateway_changed_or_not_healthy')
 return (c['Id'],c['State']['StartedAt'])

def registry():
 # Reuse the installed native read-only registry reader without loading the full
 # CLI/plugin catalog for each browser while a short admission lease is held.
 code="import {o as read} from '/app/dist/registry-D02eWy_Z.mjs'; console.log(JSON.stringify({browsers:(await read()).entries.map(e=>({...e,imageMatch:e.image==="+json.dumps(BROWSER_IMAGE)+"}))}));"
 r=decode(invoke(['docker','exec',GATEWAY,'node','--input-type=module','-e',code],10))
 if not isinstance(r,dict) or not isinstance(r.get('browsers'),list):raise UnsafeState('unknown_registry_contract')
 return r['browsers']

def owned_browser(c,entries,owner):
 name=c.get('Name','').lstrip('/');cfg=c.get('Config',{});state=c.get('State',{})
 matches=[e for e in entries if e.get('containerName')==name and e.get('imageMatch') is True]
 return (len(matches)==1 and name.startswith('openclaw-sbx-browser-')
         and (cfg.get('Labels') or {}).get('openclaw.sandboxBrowser')=='1'
         and cfg.get('Image')==BROWSER_IMAGE and c.get('Image')==BROWSER_IMAGE_ID and matches[0].get('image')==BROWSER_IMAGE
         and (matches[0].get('sessionKey')=='agent:'+owner or matches[0].get('sessionKey','').startswith('agent:'+owner+':'))
         and state.get('Running') is True and not state.get('Paused') and not state.get('Restarting'))

def rpc(method,payload):
 return decode(invoke(['docker','exec',GATEWAY,'openclaw','gateway','call',method,'--params',json.dumps(payload,separators=(',',':')),'--timeout','8000','--json'],15))

def lease_ready(r):
 return (isinstance(r,dict) and r.get('status')=='ready' and r.get('activeCount')==0 and r.get('blockers')==[]
         and isinstance(r.get('suspensionId'),str) and bool(r['suspensionId'])
         and isinstance(r.get('expiresAtMs'),(int,float)) and not isinstance(r['expiresAtMs'],bool)
         and math.isfinite(r['expiresAtMs']) and r['expiresAtMs']-now()>45000)

def lease_status_ready(r):
 # Installed native status response intentionally omits ID and activeCount.
 return (isinstance(r,dict) and r.get('status')=='ready'
         and isinstance(r.get('expiresAtMs'),(int,float)) and not isinstance(r['expiresAtMs'],bool)
         and math.isfinite(r['expiresAtMs']) and r['expiresAtMs']-now()>45000)

def sync_schedule():
 # The NAS vendor's crontab has no spool write privilege. Reuse its existing
 # systemd manager's single conditional timer; no second scheduler process.
 with locked():
  s=load()
  p=subprocess.run(['systemctl','--user','show',TIMER,'--property=LoadState','--property=ActiveState'],capture_output=True,text=True,timeout=10)
  props=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
  if props.get('LoadState') not in ('loaded','not-found') or props.get('ActiveState') not in ('active','inactive','failed'):
   raise UnsafeState('cannot_inspect_native_timer')
  active=props['ActiveState']=='active'
  if s['armed'] and not active:
   invoke(['systemctl','--user','start',TIMER])
   invoke(['systemctl','--user','is-active',TIMER])
   result={'status':'schedule_enabled','enabled':True}
  elif not s['armed'] and active:
   invoke(['systemctl','--user','stop',TIMER])
   p=subprocess.run(['systemctl','--user','is-active',TIMER],capture_output=True,text=True,timeout=10)
   if p.returncode==0:raise UnsafeState('timer_stop_not_confirmed')
   result={'status':'schedule_removed','enabled':False}
  else:result={'status':'schedule_unchanged','enabled':s['armed']}
  atomic(BASE/'scheduler-state.json',result|{'kind':'nas-native-systemd-user-timer','timer':TIMER,'atUtcMs':now()})
  return result

def reconcile_runtime(reset_baseline=False):
 # Bounded boot/deployment reconciliation, not a recurring all-off checker.
 if not reset_baseline and not load()['armed']:return {'status':'disarmed','checks':0}
 running=invoke(['docker','ps','-q','--no-trunc','--filter','label=openclaw.sandboxBrowser=1']).split()
 containers=inspect(running)
 entries=registry() if running else []
 verified=[]
 for c in containers:
  candidates=[e for e in entries if e.get('containerName')==c.get('Name','').lstrip('/')]
  if len(candidates)!=1:raise UnsafeState('unregistered_browser')
  owner=re.match(r'^agent:([a-zA-Z0-9_-]{1,128})(?::|$)',candidates[0].get('sessionKey',''))
  if not owner or not owned_browser(c,entries,owner[1]):raise UnsafeState('unverified_browser')
  verified.append((c['Id'],owner[1]))
 with locked():
  s=load();previous=s['armed']
  for bid,b in s['browsers'].items():
   if bid not in running:b['running']=False
  for bid,owner in verified:observe_started(s,bid,owner,now(),reset_baseline)
  s['untracked_running']=False;reconcile_schedule(s,now());save(s,previous)
 return {'status':'runtime_reconciled','running_browsers':len(running),'enabled':s['armed']}

def tick(apply=False):
 # An accidental manual invocation while disarmed makes NO Docker/DB/RPC calls.
 s=load()
 if not s['armed']:return {'status':'disarmed','checks':0}
 if not due(s,now()):return {'status':'not_due','checks':0}
 # This private maintenance mutex does not govern ordinary OpenClaw tool access.
 with (BASE/'tick.lock').open('a') as f:
  os.chmod(BASE/'tick.lock',0o600)
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return {'status':'already_running'}
  running=invoke(['docker','ps','-q','--no-trunc','--filter','label=openclaw.sandboxBrowser=1']).split()
  with locked():
   s=load();previous=s['armed']
   if not s['armed'] or not due(s,now()):return {'status':'superseded','checks':0}
   s['untracked_running']=bool(set(running)-set(s['browsers']))
   # Unknown running browsers cannot be silently adopted with a guessed timestamp.
   # Clear known stopped instances only when no recorded calls remain active.
   for bid,b in list(s['browsers'].items()):
    if b['running'] and bid not in running and not b['active']:confirm_stopped(s,bid,now())
   reconcile_schedule(s,now());save(s,previous)
   if not running:
    if s['armed']:return {'status':'deferred_active_or_unknown'}
    result={'status':'all_stopped','checks_cancelled':True}
   else:
    wanted=[] if s['untracked_running'] else [bid for bid in eligible_ids(s,now()) if bid in running]
    result=None
  if result is not None:
   sync_schedule();return result
  identity=gateway_identity()
  entries=registry() if wanted else []
  targets=[c for c in inspect(wanted) if owned_browser(c,entries,s['browsers'][c['Id']]['owner'])] if wanted else []
  if not apply:
   return {'status':'dry_run','eligible_browser_count':len(targets),'timer_still_event_armed':True}
  lease=None;stopped=[]
  try:
   if targets:
    lease=rpc('gateway.suspend.prepare',{'requestId':'browser-idle-v2-'+uuid.uuid4().hex,'terminalPolicy':'preserve','drain':False})
    if not lease_ready(lease):result={'status':'deferred_busy_or_uncertain','stopped':0}
    else:
     deadline=time.monotonic()+80
     for c in targets:
      if time.monotonic()+13>=deadline:break
      if gateway_identity()!=identity or not lease_status_ready(rpc('gateway.suspend.status',{'suspensionId':lease['suspensionId']})):raise UnsafeState('lease_or_gateway_changed')
      with locked():
       current=load();previous=current['armed'];bid=c['Id']
       if bid not in eligible_ids(current,now()):continue
       fresh=inspect([bid])[0]
       if not owned_browser(fresh,registry(),current['browsers'][bid]['owner']):continue
       if time.monotonic()+13>=deadline or not lease_ready(lease):break
       invoke(['docker','stop','--time','10',bid],13)
       if inspect([bid])[0]['State']['Running']:raise UnsafeState('stop_not_confirmed')
       confirm_stopped(current,bid,now());save(current,previous);stopped.append(bid[:12])
     result={'status':'completed','stopped_browser_ids':stopped,'profile_data_and_containers_preserved':True}
   else:result={'status':'nothing_eligible'}
  finally:
   if isinstance(lease,dict) and isinstance(lease.get('suspensionId'),str):
    resumed=rpc('gateway.suspend.resume',{'suspensionId':lease['suspensionId']})
    if not isinstance(resumed,dict) or resumed.get('ok') is not True or resumed.get('status')!='running' or not isinstance(resumed.get('resumed'),bool):
     raise UnsafeState('resume_not_confirmed')
  with locked():
   current=load();previous=current['armed'];advance(current,now());save(current,previous)
  sync_schedule()
  atomic(BASE/'last-result.json',result|{'atUtcMs':now()})
  return result

if __name__=='__main__':
 p=argparse.ArgumentParser();m=p.add_mutually_exclusive_group(required=True)
 m.add_argument('--event',choices=['begin','finish','started','restarted','stopped','degraded']);m.add_argument('--sync-schedule',action='store_true');m.add_argument('--reconcile-runtime',action='store_true');m.add_argument('--tick',action='store_true')
 p.add_argument('--browser-id');p.add_argument('--owner');p.add_argument('--operation-id');p.add_argument('--apply',action='store_true');a=p.parse_args()
 try:
  if a.event:event(a.event,a.browser_id,a.owner,a.operation_id)
  elif a.sync_schedule:print(json.dumps(sync_schedule()))
  elif a.reconcile_runtime:print(json.dumps(reconcile_runtime()))
  else:print(json.dumps(tick(a.apply)))
 except (UnsafeState,InvalidState,OSError,subprocess.TimeoutExpired):
  print(json.dumps({'status':'deferred_error','completion_unconfirmed':True,'inspect_activity_and_container_state_before_retry':True}));raise SystemExit(1)
