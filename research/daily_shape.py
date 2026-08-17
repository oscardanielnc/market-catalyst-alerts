#!/usr/bin/env python3
"""
daily_shape.py — ¿Los gráficos intradía de una empresa se repiten día a día?

Herramienta LOCAL y standalone (NO se despliega ni se commitea). Idea de Oscar (2026-06-23):
superponer el gráfico intradía de varios días (lun-vie) del último mes para ver si hay un
patrón diario recurrente (p.ej. "cae en la apertura y rebota a media mañana"). Empezamos con
WDC y LITE.

Cómo:
  - Baja barras de 1 minuto (Alpaca SIP) del último mes.
  - Agrupa por día de trading (hora del Este), recorta a sesión regular 09:30-16:00 ET.
  - Normaliza cada día a su APERTURA (09:30 = 0%) → eje Y = % vs apertura; eje X = min desde 09:30.
  - Emite un HTML interactivo (canvas) para superponer días, filtrar por día de semana y ver
    la curva PROMEDIO. Igual de filosofía que research/earnings_shape.py pero por día normal.

Uso:
  python research/daily_shape.py                 # WDC,LITE · últimos 30 días calendario
  python research/daily_shape.py WDC,LITE,NVDA   # otros tickers
  python research/daily_shape.py WDC 45          # 45 días de lookback

Requiere ALPACA_API_KEY / ALPACA_SECRET_KEY en el .env del proyecto (ya están en local).
Salida: data/daily_shape.html (ábrelo en el navegador).
"""
import os
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ── Rutas y entorno (cargar .env del root del proyecto) ─────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass

import pytz
ET = pytz.timezone("America/New_York")

ALPACA_BASE = "https://data.alpaca.markets"
_KEY = os.environ.get("ALPACA_API_KEY", "")
_SEC = os.environ.get("ALPACA_SECRET_KEY", "")
OUT_HTML = os.path.join(_ROOT, "data", "daily_shape.html")

SESSION_OPEN_MIN  = 9 * 60 + 30   # 09:30 ET
SESSION_CLOSE_MIN = 16 * 60       # 16:00 ET
SESSION_LEN_MIN   = SESSION_CLOSE_MIN - SESSION_OPEN_MIN  # 390


def _hdrs():
    return {"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SEC}


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_1min(ticker, start, end=None, limit=200000):
    """Barras de 1 minuto de Alpaca SIP, paginadas. Lista de dicts {t,o,h,l,c,v}."""
    bars, params = [], {"timeframe": "1Min", "start": start, "feed": "sip",
                        "limit": 1000, "sort": "asc"}
    if end:
        params["end"] = end
    try:
        while True:
            r = requests.get(f"{ALPACA_BASE}/v2/stocks/{ticker}/bars",
                             params=params, headers=_hdrs(), timeout=30)
            r.raise_for_status()
            data = r.json()
            chunk = data.get("bars") or []
            bars.extend(chunk)
            tok = data.get("next_page_token")
            if not tok or len(bars) >= limit:
                break
            params["page_token"] = tok
            time.sleep(0.15)
    except Exception as e:
        print(f"  [!] {ticker}: error bajando barras: {e}")
    return [{"t": b["t"], "o": float(b["o"]), "h": float(b["h"]),
             "l": float(b["l"]), "c": float(b["c"]), "v": int(b["v"])} for b in bars]


def build_ticker(ticker, lookback_days):
    """Devuelve {'ticker','days':[{date,weekday,wd_name,points:[[x,y],..],last_pct}]}."""
    start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    print(f"  - {ticker}: bajando 1min desde {start.date()} ...")
    bars = fetch_1min(ticker, _iso(start))
    print(f"    {len(bars)} barras 1min")

    # Agrupar por día de trading (ET) y recortar a sesión regular
    by_day = {}  # date_str -> list[(min_from_open, bar)]
    for b in bars:
        # t = "2026-06-23T13:30:00Z" (UTC). Convertir a ET.
        ts = b["t"].replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts).astimezone(ET)
        except Exception:
            continue
        mins = dt.hour * 60 + dt.minute
        if mins < SESSION_OPEN_MIN or mins > SESSION_CLOSE_MIN:
            continue  # fuera de sesión regular (pre/post market)
        x = mins - SESSION_OPEN_MIN  # 0..390
        by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append((x, b))

    WD = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    days = []
    for date_str, items in sorted(by_day.items()):
        items.sort(key=lambda it: it[0])
        if len(items) < 60:   # día incompleto (festivo/medio día) → saltar
            continue
        ref = items[0][1]["o"]  # apertura del día (open de la 1ª barra 09:30)
        if not ref:
            continue
        pts = [[x, round((bar["c"] / ref - 1) * 100, 3)] for x, bar in items]
        wd = datetime.strptime(date_str, "%Y-%m-%d").weekday()
        days.append({"date": date_str, "weekday": wd, "wd_name": WD[wd],
                     "points": pts, "last_pct": pts[-1][1]})
    print(f"    {len(days)} dias de sesion completos")
    return {"ticker": ticker, "days": days}


