// 10.5.0: aufbereiteter Diagnosebericht aus ZUSAMMENFASSUNG.json der verifizierten ZIP (nur lesend).
const rnum=(n,d=0)=>(typeof n==='number'&&Number.isFinite(n))?n.toLocaleString('de-DE',{maximumFractionDigits:d}):'unbekannt';
const rpct=(n,d=1)=>(typeof n==='number'&&Number.isFinite(n))?(n*100).toLocaleString('de-DE',{maximumFractionDigits:d,minimumFractionDigits:d})+' %':'unbekannt';
const rtime=v=>{if(!v)return 'unbekannt';const d=new Date(typeof v==='number'?v*1000:v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString('de-DE',{dateStyle:'short',timeStyle:'short'})};
const completionLabel={COMPLETED:'Vollständige Sammlung',COMPLETED_WITH_EXPORT_ERRORS:'Abgeschlossen mit Exportfehlern',INTERRUPTED_PARTIAL:'Unterbrochen (Teilbericht)',PARTIAL_ERROR:'Teilbericht mit Fehler',SOURCE_NOT_FOUND:'Quellordner nicht gefunden',RUNNING:'Läuft'};
const levelLabel={ERROR:'Fehler',WARN:'Warnung',UNKNOWN:'Unbekannt / Belegluecke',INFO:'Hinweis'};
function kpi(label,value,note){return '<div class="kpi"><strong>'+esc(value)+'</strong>'+esc(label)+(note?'<br><span>'+esc(note)+'</span>':'')+'</div>'}
function renderKpis(s,job){
  const f=s.findings||{},c=f.counts||{};
  return '<div class="kpi-grid">'+kpi('Laufabschluss',completionLabel[s.completion]||s.completion||'unbekannt',s.observation_mode==='TIMED_OBSERVATION'?'30-Minuten-Beobachtung':'Sofortexport')+kpi('Beobachtet',(typeof s.observed_seconds==='number'?rnum(s.observed_seconds/60,1)+' min':'unbekannt'),rnum(s.sample_count)+' Messpunkte')+kpi('Warnungen',rnum(c.WARN||0),'Fehler '+rnum(c.ERROR||0)+' · Unbekannt '+rnum(c.UNKNOWN||0)+' · Hinweise '+rnum(c.INFO||0))+kpi('Entscheidungen im Fenster',rnum((s.decisions||{}).rows_in_window),'Export '+esc((s.decisions||{}).export_status||'unbekannt'))+kpi('GPT gestartet / erfolgreich',rnum((s.gpt||{}).local_dispatches_in_window)+' / '+rnum((s.gpt||{}).successful_results_in_window),'Cache '+rnum((s.gpt||{}).cache_uses_in_window)+' · Fehler '+rnum((s.gpt||{}).failed_timed_out_discarded_in_window))+kpi('Erfassungsfehler',rnum((s.errors||[]).length),'FEHLER.json')+'</div>'+(s.fallback?'<p class="alert warn small">Ältere Diagnose ohne Zusammenfassungsdatei: nur Befunde und Metadaten verfügbar.</p>':'');
}
function renderRuntime(r){
  const keys=Object.keys(r||{});if(!keys.length)return '<p class="small">Keine Laufzeitbelege im Export.</p>';
  return '<div class="table-wrap"><table><thead><tr><th>Broker</th><th>Heartbeat</th><th>Laufzustand</th><th>Käufe</th><th>Grund</th><th>Risikokapital</th></tr></thead><tbody>'+keys.map(k=>{const x=r[k]||{};const buys=x.buys_allowed===true?'<span class="status ok">ERLAUBT</span>':x.buys_allowed===false?'<span class="status warn">GESPERRT</span>':'<span class="status">UNBEKANNT</span>';return '<tr><th>'+esc(k.toUpperCase())+'</th><td>'+esc(x.freshness||'unbekannt')+'</td><td>'+esc(x.state||'unbekannt')+'</td><td>'+buys+'</td><td>'+esc(x.buy_reason||'–')+'</td><td>'+(x.tradable_capital!=null?rnum(x.tradable_capital,2)+' '+esc(x.capital_currency||''):'unbekannt')+'</td></tr>'}).join('')+'</tbody></table></div>';
}
function renderMeasurement(m){
  const v=(m||{}).verdict||{};const b=document.querySelector('#r-verdict');
  const cls={ERFOLGREICH:'verdict-ok',NICHT_ERFOLGREICH:'verdict-bad',UNKLAR:'verdict-warn',ZU_WENIG_DATEN:'verdict-none'}[v.status]||'verdict-none';
  if(b){b.className='verdict '+cls;b.textContent=({ERFOLGREICH:'ERFOLGREICH',NICHT_ERFOLGREICH:'NICHT ERFOLGREICH',UNKLAR:'UNKLAR',ZU_WENIG_DATEN:'ZU WENIG DATEN'})[v.status]||'KEINE MESSUNG'}
  if(!m||m.total==null)return '<p class="small">Keine Messdaten im Export (Tabelle attention_outcomes fehlt oder Version vor 10.5.0).</p>';
  const counts=m.status_counts||{};
  let html='<p><strong>'+rnum(m.total)+' Auslöser gemessen</strong> · vollständig '+rnum(counts.COMPLETE||0)+' · laufend '+rnum((counts.OPEN||0)+(counts.PARTIAL||0))+' · ohne Kurs '+rnum(counts.UNRESOLVABLE||0)+'<br><span class="small">'+esc(v.reason||'')+'</span></p>';
  const h=m.horizons||{};
  if(Object.keys(h).length)html+='<div class="table-wrap"><table><thead><tr><th>Horizont</th><th>Messungen</th><th>Trefferquote</th><th>Median</th><th>Median nach Kosten</th></tr></thead><tbody>'+Object.keys(h).map(k=>{const s=h[k]||{};return '<tr><th>'+esc(k)+' Handelstage</th><td>'+rnum(s.n)+'</td><td>'+rpct(s.hit_rate,0)+'</td><td>'+rpct(s.median)+'</td><td>'+rpct(s.median_after_costs)+'</td></tr>'}).join('')+'</tbody></table></div>';
  const groups=[['Nach Auslöserart',m.by_trigger],['Nach Squeeze-Merkmal',m.by_squeeze],['Kandidat gegen nur Auslöser',m.by_eligible]];
  for(const [title,g] of groups){const keys=Object.keys(g||{});if(!keys.length)continue;html+='<h4>'+esc(title)+'</h4><div class="table-wrap"><table><thead><tr><th>Gruppe</th><th>Messungen</th><th>Trefferquote 5 Tage</th><th>Median nach Kosten</th></tr></thead><tbody>'+keys.map(k=>{const s=(g[k]||{})['5']||{};return '<tr><th>'+esc(k)+'</th><td>'+rnum(g[k].total)+' ('+rnum(s.n)+' vollständig)</td><td>'+rpct(s.hit_rate,0)+'</td><td>'+rpct(s.median_after_costs)+'</td></tr>'}).join('')+'</tbody></table></div>';}
  const recent=m.recent||[];
  if(recent.length)html+='<details data-detail-key="r-recent"><summary>Letzte Messungen ('+rnum(recent.length)+')</summary><div class="table-wrap"><table><thead><tr><th>Aktie</th><th>Tag</th><th>Auslöser</th><th>Kurs</th><th>+1</th><th>+3</th><th>+5</th><th>+10</th><th>Stand</th></tr></thead><tbody>'+recent.map(r=>'<tr><td>'+esc(r.symbol)+'</td><td>'+esc(r.trigger_day)+'</td><td>'+esc(r.trigger_kind)+'</td><td>'+rnum(r.price_at,4)+'</td><td>'+rpct(r.r1)+'</td><td>'+rpct(r.r3)+'</td><td>'+rpct(r.r5)+'</td><td>'+rpct(r.r10)+'</td><td>'+esc(r.status)+'</td></tr>').join('')+'</tbody></table></div></details>';
  return html+'<p class="small">'+esc(m.meaning||'')+'</p>';
}
function renderFindings(f,errors){
  const items=(f||{}).items||[];
  const order={ERROR:0,WARN:1,UNKNOWN:2,INFO:3};
  const sorted=[...items].sort((a,b)=>(order[a.level]??9)-(order[b.level]??9));
  let html='';
  if((errors||[]).length)html+='<h4>Erfassungsfehler des Diagnoselaufs</h4>'+errors.map(e=>'<div class="finding finding-ERROR"><strong>'+esc(e.type||e.error||'Fehler')+'</strong><br><span class="small">'+esc(e.message||e.detail||'')+'</span></div>').join('');
  if(!sorted.length)return html+'<p class="small">Keine Befunde in der ZIP. Das ist kein pauschales PASS, sondern: keine auffälligen Zustände in den exportierten Belegen.</p>';
  const grouped={};for(const it of sorted){(grouped[it.level||'INFO']=grouped[it.level||'INFO']||[]).push(it)}
  for(const level of ['ERROR','WARN','UNKNOWN','INFO']){const list=grouped[level];if(!list)continue;html+='<h4>'+esc(levelLabel[level]||level)+' ('+list.length+')</h4>'+(level==='INFO'?'<details data-detail-key="r-info"><summary>Hinweise anzeigen</summary>':'')+list.map(it=>'<div class="finding finding-'+esc(level)+'"><strong>'+esc(it.code||'')+'</strong> · '+esc(it.component||'')+(it.symbol?' · '+esc(it.symbol):'')+(it.scope?' · '+esc(it.scope):'')+(it.meaning?'<br><span class="small">'+esc(it.meaning)+'</span>':'')+'</div>').join('')+(level==='INFO'?'</details>':'');}
  return html;
}
function renderDecisions(d){
  const blocks=(d||{}).block_reasons||{};const keys=Object.keys(blocks).sort((a,b)=>(blocks[b]||0)-(blocks[a]||0));
  if(!keys.length)return '<p class="small">Keine Entscheidungsblocker im Auswertungsfenster exportiert ('+rnum((d||{}).rows_in_window)+' Entscheidungen).</p>';
  return '<p class="small">'+rnum(d.rows_in_window)+' Entscheidungen im Fenster. Ein Blocker ist kein Brokerfehlerbeweis, sondern die NEXUS-Prüfung, an der die Entscheidung endete.</p><div class="table-wrap"><table><thead><tr><th>Blocker</th><th>Anzahl</th></tr></thead><tbody>'+keys.slice(0,30).map(k=>'<tr><th>'+esc(k)+'</th><td>'+rnum(blocks[k])+'</td></tr>').join('')+'</tbody></table></div>';
}
function renderSources(s){
  const src=s.sources||[],ex=s.database_exports||{},cards=(s.pulsar||{}).cards||[];
  let html='<h4>Quellen</h4>'+(src.length?'<div class="chips">'+src.map(x=>'<span class="chip '+(x.state==='ok'||x.state==='OK'?'chip-ok':/error|backoff|paused/i.test(x.state||'')?'chip-block':'chip-unknown')+'">'+esc(x.provider)+' · '+esc(x.state||'unbekannt')+'</span>').join('')+'</div>':'<p class="small">Keine Quellenstände im Export.</p>');
  html+='<h4>Datenbankexporte</h4><div class="chips">'+Object.entries(ex).map(([k,v])=>'<span class="chip '+(v==='OK'?'chip-ok':v==='MISSING'?'chip-unknown':'chip-open')+'">'+esc(k)+' · '+esc(v||'unbekannt')+'</span>').join('')+'</div>';
  html+='<h4>PULSAR-Karten im Export</h4>'+(cards.length?'<div class="table-wrap"><table><thead><tr><th>Aktie</th><th>Zustand</th><th>Hype-Kandidat</th><th>Blöcke</th></tr></thead><tbody>'+cards.map(c=>'<tr><td>'+esc(c.symbol)+'</td><td>'+esc(c.state||'')+'</td><td>'+(c.eligible?'ja':'nein')+'</td><td class="small">'+esc((c.blocks||[]).join('; ')||'–')+'</td></tr>').join('')+'</tbody></table></div>':'<p class="small">Keine Karten im Export ('+esc((s.pulsar||{}).cards_status||'unbekannt')+').</p>');
  return html;
}
async function loadReport(){
  const id=document.querySelector('meta[name="diagnosis-id"]')?.content||'';
  const message=document.querySelector('#r-message');
  try{
    const d=await api('/api/diagnosis/'+encodeURIComponent(id)+'/summary');
    const s=d.summary||{},job=d.job||{};
    document.querySelector('#r-title').textContent=(job.mode==='instant'?'Sofortdiagnose':'30-Minuten-Diagnose')+' · '+(completionLabel[s.completion]||s.completion||'unbekannt');
    document.querySelector('#r-subtitle').textContent='Gestartet '+rtime(job.created_at)+' · Zeitfenster '+esc(s.started_utc||'?')+' bis '+esc(s.ended_utc||'?')+' · Werkzeug '+esc(s.tool_version||'?')+' · ZIP '+esc((d.archive||{}).name||'')+' ('+rnum(((d.archive||{}).size||0)/1048576,1)+' MB, '+rnum((d.archive||{}).members)+' Dateien)';
    document.querySelector('#r-download').href='/api/diagnosis/'+encodeURIComponent(id)+'/download';
    document.querySelector('#r-kpis').innerHTML=renderKpis(s,job);
    document.querySelector('#r-runtime').innerHTML=renderRuntime(s.runtime);
    document.querySelector('#r-measurement').innerHTML=renderMeasurement((s.pulsar||{}).measurement);
    document.querySelector('#r-findings').innerHTML=renderFindings(s.findings,s.errors);
    document.querySelector('#r-decisions').innerHTML=renderDecisions(s.decisions);
    document.querySelector('#r-sources').innerHTML=renderSources(s);
    document.querySelector('#r-report').textContent=d.report_markdown||'BERICHT.md nicht in der ZIP enthalten.';
    document.querySelector('#r-limits').textContent=(s.limits||[]).join(' · ')||(s.meaning||'');
  }catch(e){message.textContent='Bericht nicht lesbar: '+e.message;}
}
loadReport();
