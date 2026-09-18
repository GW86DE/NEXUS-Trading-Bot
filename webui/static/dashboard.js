function fmtDuration(v){if(!knownNumber(v))return 'unbekannt';let s=Math.max(0,Math.round(Number(v)));const d=Math.floor(s/86400);s%=86400;const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60);return d?`${d} T ${h} h ${m} min`:h?`${h} h ${m} min`:`${m} min`}
function pct(used,total){return knownNumber(used)&&knownNumber(total)&&Number(total)>0?`${(Number(used)/Number(total)*100).toFixed(1)} %`:'unbekannt'}
function ortszeit(utc){
  if(!utc) return '';
  const d = new Date(String(utc).endsWith('Z')||String(utc).includes('+') ? utc : utc+'Z');
  if(isNaN(d)) return String(utc).replace('T',' ').slice(0,19);
  return d.toLocaleString('de-DE',{day:'2-digit',month:'2-digit',
    hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function decisionRows(rows){if(!rows?.length)return '<p>Noch keine ernsthaften Kaufkandidaten protokolliert.</p>';return `<table><thead><tr><th>Zeit</th><th>Wert</th><th>Broker</th><th>Status</th><th>Begründung</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(ortszeit(r.created_at_utc))}</td><td>${esc(r.symbol||'?')}</td><td>${contextBadge(r)}</td><td>${badge(r.status||'?',(r.status==='APPROVED'&&String(r.execution_status||'').toUpperCase()==='FILLED')?'ok':'warn')}</td><td>${esc(r.reason||r.blocked_by||'-')}</td></tr>`).join('')}</tbody></table>`}
function brokerAdditionalEvidence(runtime={}, detail={}, operation={}) {
  const blockers=Array.isArray(operation.buy?.blockers)?operation.buy.blockers:[];
  const risk=operation.buy?.risk_review||runtime.risk_review||{};
  const wait=detail.safety_wait||runtime.handelsbereitschaft?.safety_wait||{};
  const quality=Array.isArray(detail.candle_quality)?detail.candle_quality:Array.isArray(runtime.candle_quality)?runtime.candle_quality:[];
  const timeframe=detail.signal_timeframe||runtime.signal_timeframe;
  return `${blockers.length?'<h4>Alle gemeldeten Kaufhindernisse</h4><ul>'+blockers.map(reason=>'<li>'+esc(reason)+'</li>').join('')+'</ul>':''}
    ${risk.status?'<h4>Risikobasis</h4><p>'+esc(risk.detail||risk.status)+'</p><p class="small">Gespeichert: '+esc(risk.stored_basis||'unbekannt')+'<br>Aktuell beobachtet: '+esc(risk.observed_basis||'unbekannt')+'<br>'+esc((risk.reason_codes||[]).join(' · '))+'</p>':''}
    ${timeframe?'<p><strong>Aktives Signal-Zeitraster:</strong> '+esc(timeframe)+'</p>':''}
    ${knownNumber(wait.remaining_seconds)?'<p class="small">Zusätzliche Sicherheitswartezeit: '+esc(fmtDuration(wait.remaining_seconds))+' verbleibend · '+esc(wait.detail||'')+'</p>':''}
    ${sperrenView(runtime.sperren)}${okxAccountActionView(runtime.account_action||{})}${exitCostsView(runtime.etoro_exit_costs||{})}${candleQualityView(quality)}`;
}
// 10.7.0: Jede Kaufsperre mit Grund, Reichweite, Ablauf und Aufloesung -- aus
// derselben Quelle, aus der der Kaufpfad entscheidet. Ohne Liste (aelterer
// Kern) bleibt der Block leer statt etwas zu behaupten.
function sperrenView(s) {
  if(!s||typeof s!=='object')return '';
  const rows=Array.isArray(s.sperren)?s.sperren:[];
  if(s.fehler)return '<h4>Kaufsperren</h4><p class="small">Sperrliste nicht lesbar: '+esc(s.fehler)+'</p>';
  if(!rows.length)return '<h4>Kaufsperren</h4><p class="small">Keine aktive Kaufsperre.</p>';
  const scope={SYMBOL:'nur dieser Wert',DOMAIN:'ganzes Konto'},ablauf={TAGESRESET:'Tageswechsel',BELEG:'Beleg',ZEIT:'Zeitablauf',REPARATUR:'Reparatur'};
  const kopf=s.domaene_gesperrt?'<p class="small">Das Konto ist für neue Käufe gesperrt.</p>':'<p class="small">Das Konto ist frei; gesperrt sind nur: '+esc((s.gesperrte_symbole||[]).join(', ')||'–')+'.</p>';
  return '<h4>Kaufsperren</h4>'+kopf+'<table><thead><tr><th>Grund</th><th>Reichweite</th><th>Endet durch</th><th>Auflösung</th></tr></thead><tbody>'+rows.map(r=>`<tr><td>${esc(r.grund||'?')}${r.symbol?' · '+esc(r.symbol):''}${r.detail?'<br><span class="small">'+esc(r.detail)+'</span>':''}</td><td>${esc(scope[r.reichweite]||r.reichweite||'?')}</td><td>${esc(ablauf[r.ablauf]||r.ablauf||'?')}</td><td>${esc(r.aufloesung||'')}</td></tr>`).join('')+'</tbody></table>';
}
function exitCostsView(report={}) {
  const rows=Array.isArray(report.positions)?report.positions:[];
  if(!rows.length)return '';
  const price=v=>knownNumber(v)?Number(v).toFixed(4):'unbekannt';
  return '<h4>eToro · Kosten vor Gewinnmitnahme</h4><p class="small">Festes Kursziel und erwarteter Nettogewinn sind getrennt. Ein hinterlegter Broker-TP kann unabhängig von NEXUS auslösen. Schutzverkäufe warten nicht auf einen Gewinn.</p>'+rows.map(row=>'<p><strong>'+esc(row.symbol||row.position_id)+'</strong> · '+esc(({ALLOW:'Gewinnmitnahme geprüft',BLOCK_PROFIT:'Gewinnmitnahme wartet',SAFETY_BYPASS:'Sicherheitsausstieg hat Vorrang',UNKNOWN:'Kostenprüfung offen'})[row.decision]||'nicht belegt')+'<br>'+esc(row.reason||'')+'<br><span class="small">Verkaufskurs '+price(row.quote_price)+' · erwartetes Netto '+price(row.net_profit_estimate)+' '+esc(row.currency||'')+'<br>Einstiegskosten '+price(row.entry_fee)+' · erwartete Verkaufskosten '+price(row.exit_fee_estimate)+' · Ausführungspuffer '+price(row.execution_buffer)+'<br>Kursstand '+esc(observationTime(row.quote_at))+' · Kostenstand '+esc(observationTime(row.cost_at))+'</span></p>').join('');
}
function brokerOverview(key,d) {
  const runtime=d[key]||{}, isStock=key==='etoro', detail=isStock?runtime.stock_trading_ready||{}:d.okx_detail||{};
  const online=Boolean(runtime.online)&&Boolean(runtime.worker_alive);
  const context=d.broker_contexts?.[key]||{};
  const ready=online && !context.mode_mismatch && ['DEMO','LIVE'].includes(context.observed_environment) && Boolean(context.account) && !detail.werte_veraltet && Boolean(detail.kaeufe_erlaubt) && d.bot?.zustand==='aktiv';
  const positionRows=isStock?d.stock_positions?.positionen:detail.positionen;
  const count=Array.isArray(positionRows)?positionRows.length:'–';
  const decisions=d.decisions_by_broker?.[key]||{};
  const waiting=online&&isStock&&detail.market_wait_only&&!context.mode_mismatch&&d.bot?.zustand==='aktiv';
  const reasons=isStock?(detail.offen||[]):(detail.offene_bedingungen||[]);
  const operation=d.operations?.brokers?.[key]||{};
  const buy=operation.buy||{state:ready?'ALLOWED':'BLOCKED',detail:waiting?'Wartet auf Börsenöffnung':detail.grund||detail.bereitschaft_grund};
  const channels=[['rest','REST'],['websocket','WebSocket'],['reconciliation','Abgleich'],['protection','Positionsschutz']];
  const rows=channels.map(([name,label])=>`<div class="operation-row"><strong>${label}</strong>${operationBadge(operation[name])}<p class="small">${esc(operation[name]?.detail||'Für diesen Teilstatus fehlt eine separate Messung.')}<br>Stand: ${esc(observationTime(operation[name]?.observed_at))}${operation[name]?.reason_code?'<br>Code: '+esc(operation[name].reason_code):''}</p></div>`).join('');
  return `<article class="panel broker-overview broker-${key}"><div class="section-head"><h2>${esc(brokerName(key))}</h2>${badge(runtime.worker_alive?'Handelskern aktuell':'Handelskern nicht aktuell',runtime.worker_alive?'ok':'warn')}</div>${contextBadge(runtime,key)}<div class="broker-operation-grid"><div><span class="small">Neue Käufe</span>${operationBadge(buy)}<p class="small">${esc(buy.detail||'Der Handelskern prüft jede Order erneut.')}</p></div><div><span class="small">Verkäufe</span>${operationBadge(operation.sell)}<p class="small">${esc(operation.sell?.detail||'Kein eigener Statusbeleg. Maßgeblich sind Zuordnung und Exitstatus jeder Position.')}</p></div></div><div class="broker-numbers"><div><strong>${count}</strong><span>${isStock?'Depotpositionen':'Botpositionen'}</span></div><div><strong>${decisions.candidates??'–'}</strong><span>Kandidaten heute</span></div><div><strong>${decisions.blocked??'–'}</strong><span>Abgelehnt</span></div></div><a class="button secondary" href="/trades?broker=${key}">Handel öffnen</a><details class="broker-diagnostics" data-detail-key="broker-${key}"><summary>Verbindungen, Abgleich & Bedingungen</summary><div class="operation-grid">${rows}</div>${brokerAdditionalEvidence(runtime,detail,operation)}<div class="small"><p>${esc(reasons.join(' · ')||'Keine weiteren Bedingungen übermittelt.')}</p>${isStock?(detail.bedingungen||[]).map(c=>'<p>'+esc(c.beschreibung)+': '+(c.erfuellt?'erfüllt':'offen')+' · '+esc(c.detail||'kein Detail')+'</p>').join(''):''}<p>Heartbeat: ${runtime.heartbeat_age_seconds==null?'unbekannt':Math.round(runtime.heartbeat_age_seconds)+' s'}<br>Letzter Brokerkontakt: ${esc(observationTime(runtime.last_broker_contact))}</p>${isStock?`<p>US-Markt: ${esc(d.stock_market?.phase||'unbekannt')}<br>${esc(d.stock_market?.reason||'')}</p>`:''}<p>${esc(runtime.last_connection_error||runtime.letzter_fehler||'')}</p><a href="/settings">Verbindung in Einstellungen testen</a></div></details></article>`;
}
async function load(){
  try {
    const d=await api('/api/dashboard'), b=d.bot||{}, pi=d.pi||{}, tg=d.telegram||{};
    document.querySelector('#overview-updated').textContent='Letzte Aktualisierung '+new Date().toLocaleTimeString('de-DE');
    document.querySelector('#overview-state').innerHTML=`<strong>${b.zustand==='aktiv'?'Kaufsteuerung aktiv':'Kaufsteuerung pausiert / unklar'}</strong><span>${esc(b.grund||'Schutz und Abgleich werden im zuständigen Handelskern überwacht.')}</span>`;
    replaceKeepingDetails(document.querySelector('#cards'),['etoro','okx'].map(key=>brokerOverview(key,d)).join(''));
    const issues=(d.accounting?.blocking||[]).length, unresolved=(d.etoro_reconciliation?.active||[]).length;
    document.querySelector('#overview-attention').innerHTML=issues||unresolved?`<p class="alert danger">${issues} Buchungsfälle mit Sperrwirkung · ${unresolved} eToro-Aufträge im Abgleich. <a href="/trades#klaerung">Klärung öffnen →</a></p>`:'';
    document.querySelector('#accounting-overview').innerHTML=accountingPanel(d.accounting);
    if(issues)document.querySelector('#accounting-details').open=true;
    if(d.broker_contexts)contextStrip(d.broker_contexts);
    document.querySelector('#decision-storage').textContent='Die letzten fünf Kaufentscheidungen, mit Broker und Begründung.';
    document.querySelector('#recent-decisions').innerHTML=decisionRows((d.recent_decisions||[]).slice(0,5));
    document.querySelector('#system-overview').innerHTML=`<article class="card"><h3>Raspberry Pi</h3><p>${pi.cpu_temperature_c==null?'Temperatur unbekannt':Number(pi.cpu_temperature_c).toFixed(1)+' °C'} · Laufzeit ${fmtDuration(pi.system_uptime_seconds)}</p><p class="small">CPU ${knownNumber(pi.cpu_usage_pct)?Number(pi.cpu_usage_pct).toFixed(1)+' %':'noch nicht gemessen'} · RAM ${pct(pi.memory_used_mb,pi.memory_total_mb)} · Speicher ${pct(pi.disk_used_mb,pi.disk_total_mb)}<br>Frei: ${knownNumber(pi.memory_available_mb)?Math.round(pi.memory_available_mb)+' MB RAM':'RAM unbekannt'} · ${knownNumber(pi.disk_free_mb)?Math.round(pi.disk_free_mb)+' MB Speicher':'Speicher unbekannt'}<br>${esc((pi.warnings||[]).join(' · ')||'Keine Systemwarnung übermittelt')}</p></article><article class="card"><h3>Telegram</h3><p>${esc(tg.delivery_stalled?'Zustellung gestaut':tg.configured?'Eingerichtet':'Nicht eingerichtet')}</p><p class="small">Warteschlange ${tg.queued??'–'} · Steuerung ${esc(tg.control?.status||'unbekannt')}</p><a href="/settings">Einstellungen öffnen</a></article>`;
    document.querySelector('#providers').innerHTML=Object.entries(d.news||{}).map(([n,x])=>`<article class="card"><h3>${esc(n)}</h3><p>${badge(({ok:'Erfolgreich geprüft',partial:'Teilbelege verfügbar',stale:'Älterer Prüfstand',unknown:'Noch nicht geprüft',disabled:'Deaktiviert',backoff:'Anbieterpause',entitlement:'Tarif reicht nicht',authentication:'Zugang prüfen',error:'Prüfung fehlgeschlagen'})[x.state]||'Unbekannt',x.healthy?'ok':x.state==='disabled'?'':'warn')}</p><p class="small">${esc(x.role||'Nachrichten')} · ${x.configured?'Eingerichtet':'Nicht aktiviert'}<br>Geprüft: ${esc(ortszeit(x.last_checked_at)||'noch nie')}<br>Letzter Erfolg: ${esc(ortszeit(x.last_success_at)||'noch keiner')}${x.backoff_seconds ? '<br>Nächster Versuch frühestens in '+Math.ceil(x.backoff_seconds/60)+' min' : ''}</p><details><summary>Prüfergebnis</summary><p class="small">${esc(x.detail||'Noch nicht gemessen')}</p></details>${providerHistoryView(x)}${n.includes('(historisch)')?'<p class="small">Dieser alte Prüfstand beschreibt nicht den aktuellen FMP-Zugang.</p>':'<button class="secondary provider-test" data-provider="'+esc(n)+'">Verbindung testen</button>'}</article>`).join('');
    const calendar = d.earnings_calendar||{};
    document.querySelector('#earnings-calendar').innerHTML=`${badge('Kommende Quartalszahlen: '+(calendar.available?'Kalender geprüft':'Nicht verfügbar'),calendar.available?'ok':'warn')}<p class="small">${esc(calendar.detail||'Kein bestätigter Zukunftskalender vorhanden.')}</p>`;
    document.querySelectorAll('.provider-test').forEach(button=>button.addEventListener('click',()=>testProvider(button.dataset.provider)));
  }catch(e){document.querySelector('#cards').innerHTML=`<div class="alert danger">Status nicht aktuell: ${esc(e.message)}</div>`;document.querySelector('#overview-state').textContent='Aktueller Betriebszustand nicht abrufbar.';document.querySelector('#overview-updated').textContent='Aktualisierung fehlgeschlagen · weitere Angaben sind der letzte geladene Stand';}
}

async function testProvider(name){try{const x=await api('/api/providers/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:name})});alert(`${name}: ${x.ok?'OK':'FEHLER'}\n${x.detail||''}`);load()}catch(e){alert(e.message)}}

async function control(action){const phrase=action==='pause'?'PAUSIEREN':'AKTIVIEREN';if(prompt(`Zur Bestätigung ${phrase} eingeben:`)!==phrase)return;try{await api(`/api/control/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:phrase,reason:'WebUI'})});load()}catch(e){alert(e.message)}}
nexusPoll(load,15000);