# ── Análisis: ¿cuándo se "decide" la dirección del día? ─────────────────────────

def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n; my = sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs); syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / (sxx * syy) ** 0.5


def analyze_commitment(ticker, days):
    """
    Responde: ¿durante las 1ª horas el movimiento es oscilante y RECIÉN después se fija la
    dirección del cierre? Para cada minuto t mide, sobre todos los días:
      - agree%  : % de días en que el signo del retorno en t YA coincide con el del cierre.
      - corr    : correlación entre el retorno en t y el retorno al cierre (qué tan predictivo es t).
      - osc%    : |retorno| medio en t (tamaño del bandazo).
    Y el 'lock-in': minuto a partir del cual el signo ya NO vuelve a cambiar hasta el cierre (mediana).
    """
    rows = []
    checkpoints = list(range(15, SESSION_LEN_MIN, 15)) + [SESSION_LEN_MIN - 1]
    # mapa por día: x->y, y retorno al cierre
    per_day = []
    for d in days:
        pm = {p[0]: p[1] for p in d["points"]}
        xclose = d["points"][-1][0]
        per_day.append((pm, pm[xclose]))
    for t in checkpoints:
        rt, rc = [], []
        for pm, close in per_day:
            if t in pm:
                rt.append(pm[t]); rc.append(close)
        if len(rt) < 3:
            continue
        agree = 100 * sum(1 for a, b in zip(rt, rc) if (a >= 0) == (b >= 0)) / len(rt)
        corr = _pearson(rt, rc)
        osc = sum(abs(a) for a in rt) / len(rt)
        rows.append({"t": t, "agree": round(agree), "corr": round(corr, 2) if corr is not None else None,
                     "osc": round(osc, 2)})
    # lock-in por día: minuto desde el cual el signo se mantiene == signo de cierre
    lockins = []
    for pm, close in per_day:
        cs = (close >= 0)
        xs = sorted(pm.keys())
        lk = xs[-1]
        for x in reversed(xs):
            if (pm[x] >= 0) == cs:
                lk = x
            else:
                break
        lockins.append(lk)
    lockins.sort()
    lockin_med = lockins[len(lockins) // 2] if lockins else None
    return {"rows": rows, "lockin_med": lockin_med, "n": len(days)}


def print_analysis(out):
    def hm(t):  # minuto desde 09:30 -> hora ET
        tot = 9 * 60 + 30 + t
        return f"{tot // 60:02d}:{tot % 60:02d}"
    for d in out:
        a = d.get("analysis") or {}
        rows = a.get("rows") or []
        print("\n" + "=" * 70)
        print(f"{d['ticker']}  ({a.get('n')} dias)  — ¿cuando se fija la direccion del cierre?")
        print(f"{'hora':>6} {'min':>5} {'coincide cierre':>16} {'corr c/cierre':>14} {'|mov| medio':>12}")
        for r in rows:
            c = "  -  " if r["corr"] is None else f"{r['corr']:+.2f}"
            print(f"{hm(r['t']):>6} {r['t']:>5} {str(r['agree'])+'%':>16} {c:>14} {str(r['osc'])+'%':>12}")
        lk = a.get("lockin_med")
        if lk is not None:
            print(f"  -> lock-in mediano: {hm(lk)} ET ({lk} min desde apertura) — desde aqui el signo ya no cambia hasta el cierre")


# ── HTML interactivo (canvas) ───────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Shape — ¿se repite el día?</title>
<style>
  :root{--bg:#0e1116;--surf:#171c24;--bd:#283039;--tx:#e6edf3;--tx3:#8b949e;--ac:#4f8cff;}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--tx);
    font:14px/1.4 system-ui,Segoe UI,Roboto,sans-serif}
  header{padding:14px 18px;border-bottom:1px solid var(--bd);display:flex;gap:18px;align-items:center;flex-wrap:wrap}
  h1{font-size:16px;margin:0;font-weight:700}
  .sub{color:var(--tx3);font-size:12px}
  .wrap{display:flex;gap:0;height:calc(100vh - 58px)}
  .side{width:230px;border-right:1px solid var(--bd);overflow:auto;padding:12px;flex-shrink:0}
  .main{flex:1;position:relative;padding:10px}
  canvas{width:100%;height:100%;display:block}
  .btn{background:var(--surf);border:1px solid var(--bd);color:var(--tx);border-radius:8px;
    padding:5px 10px;cursor:pointer;font-size:12px}
  .btn.on{background:var(--ac);border-color:var(--ac);color:#fff}
  .sec{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--tx3);margin:14px 0 6px}
  label.row{display:flex;align-items:center;gap:7px;padding:3px 4px;border-radius:6px;cursor:pointer;font-size:12px}
  label.row:hover{background:var(--surf)}
  .dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
  .pos{color:#3fb950}.neg{color:#f85149}
  .legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--tx3)}
  .legend span{display:flex;align-items:center;gap:5px}
