
from __future__ import annotations
from app.config.hfc_endpoints_settings import get_hfc_rf_analytics_settings
import base64, json, shlex
from typing import Any, Optional
import paramiko

try:
    from app.core.settings import settings
except Exception:
    settings = None

try:
    from app.core.paths import HFC_ANALYTICS_SSH_KEY
except Exception:
    HFC_ANALYTICS_SSH_KEY = r"C:\Users\NTTSERVER\.ssh\noc_cable_atlas_ed25519"

_HFC_RF_ANALYTICS = get_hfc_rf_analytics_settings()

def _setting(name, default):
    if settings is None:
        return default
    value=getattr(settings,name,None)
    return default if value in (None,"") else value

class HfcRfService:
    def __init__(self):
        settings = get_hfc_rf_analytics_settings()
        self.host=settings.host
        self.port=settings.port
        self.user=settings.user
        self.db=settings.remote_db
        self.connect_timeout=settings.connect_timeout
        self.command_timeout=settings.command_timeout
        self.key_path=str(HFC_ANALYTICS_SSH_KEY)

    def _remote(self,payload):
        remote_code=r"""
import json,sqlite3,sys
payload=json.loads(sys.argv[1]); db=payload["db"]; action=payload["action"]; params=payload.get("params") or {}
con=sqlite3.connect(db); con.row_factory=sqlite3.Row

def tables():
    return [r["name"] for r in con.execute("select name from sqlite_master where type in ('table','view')").fetchall()]
def cols(name):
    if not name:return []
    return [r[1] for r in con.execute("pragma table_info(%s)"%json.dumps(name)).fetchall()]
def pick(cands,contains=None):
    ex=tables()
    for c in cands:
        if c in ex:return c
    if contains:
        for t in ex:
            l=t.lower()
            if all(x in l for x in contains):return t
    return None
def cc(columns,names):
    lm={c.lower():c for c in columns}
    for n in names:
        if n.lower() in lm:return lm[n.lower()]
    return None

up=pick(["upstream_scores","hfc_upstream_scores","upstream_score_current"],["upstream","score"])
sp=pick(["spectrum_points","spectrum_samples","spectrum_observations","spectrum_data"],["spectrum"])

if action=="schema":
    print(json.dumps({"ok":True,"upstream_table":up,"upstream_columns":cols(up),"spectrum_table":sp,"spectrum_columns":cols(sp),"all_spectrum_like":[t for t in tables() if "spectrum" in t.lower()]},ensure_ascii=False));raise SystemExit
if not up:
    print(json.dumps({"ok":False,"error":"No upstream_scores"}));raise SystemExit

uc=cols(up)
c_node=cc(uc,["node","nodo","node_name"])
c_cmts=cc(uc,["cmts","cmts_name"])
c_up=cc(uc,["upstream","upstream_name","interface","port","us"])
c_cnr=cc(uc,["cnr_actual","cnr","cnr_db","snr","snr_db","snr_up"])
c_sq=cc(uc,["sq_actual","signal_quality","signal_quality_db","sq","sq_db"])
c_fec=cc(uc,["fec_actual_pct","fec_pct","fec_percent","fec"])
c_ufec=cc(uc,["ufec_actual_pct","ufec_pct","ufec_percent","ufec"])
c_util=cc(uc,["util_actual_pct","utilization","utilization_pct","util_pct","utilization_max"])
c_imp=cc(uc,["impair_actual","impair_flag","impair","impairment"])
c_imph=cc(uc,["impair_historical_pct"])
c_ssnr=cc(uc,["spectrum_snr_db"])
c_ssig=cc(uc,["spectrum_historical_signature"])
c_scapt=cc(uc,["spectrum_captures"])
c_smin=cc(uc,["spectrum_snr_min_hist"])
c_srange=cc(uc,["spectrum_snr_range_hist"])
c_speak=cc(uc,["spectrum_peak_pct_hist"])
c_score=cc(uc,["score_analytics","score","rf_score","risk_score"])
c_state=cc(uc,["analytics_state","state","estado"])
c_diag=cc(uc,["diagnosis","diagnostico","main_diagnosis"])
c_conf=cc(uc,["pattern_confidence"])
c_reasons=cc(uc,["score_reasons"])
c_ts=cc(uc,["latest_sample","collected_at","created_at","updated_at","ts","timestamp","fecha"])
c_updated=cc(uc,["updated_at"])

fields=[("nodo",c_node),("cmts",c_cmts),("upstream",c_up),("cnr",c_cnr),("signal_quality",c_sq),("fec_pct",c_fec),("ufec_pct",c_ufec),("utilization",c_util),("impair",c_imp),("impair_historical_pct",c_imph),("spectrum_snr_db",c_ssnr),("spectrum_historical_signature",c_ssig),("spectrum_captures",c_scapt),("spectrum_snr_min_hist",c_smin),("spectrum_snr_range_hist",c_srange),("spectrum_peak_pct_hist",c_speak),("score",c_score),("analytics_state",c_state),("diagnosis",c_diag),("pattern_confidence",c_conf),("score_reasons",c_reasons),("timestamp",c_ts),("updated_at",c_updated)]
sel=[('"%s" AS "%s"'%(col,a)) if col else ('NULL AS "%s"'%a) for a,col in fields]

where=[];args=[]
for p,col in [("node",c_node),("cmts",c_cmts),("upstream",c_up)]:
    v=params.get(p)
    if v not in (None,"") and col: where.append('"%s"=?'%col); args.append(str(v))
search=str(params.get("search") or "").strip()
if search:
    ors=[]
    for col in [c_node,c_cmts,c_up,c_diag]:
        if col: ors.append('upper(coalesce("%s","")) like ?'%col); args.append("%%%s%%"%search.upper())
    if ors: where.append("("+" OR ".join(ors)+")")
wsql=(" WHERE "+" AND ".join(where)) if where else ""
limit=max(1,min(int(params.get("limit") or 500),5000));offset=max(0,int(params.get("offset") or 0))

def ordering():
    p=[]
    if c_imp:p.append('coalesce("%s",0) DESC'%c_imp)
    if c_score:p.append('coalesce("%s",-1) DESC'%c_score)
    if c_ufec:p.append('coalesce("%s",-1) DESC'%c_ufec)
    if c_cnr:p.append('coalesce("%s",999) ASC'%c_cnr)
    return (" ORDER BY "+", ".join(p)) if p else ""

if action=="upstreams":
    total=con.execute('SELECT COUNT(*) c FROM "%s"%s'%(up,wsql),args).fetchone()["c"]
    sql='SELECT %s FROM "%s"%s%s LIMIT ? OFFSET ?'%(",".join(sel),up,wsql,ordering())
    rows=[dict(r) for r in con.execute(sql,args+[limit,offset]).fetchall()]
    print(json.dumps({"ok":True,"source":"HFC_ANALYTICS_LINUX","table":up,"count":len(rows),"total":total,"items":rows},ensure_ascii=False));raise SystemExit

if action=="resumen":
    res={"ok":True,"source":"HFC_ANALYTICS_LINUX","upstream_table":up,"upstreams_total":con.execute('SELECT COUNT(*) c FROM "%s"'%up).fetchone()["c"],"spectrum_table":sp,"spectrum_points":0,"spectrum_upstreams":0,"coverage":{}}
    for a,col in [("cnr",c_cnr),("signal_quality",c_sq),("fec_pct",c_fec),("ufec_pct",c_ufec),("utilization",c_util),("impair",c_imp),("score",c_score),("spectrum_snr_db",c_ssnr)]:
        res["coverage"][a]=con.execute('SELECT COUNT(*) c FROM "%s" WHERE "%s" IS NOT NULL'%(up,col)).fetchone()["c"] if col else 0
    if sp:
        sc=cols(sp); su=cc(sc,["upstream","upstream_name","interface","port","us"])
        res["spectrum_points"]=con.execute('SELECT COUNT(*) c FROM "%s"'%sp).fetchone()["c"]
        if su: res["spectrum_upstreams"]=con.execute('SELECT COUNT(DISTINCT "%s") c FROM "%s"'%(su,sp)).fetchone()["c"]
    print(json.dumps(res,ensure_ascii=False));raise SystemExit

if action=="ranking":
    lim=max(1,min(int(params.get("limit") or 20),200))
    sql='SELECT %s FROM "%s"%s%s LIMIT ?'%(",".join(sel),up,wsql,ordering())
    rows=[dict(r) for r in con.execute(sql,args+[lim]).fetchall()]
    print(json.dumps({"ok":True,"count":len(rows),"items":rows},ensure_ascii=False));raise SystemExit

if action=="spectrum":
    if not sp:
        print(json.dumps({"ok":True,"available":False,"count":0,"items":[]}));raise SystemExit
    sc=cols(sp)
    sn=cc(sc,["node","nodo","node_name"]); scm=cc(sc,["cmts","cmts_name"]); su=cc(sc,["upstream","upstream_name","interface","port","us"])
    sf=cc(sc,["frequency_hz","frequency","freq_hz","freq","mhz"]); snoise=cc(sc,["noise_dbmv","power","power_dbmv","level","amplitude","dbmv"])
    ssnr=cc(sc,["spectrum_snr_db","snr_db","snr"]); swidth=cc(sc,["channel_width_hz","channel_width"]); sraw=cc(sc,["raw_file","source_file","file"]); sts=cc(sc,["sample_time","collected_at","created_at","updated_at","ts","timestamp","fecha"])
    sw=[];sa=[]
    for p,col in [("node",sn),("cmts",scm),("upstream",su)]:
        v=params.get(p)
        if v not in (None,"") and col: sw.append('"%s"=?'%col);sa.append(str(v))
    swsql=(" WHERE "+" AND ".join(sw)) if sw else ""; slim=max(1,min(int(params.get("limit") or 5000),20000))
    sflds=[("nodo",sn),("cmts",scm),("upstream",su),("frequency_hz",sf),("noise_dbmv",snoise),("spectrum_snr_db",ssnr),("channel_width_hz",swidth),("raw_file",sraw),("timestamp",sts)]
    ss=[('"%s" AS "%s"'%(col,a)) if col else ('NULL AS "%s"'%a) for a,col in sflds]
    order=(' ORDER BY "%s" ASC'%sf) if sf else ""
    sql='SELECT %s FROM "%s"%s%s LIMIT ?'%(",".join(ss),sp,swsql,order)
    rows=[dict(r) for r in con.execute(sql,sa+[slim]).fetchall()]
    print(json.dumps({"ok":True,"available":bool(rows),"table":sp,"count":len(rows),"items":rows},ensure_ascii=False));raise SystemExit

print(json.dumps({"ok":False,"error":"Acción RF no soportada"}))
"""
        payload=dict(payload); payload["db"]=self.db
        enc=base64.b64encode(remote_code.encode()).decode()
        launcher="import base64;exec(base64.b64decode(%r).decode())"%enc
        cmd=f"python3 -c {shlex.quote(launcher)} {shlex.quote(json.dumps(payload,ensure_ascii=False))}"
        client=paramiko.SSHClient();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(self.host,port=self.port,username=self.user,key_filename=self.key_path,timeout=self.connect_timeout,banner_timeout=self.connect_timeout,auth_timeout=self.connect_timeout)
            _,stdout,stderr=client.exec_command(cmd,timeout=self.command_timeout)
            out=stdout.read().decode("utf-8",errors="replace").strip();err=stderr.read().decode("utf-8",errors="replace").strip()
            if not out: raise RuntimeError(err or "Respuesta RF vacía")
            data=json.loads(out.splitlines()[-1])
            if data.get("ok") is False: raise RuntimeError(data.get("error") or "Error RF remoto")
            return data
        finally:
            client.close()

    def schema(self): return self._remote({"action":"schema"})
    def resumen(self): return self._remote({"action":"resumen"})
    def upstreams(self,node=None,cmts=None,upstream=None,search=None,limit=500,offset=0):
        return self._remote({"action":"upstreams","params":{"node":node,"cmts":cmts,"upstream":upstream,"search":search,"limit":limit,"offset":offset}})
    def ranking(self,node=None,cmts=None,search=None,limit=20):
        return self._remote({"action":"ranking","params":{"node":node,"cmts":cmts,"search":search,"limit":limit}})
    def spectrum(self,node=None,cmts=None,upstream=None,limit=5000):
        return self._remote({"action":"spectrum","params":{"node":node,"cmts":cmts,"upstream":upstream,"limit":limit}})

service=HfcRfService()
