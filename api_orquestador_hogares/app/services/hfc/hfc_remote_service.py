# -*- coding: utf-8 -*-
from __future__ import annotations
import json,os,re,subprocess
from pathlib import Path
from typing import Any,Dict,Optional
from app.config.hfc_endpoints_settings import get_hfc_snapshot_settings

_HFC_SNAPSHOT = get_hfc_snapshot_settings()
REMOTE_HOST=_HFC_SNAPSHOT.host
REMOTE_PORT=_HFC_SNAPSHOT.port
REMOTE_USER=_HFC_SNAPSHOT.user
REMOTE_QUERY=_HFC_SNAPSHOT.query
REMOTE_TIMEOUT=_HFC_SNAPSHOT.timeout
_NODE_RE=re.compile(r'^[A-Z0-9_-]{2,40}$')
def _candidate_keys():
    configured=os.getenv('HFC_REMOTE_KEY','').strip()
    if configured:yield Path(configured)
    home=Path(os.path.expanduser('~'))
    yield home/'.ssh'/'noc_cable_atlas_ed25519'
    yield Path(r'C:\Users\NTTSERVER.ssh\noc_cable_atlas_ed25519')
def find_remote_key()->Optional[Path]:
    for key in _candidate_keys():
        try:
            if key.exists() and key.is_file():return key
        except:pass
    return None
def _parse_json_stdout(stdout:str)->Dict[str,Any]:
    text=str(stdout or '').strip()
    if not text:raise ValueError('SSH remoto no devolvio JSON.')
    for line in reversed(text.splitlines()):
        line=line.strip()
        if line.startswith('{') and line.endswith('}'):return json.loads(line)
    return json.loads(text)
def query_hfc_snapshot(nodo:str)->Dict[str,Any]:
    node=str(nodo or '').strip().upper()
    if not _NODE_RE.fullmatch(node):return {'ok':False,'codigo':'HFC_REMOTE_NODE_INVALID','nodo':node,'error':'Nodo HFC invalido.'}
    key=find_remote_key()
    if key is None:return {'ok':False,'codigo':'HFC_REMOTE_KEY_MISSING','nodo':node,'error':'No encontre la llave SSH noc_cable_atlas_ed25519.'}
    ssh=os.getenv('HFC_REMOTE_SSH_EXE','ssh.exe').strip() or 'ssh.exe'
    cmd=[ssh,'-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2','-i',str(key),'-p',str(REMOTE_PORT),f'{REMOTE_USER}@{REMOTE_HOST}','python3',REMOTE_QUERY,'--node',node]
    env=dict(os.environ);env['PYTHONIOENCODING']='utf-8'
    try:
        proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=REMOTE_TIMEOUT,env=env)
    except subprocess.TimeoutExpired:return {'ok':False,'codigo':'HFC_REMOTE_TIMEOUT','nodo':node,'error':f'Consulta remota HFC supero {REMOTE_TIMEOUT}s.'}
    except Exception as exc:return {'ok':False,'codigo':'HFC_REMOTE_EXEC_ERROR','nodo':node,'error':f'{type(exc).__name__}: {exc}'}
    if proc.returncode!=0:return {'ok':False,'codigo':'HFC_REMOTE_SSH_ERROR','nodo':node,'error':f'SSH remoto termino con codigo {proc.returncode}.','stderr':proc.stderr[-2000:],'stdout':proc.stdout[-2000:]}
    try:data=_parse_json_stdout(proc.stdout)
    except Exception as exc:return {'ok':False,'codigo':'HFC_REMOTE_JSON_ERROR','nodo':node,'error':f'No pude interpretar JSON remoto: {exc}','stdout':proc.stdout[-3000:],'stderr':proc.stderr[-1000:]}
    data.setdefault('nodo',node);data['remote_host']=REMOTE_HOST;data['remote_port']=REMOTE_PORT;return data
def snapshot_to_hfc_result(snapshot:Dict[str,Any])->Dict[str,Any]:
    if not snapshot or not snapshot.get('ok'):return {'ok':False,'nodo':(snapshot or {}).get('nodo'),'error':(snapshot or {}).get('error') or 'HFC_AUTO remoto no concluyente.','remote_snapshot':snapshot}
    totals=dict(snapshot.get('totals') or {});total=int(totals.get('total') or 0);online=int(totals.get('online') or 0);init=int(totals.get('init') or 0);offline=int(totals.get('offline') or 0);decision=dict(snapshot.get('decision') or {})
    node=snapshot.get('nodo') or '';cmts=snapshot.get('cmts') or '';ip=snapshot.get('ip') or '';vendor=snapshot.get('vendor') or ''
    parts=[str(snapshot.get('observacion') or '').strip(),str(snapshot.get('descripcion') or '').strip()];summary=' | '.join(x for x in parts if x)
    if not summary:summary=f'HFC_AUTO reporta Total={total}, Online={online}, INIT={init}, Offline={offline}.'
    counts={'total':total,'online':online,'init':init,'offline':offline,'dbc':0,'ranging':0,'other':max(total-online-init-offline,0)}
    data={'metadata':{'node':node,'cmts':cmts,'ip':ip,'vendor':vendor,'source':'HFC_AUTO_REMOTE_SNAPSHOT','fecha_reporte':snapshot.get('fecha_reporte'),'edad_minutos':snapshot.get('edad_minutos'),'stale':snapshot.get('stale')},'mode':'HFC_AUTO_REMOTE_SNAPSHOT','service_groups':[node] if node else [],'segments':[{'name':node,'exists':True,'upstreams':[],'upstream_suffixes':[],'qams_summary':None,'modem_counts':counts}],'totals':counts,'decision':{'estado':decision.get('estado') or 'REVISAR','decision':decision.get('decision') or 'REVISAR / POSIBLE ESCALAMIENTO','severidad':decision.get('severidad') or 'MEDIA','confianza':'ALTA' if not snapshot.get('stale') else 'MEDIA','resumen':summary,'accion':'Usar el consolidado HFC_AUTO como estado operativo y cruzar con PathTrak.' if not snapshot.get('stale') else 'El consolidado HFC_AUTO supera 45 minutos; validar la siguiente corrida.'},'source_row':snapshot.get('row'),'remote_snapshot':snapshot}
    return {'ok':True,'nodo':node,'cmts':cmts,'ip':ip,'vendor':vendor,'fuente_resolucion':'hfc_auto.remote_snapshot','json_path':'','duracion_seg':0.0,'stdout':'','data':data,'pathtrak':None,'remote_snapshot':snapshot}