</style></head><body>
<header>
  <h1>📈 Daily Shape</h1>
  <span class="sub">¿El intradía se repite día a día? · cada línea = un día · eje Y = % vs apertura (09:30) · eje X = minutos desde apertura</span>
  <span id="tk-btns" style="display:flex;gap:6px;margin-left:auto"></span>
</header>
<div class="wrap">
  <div class="side">
    <div class="sec">Día de la semana</div>
    <div id="wd-btns" style="display:flex;gap:5px;flex-wrap:wrap"></div>
    <div class="sec">Opciones</div>
    <label class="row"><input type="checkbox" id="opt-avg" checked> Curva promedio (visible)</label>
    <label class="row"><input type="checkbox" id="opt-detrend"> Quitar tendencia del día (forma pura)</label>
    <label class="row"><input type="checkbox" id="opt-amp"> Normalizar amplitud</label>
    <label class="row"><input type="checkbox" id="opt-fade" checked> Atenuar días individuales</label>
    <div id="analysis-box" style="margin-top:6px;font-size:11px;color:var(--tx3)"></div>
    <div class="sec">Días (<span id="day-count">0</span>)</div>
    <div style="display:flex;gap:6px;margin-bottom:6px">
      <button class="btn" id="all-on">Todos</button>
      <button class="btn" id="all-off">Ninguno</button>
    </div>
    <div id="day-list"></div>
  </div>
  <div class="main"><canvas id="cv"></canvas></div>
</div>
<script>
const DATA = __DATA__;
const WD_COLOR = ['#4f8cff','#3fb950','#d29922','#bc8cff','#f85149','#888','#888']; // Lun..Dom
let curTk = DATA[0] ? DATA[0].ticker : null;
let wdOn = {0:1,1:1,2:1,3:1,4:1,5:1,6:1};
let dayOn = {};   // date -> bool
let focus = null; // date enfocado

const $ = s => document.querySelector(s);
const cv = $('#cv'), ctx = cv.getContext('2d');

function curDays(){ return (DATA.find(d=>d.ticker===curTk)||{days:[]}).days; }

function initTickerBtns(){
  $('#tk-btns').innerHTML = '';
  DATA.forEach(d=>{
    const b=document.createElement('button'); b.className='btn'+(d.ticker===curTk?' on':'');
    b.textContent=d.ticker+' ('+d.days.length+')';
    b.onclick=()=>{curTk=d.ticker; focus=null; resetDays(); initTickerBtns(); renderList(); renderAnalysis(); draw();};
    $('#tk-btns').appendChild(b);
  });
}
function initWdBtns(){
  const names=['Lun','Mar','Mié','Jue','Vie'];
  $('#wd-btns').innerHTML='';
  names.forEach((n,i)=>{
    const b=document.createElement('button'); b.className='btn'+(wdOn[i]?' on':'');
    b.style.borderColor=WD_COLOR[i]; b.textContent=n;
    b.onclick=()=>{wdOn[i]=wdOn[i]?0:1; b.classList.toggle('on'); renderList(); draw();};
    $('#wd-btns').appendChild(b);
  });
}
function resetDays(){ dayOn={}; curDays().forEach(d=>dayOn[d.date]=true); }
function renderList(){
  const days=curDays();
  $('#day-count').textContent = days.filter(d=>wdOn[d.weekday]).length;
  const L=$('#day-list'); L.innerHTML='';
  days.slice().reverse().forEach(d=>{
    if(!wdOn[d.weekday]) return;
    const lab=document.createElement('label'); lab.className='row';
    const cb=document.createElement('input'); cb.type='checkbox'; cb.checked=!!dayOn[d.date];
    cb.onchange=()=>{dayOn[d.date]=cb.checked; draw();};
    const dot=document.createElement('span'); dot.className='dot'; dot.style.background=WD_COLOR[d.weekday];
    const txt=document.createElement('span');
    txt.innerHTML=`${d.date} <b>${d.wd_name}</b> <span class="${d.last_pct>=0?'pos':'neg'}">${d.last_pct>=0?'+':''}${d.last_pct.toFixed(2)}%</span>`;
    txt.style.cursor='pointer';
    txt.onclick=()=>{focus=(focus===d.date?null:d.date); draw();};
    lab.appendChild(cb); lab.appendChild(dot); lab.appendChild(txt);
    L.appendChild(lab);
  });
}
function visibleDays(){ return curDays().filter(d=>wdOn[d.weekday] && dayOn[d.date]); }

