// PULSAR-Seite 10.5.0: Messung zuerst, dann Kandidaten (Ausloeser + Bestaetigungen), dann Handel.
let pulsarSnapshot={};
const pf=(n,d=2)=>!knownNumber(n)?'unbekannt':Number(n).toLocaleString('de-DE',{maximumFractionDigits:d});
const pct=(n,d=1)=>!knownNumber(n)?'unbekannt':(Number(n)*100).toLocaleString('de-DE',{maximumFractionDigits:d,minimumFractionDigits:d})+' %';
const pt=s=>s?new Date(Number(s)*1000).toLocaleString('de-DE'):'noch nicht erhoben';
const safeURL=u=>{try{const url=new URL(u);return url.protocol==='https:'?url.href:''}catch{return ''}};
function pulsarSourceLink(src,symbol){
 if(String(src.provider).toUpperCase()==='FMP')return /^[0-9a-f]{64}$/.test(src.id||'')?` · <a href="/api/pulsar/source/${encodeURIComponent(symbol)}/${src.id}" target="_blank" rel="noopener noreferrer">Gespeicherten Beleg öffnen</a>`:' · Gespeicherter Beleg nicht verfügbar';
 const url=safeURL(src.url);return url?' · <a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">Quelle öffnen</a>':'';
}
async function savePulsar(){
  const target=document.querySelector('#p-message');
  try{await api('/api/pulsar/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:document.querySelector('#p-mode').value,tradestie:document.querySelector('#p-tradestie').checked,web_search:document.querySelector('#p-web-search').checked,stocktwits:!!document.querySelector('#p-stocktwits')?.checked,finra:!!document.querySelector('#p-finra')?.checked})});target.textContent='Einstellung gespeichert. Bestehende Freigaben wurden widerrufen; offene Trades werden weiter verwaltet.';await loadPulsar()}catch(e){target.textContent=e.message}
}
function chartPulsar(symbol,days){
  const card=(pulsarSnapshot.cards||[]).find(c=>c.symbol===symbol),target=document.getElementById(`p-chart-${symbol}`);
  if(!card||!target)return;
  const intraday=days===1 || days<=5 && (card.intraday||[]).length>1;
  const all=(intraday?card.intraday||[]:card.bars||[]).filter(b=>Number(b.close)>0&&Number.isFinite(Number(b.close))).sort((a,b)=>a.date.localeCompare(b.date));
  const dates=[...new Set(all.map(b=>String(b.date).slice(0,10)))].slice(-days);
  const bars=intraday?all.filter(b=>dates.includes(String(b.date).slice(0,10))):all.slice(-days);
  if(bars.length<2){target.innerHTML='<p class="small">Für diesen Zeitraum fehlen Kursdaten. Es wird kein Kursverlauf ergänzt.</p>';return}
  const width=600,height=190,min=Math.min(...bars.map(b=>Number(b.close))),max=Math.max(...bars.map(b=>Number(b.close))),span=Math.max(max-min,max*.001);
  const points=bars.map((b,i)=>`${35+i*(width-50)/(bars.length-1)},${15+(max-Number(b.close))/span*(height-40)}`).join(' ');
  const trades=[...(pulsarSnapshot.open_trades||[]),...(pulsarSnapshot.archive||[])].filter(t=>t.symbol===symbol);
  let markers='';
  for(const t of trades){
    for(const [field,label,color] of [['eingestiegen_am','Kauf','#69dabb'],['ausgestiegen_am','Verkauf','#f8ac79']]){
      if(!t[field]||!/(Z|[+-]\d{2}:\d{2})$/.test(String(t[field])))continue;
      const stamp=new Date(t[field]);if(!Number.isFinite(stamp.getTime()))continue;
      const date=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(stamp);
      const i=bars.findIndex(b=>intraday?(new Date(b.date).getTime()<=stamp.getTime()&&stamp.getTime()<new Date(b.date).getTime()+900000):String(b.date).slice(0,10)===date);
      if(i<0)continue;
      const x=35+i*(width-50)/(bars.length-1);
      markers+=`<line x1="${x}" x2="${x}" y1="12" y2="${height-20}" stroke="${color}" stroke-dasharray="4 4"><title>${label}: ${esc(t[field])}</title></line>`;
    }
  }
  target.innerHTML=`<svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${intraday?'15-Minuten':'Tages'}-Schlusskurse ${esc(symbol)}"><polyline points="${points}" fill="none" stroke="#64c8ff" stroke-width="2"/>${markers}<text x="4" y="14" fill="currentColor" font-size="11">${pf(max)}</text><text x="4" y="${height-20}" fill="currentColor" font-size="11">${pf(min)}</text></svg><p class="small">FMP-${intraday?'15-Minuten-Kerzen':'Tageskurse'} · ${esc(bars[0].date)} bis ${esc(bars.at(-1).date)}. Markierungen stammen ausschließlich aus verbuchten Ausführungen.</p>`;
}
function tradeRows(rows){
  if(!rows.length)return '<p class="small">Noch keine verbuchten PULSAR-Ausführungen.</p>';
  return `<div class="table-wrap"><table><thead><tr><th>Aktie</th><th>Umgebung</th><th>Menge</th><th>Einstand</th><th>Ergebnis</th></tr></thead><tbody>${rows.map(t=>`<tr><td>${esc(t.symbol)}</td><td>${esc(({DEMO:'DEMO',LIVE:'LIVE',CONFLICT:'Widersprüchlich'})[rowContext(t,'etoro').environment]||'Umgebung unbekannt')}</td><td>${pf(t.menge,6)}</td><td>${pf(t.einstieg_preis,4)} ${esc(t.waehrung)}</td><td>${t.ausgestiegen_am?(['CONFIRMED','BROKER_CONFIRMED','KNOWN','USER_CONFIRMED','CASH_DELTA_CONFIRMED'].includes(t.fee_quality)?pf(t.netto_pnl)+' '+esc(t.waehrung):'Netto offen · Brutto '+pf(t.brutto_pnl)+' '+esc(t.waehrung)):'offen'}</td></tr>`).join('')}</tbody></table></div>`;
}
function pulsarXContextView(x){
  if(!x||!Object.keys(x).length)return '';
  const a=x.attention||{},events=Array.isArray(x.argument_clusters)?x.argument_clusters:[];
  const count=knownNumber(a.count)?pf(a.count,0):'unbekannt';
  return '<details data-detail-key="x-context-'+esc(x.symbol||'unknown')+'"><summary>X · Zusätzlicher Recherchekontext</summary><p class="small">Getrennt von Reddit beobachtet; keine kalibrierte gemeinsame Aufmerksamkeit und keine automatische Orderfreigabe.</p><p>Treffer im beobachteten Suchfenster: '+count+' · '+esc(a.day||'Zeitraum nicht belegt')+'<br>Vollständige Vergleichstage: '+pf(a.complete_days,0)+' · '+(a.baseline_ready===true?'Vergleichsbasis vorhanden':'Vergleichsbasis noch nicht belastbar')+'</p><p class="small">Erhoben: '+pt(a.observed_at)+' · '+esc(a.coverage_status||x.coverage_status||'Abdeckung nicht belegt')+(a.stale===true?' · älterer Stand':'')+'</p>'+(events.length?events.slice(0,10).map(e=>'<p><strong>'+esc(e.title||e.event_type||'Unbestätigter Hinweis')+'</strong><br><span class="small">Unbestätigt · '+esc((e.affected_symbols||[]).join(', '))+' · Beleg '+esc(e.event_id||'nicht belegt')+'</span></p>').join(''):'<p class="small">Keine gespeicherten Argumentgruppen; das beweist keine Abwesenheit von Meldungen.</p>')+(x.ai_context_status==='OMITTED_INPUT_LIMIT'?'<p class="small">X-Kontext wegen Eingabegrenze nicht an die GPT-Vorprüfung übergeben.</p>':'')+'</details>';
}
function pulsarAttentionView(c){
  const a=c.attention||{},coverage=c.attention_coverage||{},sample=c.x_discovery||a.x_discovery||{};
  const source=String(a.source||coverage.source||'').toLowerCase();
  const isX=source==='x';
  let headline;
  if(isX)headline='<strong>X · '+pf(sample.sampled_post_count??a.sampled_post_count,0)+' Posts in der Entdeckungsstichprobe</strong><br>Vollständig gemessene Tageszahl: '+pf(a.mentions,0);
  else if(source==='volume_watch')headline='<strong>Volumen-Auslöser · '+pf(a.rvol,1)+'× des üblichen Volumens</strong>'+(knownNumber(a.gain)?' · Kurs '+pct(a.gain):'')+'<br>Reddit-Erwähnungen: '+pf(a.mentions,0);
  else if(source==='stocktwits_trending')headline='<strong>StockTwits-Trending · Platz '+pf(a.rank,0)+'</strong> · Watchlist '+pf(a.watchlist_count,0)+'<br>Reddit-Erwähnungen: '+pf(a.mentions,0);
  else headline='<strong>'+pf(a.mentions,0)+' Erwähnungen</strong> · vorher '+pf(a.mentions_24h_ago,0);
  return '<p>'+headline+' · Wachstum '+(a.growth_ratio==null?'noch nicht vergleichbar':pf(a.growth_ratio)+'×')+(isX||source==='volume_watch'||source==='stocktwits_trending'?'':' · Rang '+pf(a.rank,0))+'</p><p class="small">Beobachtet '+pt(a.observed_at)+' · '+esc(coverage.detail||c.baseline?.detail||'Vergleichsabdeckung noch unbekannt')+'</p>';
}
function pulsarStocktwitsView(c){
  const s=c.stocktwits;
  if(!s)return '<p class="small">StockTwits: nicht abgerufen oder nicht erreichbar (UNKNOWN).</p>';
  return '<p class="small">StockTwits: '+pf(s.messages_1h,0)+' Nachrichten/h von '+pf(s.authors_1h,0)+' Konten · Bullish '+pf(s.bullish_1h,0)+' / Bearish '+pf(s.bearish_1h,0)+(s.truncated_1h?' · Untergrenze (Stichprobe voll)':'')+' · '+esc((c.hype?.stocktwits||{}).detail||s.detail||'')+'</p>';
}
function chip(label,ok,detail){
  const cls=ok===true?'chip chip-ok':ok===false?'chip chip-open':'chip chip-unknown';
  return '<span class="'+cls+'" title="'+esc(detail||'')+'">'+esc(label)+' · '+(ok===true?'belegt':ok===false?'offen':'unbekannt')+'</span>';
}
function pulsarHypeView(c){
  const h=c.hype||{},t=h.trigger,conf=h.confirmations||{},sq=h.squeeze||{},ex=h.existence_risk||{};
  const state=c.eligible?'<span class="status ok">HYPE-KANDIDAT · Kriterien erfüllt, keine Orderfreigabe</span>':t?'<span class="status warn">AUSLÖSER · wird gemessen</span>':'<span class="status">BEOBACHTUNG</span>';
  const trigger=t?'<p><strong>Auslöser:</strong> '+esc(t.kind)+'<br><span class="small">'+esc(t.detail||'')+'</span></p>':'<p class="small">Kein Auslöser (Volumen ≥ 3×, Reddit-, StockTwits- oder X-Spike).</p>';
  const chips=Object.keys(conf).length?'<div class="chips">'+chip('Volumen',conf.volumen?.ok,conf.volumen?.detail)+chip('Zweite Social-Familie',conf.zweite_social_familie?.ok,conf.zweite_social_familie?.detail)+chip('Kurs',conf.kurs?.ok,conf.kurs?.detail)+'<span class="chip chip-info">'+pf(h.confirmed_count,0)+' von 3</span></div>':'';
  const risk='<div class="chips">'+(ex.blocked?'<span class="chip chip-block">Existenzrisiko · BLOCKIERT</span>':'<span class="chip chip-ok">Existenzrisiko · kein Befund</span>')+'<span class="chip '+(sq.flag===true?'chip-info':sq.flag===false?'chip-unknown':'chip-unknown')+'" title="'+esc(sq.detail||'')+'">Squeeze-Merkmal · '+(sq.flag===true?'ja':sq.flag===false?'nein':'unbekannt')+'</span>'+(h.finance_note?'<span class="chip chip-unknown" title="'+esc(h.finance_note)+'">Bilanz · nur Information</span>':'')+'</div>';
  return '<p>Hype-Spur: '+(c.eligible?'Kriterien erfüllt — noch keine Orderfreigabe, persönliche Bestätigung nötig':'Kriterien offen')+'</p>'+state+trigger+chips+risk+(h.social?.detail?'<p class="small">'+esc(h.social.detail)+'</p>':'')+(h.price?.detail?'<p class="small">'+esc(h.price.detail)+'</p>':'');
}
function renderPulsarCard(c,i){
  const labels={identitaet:'Identität und Qualitätsgrenzen',existenzrisiko:'Existenzrisiko',ausloeser:'Auslöser',volumen:'Relatives Volumen',zweite_social_familie:'Zweite Social-Familie',social_spike:'Social-Spike',kursbestaetigung:'Kurs- und Volumenbestätigung',squeeze_merkmal:'Squeeze-Merkmal (Information)',luna_warnfilter:'GPT-Warnfilter (Luna)',earnings:'Nächste Quartalszahlen',broker:'Konto und Ausführung'};
  const refs=(c.sources||[]).map(src=>`<p class="small" data-source-id="${esc(src.id||'')}">${esc(src.provider)} · ${esc(src.kind||'Aufmerksamkeit')} · ${pt(src.observed_at)}${pulsarSourceLink(src,c.symbol)}<br>Beleg ${esc(src.id||'unbekannt')}</p>`).join('');
  const claims=(c.claims||[]).map(claim=>`<li>${esc(claim.text)}<br><span class="small">Belege: ${(claim.source_ids||[]).map(esc).join(', ')}</span></li>`).join('');
  const checks=(c.checks||[]).map(check=>`<tr><th>${esc(labels[check.name]||check.name)}</th><td>${esc(check.status)}${check.detail?' · '+esc(check.detail):''}</td></tr>`).join('');
  const event=c.verified_evidence?.catalyst||{}, web=c.web_research||{};
  const gaps=[...new Set([...(c.blocks||[]),...(c.missing||[]),...(c.errors||[])].map(x=>/^Luna-(Pruefung|Vorpruefung) verwirft den Kandidaten$/.test(x)?'GPT-Vorprüfung: Kandidat abgelehnt':x))];
  const sourceProviders=[...new Set((c.sources||[]).map(src=>src.provider).filter(Boolean))];
  return `<article class="panel pulsar-card"><div class="section-head"><h3>${i+1}. ${esc(c.symbol)} · ${esc(c.name)}</h3><span class="status warn">${esc(c.state)}</span></div>
    <p class="small">${esc(c.text_source||'DATENUEBERSICHT')} · ${esc(c.analysis_status||'Individuelle Prüfung steht aus')}</p>${aiExecutionView(c.precheck||{},'GPT-Vorprüfung',true)}<p>${esc(c.thesis)}</p>
    ${pulsarHypeView(c)}
    ${pulsarAttentionView(c)}${pulsarStocktwitsView(c)}<p class="small">${c.tradestie?'Tradestie: '+esc(c.tradestie.sentiment||'unbekannt')+' (überlappende Foren)':'Tradestie nicht verfügbar oder ausgeschaltet'}</p>
    <div class="source-summary">${sourceProviders.map(provider=>badge(provider+' · Beleg vorhanden')).join('')||badge('Keine Quellenbelege verfügbar','warn')}</div>
    ${(c.risks||[]).length?'<ul>'+c.risks.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':''}
    <div class="toolbar">${[[1,'1 Tag'],[5,'5 Tage'],[22,'1 Monat'],[66,'3 Monate'],[252,'1 Jahr'],[1260,'5 Jahre']].map(([n,label])=>`<button class="secondary" data-chart-symbol="${esc(c.symbol)}" data-days="${n}">${label}</button>`).join('')}</div><div id="p-chart-${esc(c.symbol)}"></div>
    <details data-detail-key="pulsar-${esc(c.symbol)}"><summary>Prüfungen, Datenlücken und Quellen</summary>${checks?'<div class="table-wrap"><table><tbody>'+checks+'</tbody></table></div>':''}<ul>${gaps.map(x=>'<li>'+esc(x)+'</li>').join('')}</ul>
    ${aiExecutionView(c.precheck||{},'Vorprüfung')}${pulsarXContextView(c.x_context)}${pulsarXResearchHint(c.x_context?.candidate_research)}
    ${event.excerpt?'<p><strong>Originalmeldung · '+esc(event.event_type)+'</strong><br>'+esc(event.summary)+'</p><blockquote>'+esc(event.excerpt)+'</blockquote><p class="small">Meldungsdatum: '+esc(event.event_time)+'</p>':''}<p class="small">GPT-Websuche: ${esc(web.detail||'Recherche steht aus')}${web.observed_at?' · '+pt(web.observed_at):''}${web.cached?' · gespeichert':''}</p>
    <p class="small">${esc(c.baseline?.detail||'Keine vollständige Tages- oder Instrumentabdeckung belegt.')}</p>${typeof fmpContextView==='function'?fmpContextView(c.fmp_context||{}):''}${Object.keys(c.provider_status||{}).length?'<h4>Quellenabrufe dieses Kandidaten</h4>'+Object.entries(c.provider_status).map(([name,source])=>'<p class="small"><strong>'+esc(name)+'</strong> · '+esc(({NOT_REQUESTED_FMP_SUFFICIENT:'Kein Zusatzabruf: vorhandene FMP-News reichen für dieses Paket',CACHE:'Vorhandene Daten wiederverwendet',SUPPLEMENT_FOR_DATA_GAP:'Ergänzung einer Datenlücke',PAUSED:'Abruf wartet wegen Anbietergrenze',UNAVAILABLE:'Quelle nicht verfügbar'})[source.status]||source.status||source.state||'Nicht belegt')+'<br>'+esc(source.reason||source.code||'')+'</p>').join(''):''}${claims?'<h4>Aussagen mit Quellenzuordnung</h4><ul>'+claims+'</ul>':''}${refs}</details></article>`;
}
function verdictClass(status){return ({ERFOLGREICH:'verdict-ok',NICHT_ERFOLGREICH:'verdict-bad',UNKLAR:'verdict-warn',ZU_WENIG_DATEN:'verdict-none'})[status]||'verdict-none'}
function verdictLabel(status){return ({ERFOLGREICH:'ERFOLGREICH',NICHT_ERFOLGREICH:'NICHT ERFOLGREICH',UNKLAR:'UNKLAR',ZU_WENIG_DATEN:'ZU WENIG DATEN'})[status]||'NOCH KEINE MESSUNG'}
function horizonTable(h){
  const keys=Object.keys(h||{});if(!keys.length)return '';
  return '<div class="table-wrap"><table><thead><tr><th>Horizont</th><th>Messungen</th><th>Trefferquote</th><th>Median</th><th>Median nach Kosten</th><th>Schlechteste · Beste</th></tr></thead><tbody>'+keys.map(k=>{const s=h[k]||{};return '<tr><th>'+esc(k)+' Handelstag'+(k==='1'?'':'e')+'</th><td>'+pf(s.n,0)+'</td><td>'+pct(s.hit_rate,0)+'</td><td>'+pct(s.median)+'</td><td>'+pct(s.median_after_costs)+'</td><td>'+pct(s.worst)+' · '+pct(s.best)+'</td></tr>'}).join('')+'</tbody></table></div>';
}
function groupTable(title,groups,horizon){
  const keys=Object.keys(groups||{});if(!keys.length)return '';
  return '<h4>'+esc(title)+'</h4><div class="table-wrap"><table><thead><tr><th>Gruppe</th><th>Messungen</th><th>Trefferquote '+esc(horizon)+' Tage</th><th>Median nach Kosten</th></tr></thead><tbody>'+keys.map(k=>{const s=(groups[k]||{})[String(horizon)]||{};return '<tr><th>'+esc(k)+'</th><td>'+pf(groups[k].total,0)+' ('+pf(s.n,0)+' vollständig)</td><td>'+pct(s.hit_rate,0)+'</td><td>'+pct(s.median_after_costs)+'</td></tr>'}).join('')+'</tbody></table></div>';
}
function renderMeasurement(m,demo){
  const v=(m||{}).verdict||{};
  const badge_=document.querySelector('#p-verdict');
  if(badge_){badge_.className='verdict '+verdictClass(v.status);badge_.textContent=verdictLabel(v.status);}
  if(!m||m.error)return '<p class="small">Messung nicht lesbar: '+esc(m?.error||'unbekannt')+'</p>';
  const counts=m.status_counts||{};
  const head='<p><strong>'+pf(m.total,0)+' Auslöser gemessen</strong> · vollständig '+pf(counts.COMPLETE||0,0)+' · laufend '+pf((counts.OPEN||0)+(counts.PARTIAL||0),0)+' · ohne Kurs '+pf(counts.UNRESOLVABLE||0,0)+'<br><span class="small">'+esc(v.reason||'')+'</span></p>';
  const demoText=demo?'<p><strong>Demo-Trades:</strong> '+pf(demo.closed,0)+' abgeschlossen ('+pf(demo.wins,0)+' Gewinn / '+pf(demo.losses,0)+' Verlust'+(demo.net_unknown?' / '+pf(demo.net_unknown,0)+' Netto offen':'')+'), offen '+pf(demo.open,0)+(Object.keys(demo.net_by_currency||{}).length?' · Netto '+Object.entries(demo.net_by_currency).map(([k,val])=>pf(val)+' '+esc(k)).join(', '):'')+'<br><span class="small">'+esc(demo.detail||'')+'</span></p>':'';
  return head+horizonTable(m.horizons)+groupTable('Nach Auslöserart',m.by_trigger,m.primary_horizon_days||5)+groupTable('Nach Squeeze-Merkmal',m.by_squeeze,m.primary_horizon_days||5)+groupTable('Hype-Kandidat gegen nur Auslöser',m.by_eligible,m.primary_horizon_days||5)+demoText+'<p class="small">'+esc(v.rule||'')+' '+esc(m.detail||'')+'</p>';
}
function measurementRows(rows){
  if(!rows.length)return '<p class="small">Noch keine Messungen. Sie entstehen im Modus Beobachten oder Freigabe mit dem ersten Auslöser.</p>';
  return '<div class="table-wrap"><table><thead><tr><th>Aktie</th><th>Tag</th><th>Auslöser</th><th>Kurs</th><th>Kandidat</th><th>Squeeze</th><th>+1</th><th>+3</th><th>+5</th><th>+10</th><th>Stand</th></tr></thead><tbody>'+rows.map(r=>'<tr><td>'+esc(r.symbol)+'</td><td>'+esc(r.trigger_day)+'</td><td>'+esc(r.trigger_kind)+'</td><td>'+pf(r.price_at,4)+'</td><td>'+(r.eligible?'ja':'nein')+'</td><td>'+(r.squeeze===1||r.squeeze===true?'ja':r.squeeze===0||r.squeeze===false?'nein':'unbekannt')+'</td><td>'+pct(r.r1)+'</td><td>'+pct(r.r3)+'</td><td>'+pct(r.r5)+'</td><td>'+pct(r.r10)+'</td><td>'+esc(r.status)+'</td></tr>').join('')+'</tbody></table></div>';
}
async function loadMeasurementRows(){
  const target=document.querySelector('#p-messung-liste');if(!target)return;
  try{const d=await api('/api/pulsar/measurements?limit=200');target.innerHTML=measurementRows(d.rows||[]);}
  catch(e){target.innerHTML='<p class="small">Messungen nicht lesbar: '+esc(e.message)+'</p>';}
}
function sourceChips(s){
  const opt=s.optional_sources||{},names={apewisdom:'ApeWisdom (Reddit)',tradestie:'Tradestie (Reddit)',stocktwits:'StockTwits'};
  const out=Object.entries(opt).map(([name,src])=>'<span class="chip '+(src.state==='ok'?'chip-ok':src.state==='backoff'||src.state==='error'?'chip-block':'chip-unknown')+'" title="'+esc(src.impact||src.detail||'')+'">'+esc(names[name]||name)+' · '+esc(src.state||'unbekannt')+'</span>');
  const finra=s.finra_status||{};out.push('<span class="chip '+(!s.finra?'chip-unknown':finra.state==='ok'?'chip-ok':finra.state==='backoff'?'chip-block':'chip-unknown')+'" title="'+esc(finra.detail||'')+'">FINRA Short Interest · '+(s.finra?esc(finra.state||'unbekannt'):'ausgeschaltet')+(knownNumber(finra.symbols_cached)?' · '+pf(finra.symbols_cached,0)+' Symbole':'')+'</span>');
  out.push('<span class="chip chip-info" title="Relatives Volumen aus FMP-Quote und eToro-Stundenkerzen; kein Abruf">Volumen · Quote + Stundenkerzen</span>');
  return out.join('');
}
async function loadPulsar(){
 try{
  const s=await api('/api/pulsar');pulsarSnapshot=s;
  if(document.activeElement?.id!=='p-mode')document.querySelector('#p-mode').value=s.mode;
  if(document.activeElement?.id!=='p-tradestie')document.querySelector('#p-tradestie').checked=s.tradestie;
  document.querySelector('#p-status').textContent=`${s.mode} · Worker ${s.worker_fresh?'aktiv':'ohne frische Rückmeldung'} · Datenstand ${pt(s.status?.observed_at)} · ${s.status?.detail||'Nach Aktivierung sammelt der Bot die ersten Daten.'}${s.status?.errors?.length?' · Hinweise zum gespeicherten Lauf: '+s.status.errors.join('; '):''}${s.mode!=='AUS'&&s.status?.schedule?.next_scan_at?' · Nächste planmäßige Suche: '+pt(s.status.schedule.next_scan_at)+' Ortszeit':''}${s.rules_version?' · Regeln '+esc(s.rules_version):''}`;
  if(document.activeElement?.id!=='p-web-search')document.querySelector('#p-web-search').checked=s.web_search;
  for(const [id,key] of [['#p-stocktwits','stocktwits'],['#p-finra','finra']]){const box=document.querySelector(id);if(box&&document.activeElement?.id!==id.slice(1))box.checked=!!s[key];}
  const messung=document.querySelector('#p-messung');if(messung)messung.innerHTML=renderMeasurement(s.measurement,s.demo_trades);
  const listPanel=document.querySelector('[data-detail-key="pulsar-messungen-liste"]');if(listPanel?.open)loadMeasurementRows();
  const quellen=document.querySelector('#p-quellen');if(quellen)quellen.innerHTML=sourceChips(s);
  document.querySelector('#p-coverage').textContent=`Abgerufene Toplisten: ${s.coverage?.symbols??'unbekannt'} unterschiedliche Instrumente · ${(s.coverage?.feeds||[]).map(f=>f.feed+' Seite '+f.page+' ('+f.count+')').join(', ')}${s.coverage?.errors?.length?' · Lücken: '+s.coverage.errors.join('; '):''} · Keine vollständige Reddit-Abdeckung; Abwesenheit ist kein Nullbeleg.`;
  const selection=document.querySelector('#p-candidate-selection'),choice=s.candidate_selection||{};
  if(selection)replaceKeepingDetails(selection,choice.observed_at?'<details data-detail-key="candidate-selection"><summary>'+esc(choice.stale?'Älterer Stand der Aktienauswahl: ':'Aktienauswahl: ')+esc((choice.selected_symbols||[]).join(', ')||'noch offen')+'</summary><p class="small">'+esc(choice.detail||'')+' · '+esc(choice.inspected_count??'unbekannt')+' Kandidaten geprüft · '+esc(choice.remaining_slots??'unbekannt')+' Plätze offen</p>'+pulsarPipelineView(choice.source_pipeline)+(choice.excluded||[]).map(item=>'<p class="small"><strong>'+esc(item.symbol)+'</strong> · '+esc(item.status==='ETF_OR_FUND'?'ETF/Fonds ausgeschlossen':item.status==='UNKNOWN'?'Instrumenttyp noch nicht belegt':item.status)+'<br>'+esc(item.reason)+'</p>').join('')+'</details>':'');
  document.querySelector('#p-costs').textContent=`GPT-Kosten einschließlich Reservierungen: heute ${pf(s.usage?.day?.ai,4)} / 1 USD · Woche ${pf(s.usage?.week?.ai,4)} / 2 USD · Monat ${pf(s.usage?.month?.ai,4)} / 10 USD. Quellenabrufe heute: Reddit ${pf(s.usage?.day?.social,0)} / 80 · StockTwits ${pf(s.usage?.day?.stocktwits,0)} / 150 · FINRA ${pf(s.usage?.day?.finra,0)} / 40 · Suchaufrufe ${pf(s.usage?.day?.web_search,0)} / 4.`;
  document.querySelector('#p-capabilities').textContent=(s.capabilities?.detail||'Quellenfähigkeiten noch nicht ermittelt.')+' Datenpflege: '+(s.history?.detail||'Noch nicht gelaufen');
  replaceKeepingDetails(document.querySelector('#p-cards'),(s.cards||[]).map(renderPulsarCard).join('')||'<div class="panel"><p>Noch keine Kandidaten. Wähle „Beobachten und messen“, damit der laufende Bot die öffentlichen Aufmerksamkeitsdaten abruft.</p></div>');
  document.querySelectorAll('[data-chart-symbol]').forEach(b=>b.addEventListener('click',()=>chartPulsar(b.dataset.chartSymbol,Number(b.dataset.days))));
  (s.cards||[]).forEach(c=>chartPulsar(c.symbol,22));
  document.querySelector('#p-proposals').innerHTML=(s.proposals||[]).map(p=>`<p><strong>${esc(p.symbol)}</strong> · ${esc(p.environment)} · ${esc(p.week)} · ${esc(p.status)}${p.reason?' · '+esc(p.reason):''}</p>`).join('')||'<p class="small">Noch keine Nominierung. Fehlende Pflichtbelege sperren die Freigabe.</p>';
  document.querySelector('#p-open').innerHTML=tradeRows(s.open_trades||[]);
  document.querySelector('#p-observation-weeks').innerHTML=(s.universe_guests||[]).map(r=>`<p><strong>${esc(r.symbol)}</strong> · zuletzt entdeckt ${pt(r.last_seen)} · erstmals ${pt(r.first_seen)}<br><span class="small">Gast im normalen eToro-Kandidatenfeld; es gilt ausschließlich die NEXUS-Standard-Prüfung.</span></p>`).join('')||'<p class="small">Keine aktuellen Universumsgäste. Entdeckungen mit belegter Identität erscheinen hier für 14 Tage.</p>';
  const demo=s.demo_trades||{};const bilanz=document.querySelector('#p-demo-bilanz');if(bilanz)bilanz.textContent=demo.closed?`${pf(demo.closed,0)} abgeschlossen · ${pf(demo.wins,0)} Gewinn · ${pf(demo.losses,0)} Verlust${demo.net_unknown?' · '+pf(demo.net_unknown,0)+' Netto offen':''}`:'Noch keine abgeschlossenen PULSAR-Trades.';
  document.querySelector('#p-closed').innerHTML=tradeRows(s.closed_trades||[]);
  renderPulsarArchive();
 }catch(e){document.querySelector('#p-message').textContent='PULSAR konnte nicht geladen werden: '+e.message;document.querySelector('#p-status').textContent='Daten nicht aktuell. Sichtbare Kandidaten stammen vom letzten erfolgreichen Abruf.';}
}
function pulsarPipelineView(p={}){
 if(!Object.keys(p).length)return '';
 return '<p class="small"><strong>Automatische Kandidatenverarbeitung</strong> · '+pt(p.observed_at)+'<br>Reddit ausgewählt '+pf(p.reddit_selected,0)+' · Volumen '+pf(p.volume_selected,0)+' · StockTwits '+pf(p.stocktwits_selected,0)+' · X '+pf(p.x_new_selected,0)+' · gemeinsam recherchiert '+pf(p.researched,0)+'<br>'+esc(p.detail||'Die Recherche bleibt innerhalb der bestehenden PULSAR-Budgets.')+'</p>';
}
let pulsarArchivePage=1;
function renderPulsarArchive(page=pulsarArchivePage){
  const rows=pulsarSnapshot.archive||[],pages=Math.max(1,Math.ceil(rows.length/20));
  pulsarArchivePage=Math.min(pages,Math.max(1,page));
  const target=document.querySelector('#p-archive');
  target.innerHTML=tradeRows(rows.slice((pulsarArchivePage-1)*20,pulsarArchivePage*20))+`<div class="pager"><button class="secondary" id="p-archive-prev" ${pulsarArchivePage<=1?'disabled':''}>Zurück</button><span>${pulsarArchivePage}/${pages} · ${rows.length} Abschlüsse</span><button class="secondary" id="p-archive-next" ${pulsarArchivePage>=pages?'disabled':''}>Weiter</button></div>`;
  target.querySelector('#p-archive-prev').onclick=()=>renderPulsarArchive(pulsarArchivePage-1);
  target.querySelector('#p-archive-next').onclick=()=>renderPulsarArchive(pulsarArchivePage+1);
}
document.querySelector('[data-detail-key="pulsar-messungen-liste"]')?.addEventListener('toggle',e=>{if(e.target.open)loadMeasurementRows();});
nexusPoll(loadPulsar,60000,{when:()=>!document.querySelector('#p-cards details[open]')});
function pulsarXResearchHint(r){if(!r)return '';return '<p class="small"><strong>X-Kandidatenrecherche im Bewertungsstand:</strong> '+esc(({POSITIVE_HINT:'positive Hinweise',NEGATIVE_HINT:'negative Hinweise',MIXED:'gemischte Hinweise',UNCLASSIFIED:'nicht eindeutig',UNKNOWN:'unbekannt'})[r.sentiment]||'unbekannt')+' · '+esc(r.usable_posts??0)+' auswertbare Beiträge · '+pt(r.processed_at)+'<br>'+esc(r.assessment||'')+'</p>'}