function okxAccountActionView(a={}) {
  if(!a.required)return '';
  return '<div class="alert warn"><strong>OKX · Kontobestätigung erforderlich</strong><p>'+esc(a.detail||'Kontohinweis prüfen')+'</p><p class="small">Instrument: '+esc(a.instrument||'unbekannt')+' · Stand: '+esc(observationTime(a.observed_at))+'</p>'+(a.state==='REQUIRED'?'<button class="secondary okx-confirmation-retry" data-revision="'+esc(a.revision)+'">In OKX geprüft – einen regulären Kaufversuch zulassen</button>':'<p>'+esc(a.state==='RETRY_ALLOWED'?'Ein Versuch ist freigegeben; die normalen Kaufprüfungen gelten weiter.':a.state==='RETRY_IN_PROGRESS'?'Ein Versuch wird abgeglichen.':'Kontostand nicht prüfbar.')+'</p>')+'</div>';
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('.okx-confirmation-retry');if(!b)return;
  if(!confirm('Hast du die angeforderte Erklärung im betroffenen OKX-Konto geprüft und dort bestätigt? NEXUS bestätigt nichts bei OKX. Die Freigabe erlaubt einen regulären Kaufversuch nach allen Kaufprüfungen.'))return;
  b.disabled=true;
  try{await api('/api/okx/account-confirmation/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({acknowledged:true,revision:b.dataset.revision})});load()}catch(err){alert(err.message);b.disabled=false}
});