function detrend(pts){
  if(!$('#opt-detrend').checked || pts.length<2) return pts;
  const last=pts[pts.length-1]; const xL=last[0]||1; const yL=last[1];
  return pts.map(p=>[p[0], p[1]-yL*(p[0]/xL)]); // resta la recta apertura->cierre = forma pura
}
function scaleAmp(pts){
  if(!$('#opt-amp').checked) return pts;
  let m=0; pts.forEach(p=>m=Math.max(m,Math.abs(p[1])));
  if(!m) return pts;
  return pts.map(p=>[p[0], p[1]/m*100]); // re-escala cada día a ±100 (compara FORMA)
}
function prep(pts){ return scaleAmp(detrend(pts)); }
function avgCurve(days){
  const bins={}; // x -> [sum,count]
  days.forEach(d=>prep(d.points).forEach(p=>{
    const x=Math.round(p[0]); (bins[x]=bins[x]||[0,0]); bins[x][0]+=p[1]; bins[x][1]++;
  }));
  return Object.keys(bins).map(Number).sort((a,b)=>a-b).map(x=>[x, bins[x][0]/bins[x][1]]);
}

function resize(){ const r=cv.parentElement.getBoundingClientRect();
  cv.width=r.width*devicePixelRatio; cv.height=r.height*devicePixelRatio; draw(); }

