"""Browser-only activity state: no NAS/Gateway access and no polling loop."""
import math
INTERVAL_MS = 60 * 60 * 1000
IDLE_MS = 3 * INTERVAL_MS
class InvalidState(ValueError): pass

def initial():
    return {'version':2,'revision':0,'armed':False,'next_due_ms':None,'healthy':True,'untracked_running':False,'browsers':{}}

def timestamp(n):
    if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or n<=0:
        raise InvalidState('Invalid timestamp')
    return int(n)

def reconcile_schedule(s, now):
    live=any(b['running'] for b in s['browsers'].values()) or s.get('untracked_running',False)
    if live and not s['armed']:
        s['armed']=True;s['next_due_ms']=timestamp(now)+INTERVAL_MS
    elif not live:
        s['armed']=False;s['next_due_ms']=None

def begin(s, browser_id, owner, operation_id, now):
    now=timestamp(now)
    if not browser_id or not owner or not operation_id:raise InvalidState('Missing authoritative identity')
    b=s['browsers'].setdefault(browser_id,{'owner':owner,'running':False,'last_activity_ms':now,'active':{},'unknown':False})
    if b['owner']!=owner:raise InvalidState('Browser owner changed')
    # Call identity must be unique per actual execution, not model text or params.
    if operation_id in b['active']:raise InvalidState('Duplicate begin')
    b['running']=True;b['active'][operation_id]=now
    b['last_activity_ms']=max(b['last_activity_ms'],now)
    s['revision']+=1;reconcile_schedule(s,now)

def finish(s, browser_id, operation_id, now):
    now=timestamp(now);b=s['browsers'].get(browser_id)
    if not b or operation_id not in b['active']:
        # An unmatched completion cannot clear other calls or re-arm a timer.
        s['healthy']=False;s['revision']+=1;return
    b['active'].pop(operation_id)
    b['last_activity_ms']=max(b['last_activity_ms'],now)
    s['revision']+=1

def observe_started(s, browser_id, owner, now, restarted=False):
    """Authoritative native lifecycle observation; never general agent activity."""
    now=timestamp(now)
    b=s['browsers'].get(browser_id)
    if b and b['owner']!=owner:raise InvalidState('Browser owner changed')
    if b is None:
        b={'owner':owner,'running':True,'last_activity_ms':now,'active':{},'unknown':False}
        s['browsers'][browser_id]=b
    elif not b['running'] or restarted:
        b['running']=True;b['last_activity_ms']=max(b['last_activity_ms'],now)
    s['revision']+=1;reconcile_schedule(s,now)

def confirm_stopped(s, browser_id, now):
    b=s['browsers'].get(browser_id)
    if b and b['active']:raise InvalidState('Active browser cannot be marked stopped')
    if b:b['running']=False;s['revision']+=1
    reconcile_schedule(s,now)

def eligible_ids(s, now):
    now=timestamp(now)
    if not s['armed'] or not s['healthy']:return []
    return [key for key,b in s['browsers'].items() if b['running'] and not b['active'] and not b['unknown']
            and b['last_activity_ms']<=now and now-b['last_activity_ms']>IDLE_MS]

def due(s, now):
    return s['armed'] and isinstance(s['next_due_ms'],int) and timestamp(now)>=s['next_due_ms']

def advance(s, now):
    if s['armed'] and due(s,now):
        # Keep the original hourly phase, avoiding interval drift from minute-level cron.
        s['next_due_ms']+=((timestamp(now)-s['next_due_ms'])//INTERVAL_MS+1)*INTERVAL_MS
        s['revision']+=1