function draw(){
  const W=cv.width, H=cv.height, dpr=devicePixelRatio;
  ctx.clearRect(0,0,W,H);
  const days=visibleDays();
  // rango Y
  let yMin=0,yMax=0;
  const series=days.map(d=>prep(d.points));
  series.forEach(s=>s.forEach(p=>{yMin=Math.min(yMin,p[1]);yMax=Math.max(yMax,p[1]);}));
  if(yMin===0&&yMax===0){yMin=-1;yMax=1;}
  const pad=(yMax-yMin)*0.08||1; yMin-=pad; yMax+=pad;
  const padL=54*dpr, padR=14*dpr, padT=14*dpr, padB=28*dpr;
  const X0=0, X1=__SESSION_LEN__;
  const px=x=>padL+(x-X0)/(X1-X0)*(W-padL-padR);
  const py=y=>padT+(yMax-y)/(yMax-yMin)*(H-padT-padB);

  // grid + ejes
  ctx.strokeStyle='#283039'; ctx.fillStyle='#8b949e'; ctx.lineWidth=1*dpr;
  ctx.font=(11*dpr)+'px system-ui'; ctx.textAlign='right'; ctx.textBaseline='middle';
  const ySteps=6;
  for(let i=0;i<=ySteps;i++){ const v=yMin+(yMax-yMin)*i/ySteps, Y=py(v);
    ctx.globalAlpha=(Math.abs(v)<1e-9)?0.6:0.22; ctx.beginPath();ctx.moveTo(padL,Y);ctx.lineTo(W-padR,Y);ctx.stroke();
    ctx.globalAlpha=1; ctx.fillText(v.toFixed(1)+'%', padL-6*dpr, Y); }
  // x labels cada 60 min (9:30,10:30,...)
  ctx.textAlign='center'; ctx.textBaseline='top';
  const lab=['9:30','10:30','11:30','12:30','13:30','14:30','15:30','16:00'];
  for(let i=0;i<lab.length;i++){ const x=Math.min(i*60,X1), X=px(x);
    ctx.globalAlpha=0.18;ctx.beginPath();ctx.moveTo(X,padT);ctx.lineTo(X,H-padB);ctx.stroke();
    ctx.globalAlpha=1;ctx.fillText(lab[i],X,H-padB+5*dpr); }

  // líneas por día
  const fade=$('#opt-fade').checked;
  days.forEach((d,idx)=>{
    const s=series[idx]; if(!s.length) return;
    const isFocus = focus===d.date;
    ctx.strokeStyle=WD_COLOR[d.weekday];
    ctx.globalAlpha = focus ? (isFocus?1:0.08) : (fade?0.32:0.7);
    ctx.lineWidth=(isFocus?2.4:1)*dpr;
    ctx.beginPath(); s.forEach((p,i)=>{ const X=px(p[0]),Y=py(p[1]); i?ctx.lineTo(X,Y):ctx.moveTo(X,Y); }); ctx.stroke();
  });
  // promedio
  if($('#opt-avg').checked && days.length){
    const a=avgCurve(days);
    ctx.globalAlpha=1; ctx.strokeStyle='#fff'; ctx.lineWidth=3*dpr;
    ctx.beginPath(); a.forEach((p,i)=>{const X=px(p[0]),Y=py(p[1]); i?ctx.lineTo(X,Y):ctx.moveTo(X,Y);}); ctx.stroke();
    ctx.strokeStyle='#000'; ctx.lineWidth=1*dpr; ctx.stroke();
  }
  ctx.globalAlpha=1;
  // título de conteo
  ctx.fillStyle='#8b949e'; ctx.textAlign='left'; ctx.textBaseline='top'; ctx.font=(12*dpr)+'px system-ui';
  ctx.fillText(`${curTk} · ${days.length} día(s) visibles · promedio = línea blanca gruesa`, padL, padT-2*dpr);
}
function renderAnalysis(){
  const d=DATA.find(x=>x.ticker===curTk)||{};
  const a=d.analysis||{}; const box=$('#analysis-box');
  if(!a.rows){ box.innerHTML=''; return; }
  const hm=t=>{const tot=570+t;return String(Math.floor(tot/60)).padStart(2,'0')+':'+String(tot%60).padStart(2,'0');};
  // mostrar 3 hitos: 1h, 2h, lock-in
  const at=mm=>a.rows.reduce((b,r)=>Math.abs(r.t-mm)<Math.abs(b.t-mm)?r:b,a.rows[0]);
  const r1=at(60), r2=at(120), r3=at(240);
  box.innerHTML=`<div class="sec" style="margin-top:10px">Compromiso direccional</div>
    <div>coincide c/cierre:</div>
    <div>· 1h (${hm(60)}): <b>${r1.agree}%</b> · corr ${r1.corr}</div>
    <div>· 2h (${hm(120)}): <b>${r2.agree}%</b> · corr ${r2.corr}</div>
    <div>· 4h (${hm(240)}): <b>${r3.agree}%</b> · corr ${r3.corr}</div>
    <div style="margin-top:4px">lock-in mediano: <b>${a.lockin_med!=null?hm(a.lockin_med):'—'}</b></div>`;
}
function renderAll(){ initTickerBtns(); initWdBtns(); renderList(); renderAnalysis(); draw(); }
$('#all-on').onclick=()=>{curDays().forEach(d=>{if(wdOn[d.weekday])dayOn[d.date]=true;}); renderList(); draw();};
$('#all-off').onclick=()=>{curDays().forEach(d=>dayOn[d.date]=false); renderList(); draw();};
['opt-avg','opt-amp','opt-fade','opt-detrend'].forEach(id=>$('#'+id).onchange=draw);
window.addEventListener('resize',resize);
resetDays(); renderAll(); setTimeout(resize,30);
</script></body></html>"""


def main():
    if not _KEY or not _SEC:
        print("ERROR: faltan ALPACA_API_KEY / ALPACA_SECRET_KEY en el .env del proyecto.")
        sys.exit(1)
    tickers = ["WDC", "LITE"]
    lookback = 30
    args = [a for a in sys.argv[1:]]
    if args:
        if "," in args[0] or not args[0].isdigit():
            tickers = [t.strip().upper() for t in args[0].split(",") if t.strip()]
            args = args[1:]
        if args and args[0].isdigit():
            lookback = int(args[0])
    print(f"Daily Shape - tickers={tickers} lookback={lookback}d")
    out = []
    for tk in tickers:
        d = build_ticker(tk, lookback)
        d["analysis"] = analyze_commitment(tk, d["days"]) if d["days"] else {}
        out.append(d)
    print_analysis(out)
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    html = (_HTML
            .replace("__DATA__", json.dumps(out))
            .replace("__SESSION_LEN__", str(SESSION_LEN_MIN)))
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nOK -> {OUT_HTML}")
    print("Abrelo en el navegador. Tip: filtra por dia de semana y mira la curva blanca (promedio).")


if __name__ == "__main__":
    main()
