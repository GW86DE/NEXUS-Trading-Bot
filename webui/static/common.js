const csrf = () => document.querySelector('meta[name="csrf"]')?.content || '';

async function api(url, opts = {}) {
  opts.headers = {...(opts.headers || {}), Accept: 'application/json'};
  if ((opts.method || 'GET') !== 'GET') opts.headers['X-CSRF-Token'] = csrf();
  const response = await fetch(url, opts);
  let data;
  const text = await response.text();
  try { data = JSON.parse(text); }
  catch { data = {detail: text || `HTTP ${response.status}`}; }
  if (response.status === 401) {
    location = '/login';
    throw new Error('Anmeldung abgelaufen');
  }
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  })[c]);
}

async function logout() {
  await api('/api/logout', {method: 'POST'});
  location = '/login';
}

function badge(text, kind = '') {
  return `<span class="status ${kind}">${esc(text)}</span>`;
}

// One pending refresh per subscription, no work in hidden browser tabs.
// A slow request never creates a backlog of refreshes on the Raspberry Pi.
function nexusPoll(task, intervalMs, {immediate = true, when = () => true} = {}) {
  let stopped = false, timer = null;
  const interval = Math.max(1000, Number(intervalMs) || 30000);
  async function tick() {
    if (stopped) return;
    try {
      if (!document.hidden && when()) await task();
    } catch (error) {
      // The individual view owns its error display; scheduling must continue.
      console.warn('NEXUS-Aktualisierung fehlgeschlagen', error?.name || 'Fehler');
    } finally {
      if (!stopped) timer = setTimeout(tick, interval);
    }
  }
  if (immediate) tick(); else timer = setTimeout(tick, interval);
  return () => { stopped = true; clearTimeout(timer); };
}

function knownNumber(value) {
  return (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value));
}

function observationTime(value) {
  if (value === null || value === undefined || value === '') return 'nicht belegt';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? 'Zeitangabe ungültig' : date.toLocaleString('de-DE');
}

function operationBadge(item = {}, fallback = 'Unbekannt') {
  const state = String(item.state || 'UNKNOWN').toUpperCase();
  const names = {ACTIVE:'Aktiv',READY:'Freigegeben',ALLOWED:'Freigegeben',BLOCKED:'Gesperrt',
    PAUSED:'Pausiert',OFFLINE:'Offline',ONLINE:'Erreichbar',OK:'Bestätigt',WARN:'Eingeschränkt',
    WARNING:'Warnung',ERROR:'Fehler',STALE:'Stand veraltet',UNKNOWN:fallback,
    RECONCILIATION_REQUIRED:'Abgleich erforderlich',REQUIRED:'Abgleich erforderlich',
    PENDING:'Prüfung läuft',NOT_OBSERVED:'Nicht gemessen',NOT_CONFIGURED:'Nicht eingerichtet',
    NOT_APPLICABLE:'Nicht verwendet',DISABLED:'Deaktiviert',MONITORING:'Positionsprüfung aktiv',
    PER_POSITION:'Je Position prüfen',MATCHED:'Abgleich bestätigt',CLEAR:'Keine offene Sperre'};
  const kind = ['ACTIVE','READY','ALLOWED','ONLINE','OK','MATCHED'].includes(state) ? 'ok'
    : ['ERROR','BLOCKED','OFFLINE'].includes(state) ? 'bad'
    : ['DISABLED','NOT_APPLICABLE','NOT_CONFIGURED'].includes(state) ? '' : 'warn';
  return badge(names[state] || state, kind);
}

function replaceKeepingDetails(target, html) {
  if (!target) return;
  const opened = new Set([...target.querySelectorAll('details[data-detail-key][open]')].map(x => x.dataset.detailKey));
  target.innerHTML = html;
  target.querySelectorAll('details[data-detail-key]').forEach(x => { if (opened.has(x.dataset.detailKey)) x.open = true; });
}

function initResponsiveNavigation() {
  const header = document.querySelector('.topbar');
  const nav = header?.querySelector('nav');
  if (!header || !nav || header.querySelector('.mobile-navigation')) return;
  for (const [path, title] of [['/universe','Universum'],['/underdogs','Underdogs'],['/pulsar','PULSAR'],['/sources','Quellen & X'],['/diagnosis','Diagnose'],['/backtest','Backtest']]) {
    if (!nav.querySelector(`a[href="${path}"]`)) {
      const link = document.createElement('a');
      link.href = path;
      link.textContent = title;
      nav.insertBefore(link, nav.querySelector('a[href="/settings"]'));
    }
  }
  const currentPath = location.pathname.replace(/\/$/, '') || '/';
  for (const link of nav.querySelectorAll('a[href]')) {
    const active = link.getAttribute('href') === currentPath;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  nav.classList.add('desktop-navigation');

  const label = document.createElement('label');
  label.className = 'mobile-navigation';
  label.setAttribute('aria-label', 'Hauptmenü');
  const caption = document.createElement('span');
  caption.textContent = 'Menü';
  const select = document.createElement('select');
  select.setAttribute('aria-label', 'Seite auswählen');

  [...nav.querySelectorAll('a[href]')].forEach(link => {
    const option = document.createElement('option');
    option.value = link.getAttribute('href');
    option.textContent = link.textContent.trim();
    option.selected = link.classList.contains('active');
    select.append(option);
  });
  if (![...select.options].some(option => option.selected)) {
    const current = location.pathname.replace(/\/$/, '') || '/';
    const option = [...select.options].find(item =>
      (item.value.replace(/\/$/, '') || '/') === current);
    if (option) option.selected = true;
  }
  select.addEventListener('change', () => {
    if (select.value && select.value !== location.pathname) location.href = select.value;
  });
  label.append(caption, select);
  nav.insertAdjacentElement('afterend', label);

  // Desktop: seltener genutzte Seiten wandern in ein "Mehr"-Aufklappmenü,
  // damit die Reiterleiste kurz bleibt. Das Mobilmenü behält alle Seiten.
  const moreLinks = ['/universe', '/underdogs', '/sources', '/logbook']
    .map(path => nav.querySelector(`a[href="${path}"]`)).filter(Boolean);
  if (moreLinks.length > 1) {
    const more = document.createElement('details');
    more.className = 'nav-more';
    const summary = document.createElement('summary');
    const panel = document.createElement('div');
    panel.className = 'nav-more-panel';
    for (const link of moreLinks) panel.append(link);
    const activeLink = moreLinks.find(link => link.classList.contains('active'));
    summary.textContent = activeLink ? activeLink.textContent.trim() : 'Mehr';
    if (activeLink) summary.classList.add('active');
    more.append(summary, panel);
    nav.insertBefore(more, nav.querySelector('a[href="/settings"]') || null);
    document.addEventListener('click', event => {
      if (more.open && !more.contains(event.target)) more.open = false;
    });
    more.addEventListener('keydown', event => { if (event.key === 'Escape') more.open = false; });
  }

  const user = header.querySelector(':scope > .small');
  if (user) user.classList.add('topbar-user');
  const logoutButton = header.querySelector(':scope > button');
  if (logoutButton) logoutButton.classList.add('topbar-logout');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initResponsiveNavigation);
} else {
  initResponsiveNavigation();
}


// 9.7.3: every value retains its source. Display context never authorises orders.
let brokerContexts = {};
function brokerName(b) { return ({etoro:'eToro · Aktien',okx:'OKX · Spot',system:'System · gemeinsam'})[String(b||'').toLowerCase()] || 'Broker unbekannt'; }
function rowContext(row={}, forcedBroker='') {
  if(row.display_context) return row.display_context;
  const b=String(row.broker||forcedBroker||'unknown').toLowerCase();
  const observed=['environment','broker_environment','modus','mode'].map(k=>String(row[k]||'').toUpperCase()).filter(v=>['DEMO','PAPER','LIVE'].includes(v)).map(v=>v==='PAPER'?'DEMO':v);
  const hasPaper=row.paper===true||row.paper===false||row.paper===0||row.paper===1;
  if(hasPaper)observed.push(row.paper?'DEMO':'LIVE');
  const unique=[...new Set(observed)];
  const env=unique.length>1?'CONFLICT':(unique[0]||'UNKNOWN');
  const raw=String(row.broker_account_fingerprint||row.account_fingerprint||'');
  const account=/^[A-Za-z0-9_-]{1,80}$/.test(raw)?raw:'';
  return {broker:b,environment:env,account,account_label:account?account.slice(0,8):'Kontobindung fehlt',currency:row.waehrung||row.trade_quote_ccy||'UNKNOWN'};
}
function contextBadge(row={}, forcedBroker='') {
  const c=rowContext(row,forcedBroker);
  const b=['etoro','okx','system'].includes(c.broker)?c.broker:'unknown';
  const env=({DEMO:'DEMO',LIVE:'LIVE',UNKNOWN:'Umgebung unbekannt',CONFLICT:'Umgebung widersprüchlich'})[c.environment]||'Umgebung unbekannt';
  return `<span class="broker-badge broker-${b}">${esc(brokerName(b))} · ${esc(env)}</span><span class="context-account small">Konto ${esc(c.account_label||'unbekannt')}</span>`;
}
function moneyByCurrency(rows,field,forcedBroker='etoro') {
  const groups=new Map(); let missing=0;
  (rows||[]).forEach(row=>{
    const c=rowContext(row,forcedBroker),currency=String(c.currency||'UNKNOWN').toUpperCase();
    const valid=c.account&&['DEMO','LIVE'].includes(c.environment)&&['etoro','okx'].includes(c.broker);
    if(!valid||currency==='UNKNOWN'||row[field]===null||row[field]===undefined||!Number.isFinite(Number(row[field]))) {missing++;return;}
    const key=[c.broker,c.environment,c.account,currency].join(':');
    if(!groups.has(key))groups.set(key,{...c,currency,total:0});
    groups.get(key).total+=Number(row[field]);
  });
  const text=[...groups.values()].map(g=>`${g.total.toLocaleString('de-DE',{minimumFractionDigits:2,maximumFractionDigits:2,signDisplay:'always'})} ${g.currency} (${g.environment}, Konto ${g.account_label})`).join(' · ');
  return (text||'unbekannt')+(missing?` · ${missing} ohne belastbaren Wert oder Kontobindung`:'');
}
function contextStrip(data) {
  brokerContexts=data||{};
  const node=document.getElementById('broker-context-strip'); if(!node)return;
  node.innerHTML=['etoro','okx'].map(b=>{
    const c=data?.[b]||{};
    const env=({DEMO:'DEMO',LIVE:'LIVE',CONFLICT:'WIDERSPRÜCHLICH'})[c.observed_environment]||'unbekannt';
    const cfg=({DEMO:'DEMO',LIVE:'LIVE'})[c.configured_environment]||'unbekannt';
    return `<div class="context-tile broker-${b}"><strong>${esc(brokerName(b))}</strong><span>Worker ${c.worker_alive?'aktiv':'nicht aktuell'} · Laufzeit ${esc(env)}</span><small>Konfiguriert ${esc(cfg)} · Konto ${esc(c.account_label||'unbekannt')}${c.mode_mismatch?' · MODUSWECHSEL NOCH NICHT ÜBERNOMMEN':''}</small></div>`;
  }).join('');
}
async function loadBrokerContexts(){
  try {contextStrip(await api('/api/broker-contexts'));}
  catch(error){const node=document.getElementById('broker-context-strip');if(node)node.textContent='Brokerkontext nicht abrufbar: '+error.message;}
}
function initBrokerContexts(){
  if(!document.querySelector('.topbar nav'))return;
  const main=document.querySelector('main');if(!main)return;
  const box=document.createElement('section');box.id='broker-context-strip';box.className='broker-context-strip';box.setAttribute('aria-label','Broker und Handelsumgebung');
  box.textContent='Brokerkontext wird geladen …';main.prepend(box);nexusPoll(loadBrokerContexts,30000);
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',initBrokerContexts);else initBrokerContexts();

function accountingPanel(report){
  if(!report)return '<p class="warn">eToro-Buchungsabgleich nicht abrufbar.</p>';
  const rows=report.items||[];
  if(!rows.length)return '<p>eToro: Keine gespeicherten Buchungsfälle. Das ist keine Handelsfreigabe; Worker, Kursdaten und Risikogates gelten weiter.</p>';
  const names={RESOLVED:'Ledger-Zuordnung bestätigt',LEGACY_AUDIT:'Historischer Altbestand · nur Audit',ACTIVE:'Abgleich offen',STORAGE_ERROR:'Speicherprüfung fehlgeschlagen'};
  return `<p class="small">${(report.blocking||[]).length} sperrend · ${(report.resolved||[]).length} verknüpft · ${(report.legacy||[]).length} historische Auditfälle. Auflösung verändert keine Menge und kein Ergebnis. Erfasst ist nicht das Handelsdatum.</p><div class="table-wrap"><table><thead><tr><th>Broker / Wert</th><th>Position / Trade</th><th>Zuordnung</th><th>Wirkung</th><th>Beleg / Zeitpunkt</th></tr></thead><tbody>${rows.map(x=>{
    const parts=String(x.domaene||'').split(':');
    return `<tr><td>${contextBadge({broker:'etoro',environment:parts[1]?.toUpperCase(),account_fingerprint:x.status==='LEGACY_AUDIT'?'':(parts[2]||'')})}<strong>${esc(x.symbol||'Speicher')}</strong></td><td>${esc(x.broker_position_id||'–')}<br>Trade ${(x.trade_ids||[]).map(esc).join(', ')||'keine Zuschreibung'}</td><td>${badge(names[x.status]||x.status,x.status==='RESOLVED'?'ok':x.status==='LEGACY_AUDIT'?'':'warn')}<div class="small">${esc(x.result_status==='CONFIRMED'?'Netto bestätigt':x.result_status==='LEGACY_UNBOUND'?'Ergebnis nicht rekonstruiert':'Gebühren/Ergebnis nicht abschließend bestätigt')}</div></td><td>${x.blocks_entries?badge('eToro-Kaufsperre','warn'):badge('keine aktuelle Sperre','')}<div class="small">${x.status==='LEGACY_AUDIT'?'Keinem Konto zugeschrieben':'Nur dieser Kontokontext'}</div></td><td>${esc(x.detail)}<br><span class="small">Abschluss ${esc(x.effective_at_utc||'nicht belegt')}<br>Erfasst ${esc(x.observed_at_utc||'–')}</span></td></tr>`;
  }).join('')}</tbody></table></div>`;
}

// Accessible labels and compact settings search preserve every original control.
function initNexusForms() {
  document.querySelectorAll('.field, .check').forEach((group,index)=>{
    const control=group.querySelector('input,select,textarea'), label=group.querySelector('label');
    if(control&&label&&!label.contains(control)){
      if(!control.id)control.id='field-'+index;
      label.htmlFor=control.id;
    }
  });
  document.querySelectorAll('.table-wrap').forEach(node=>{
    node.tabIndex=0;node.setAttribute('role','region');
    node.setAttribute('aria-label','Tabelle, bei Bedarf seitlich scrollbar');
  });
  const search=document.querySelector('#settings-search');
  if(search)search.addEventListener('input',()=>{
    const text=search.value.trim().toLocaleLowerCase('de'), sections=[...document.querySelectorAll('.settings-section')];
    let count=0;
    sections.forEach(section=>{
      const found=!text||section.textContent.toLocaleLowerCase('de').includes(text);
      section.hidden=!found;if(found)count++;if(found&&text)section.open=true;
    });
    document.querySelector('#settings-search-status').textContent=text?`${count} passende Bereiche`:'';
  });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initNexusForms);else initNexusForms();

function fmpContextView(context={}) {
  const pf=(n,d=2)=>!knownNumber(n)?'unbekannt':Number(n).toLocaleString('de-DE',{maximumFractionDigits:d});
  if(!context||!Object.keys(context).length)return '<p class="small">Noch kein separat aufbereiteter FMP-Kontext gespeichert.</p>';
  const daily=context.daily||{}, annual=context.annual||{}, latest=annual.latest||{}, period=annual.trend_period||{};
  const numeric=(name,value,unit='')=>knownNumber(value)?'<dt>'+esc(name)+'</dt><dd>'+pf(value,3)+(unit?' '+esc(unit):'')+'</dd>':'';
  const metrics=(annual.metrics||[]).slice(0,4).map(m=>'<p class="small">Jährliche Kennzahlen vom '+esc(m.date||'unbekannten Datum')+' · '+esc(m.currency||'Währung unbekannt')+'; keine aktuelle Bewertung</p><dl class="evidence-grid">'+[['ev_to_ebitda','Unternehmenswert / EBITDA'],['net_debt_to_ebitda','Nettoverschuldung / EBITDA'],['interest_coverage','Zinsdeckung'],['quick_ratio','Kurzfristige Liquiditätsdeckung'],['roic','Kapitalrendite (Anteil)'],['cashflow_conversion','Cashflow / Gewinn']].map(([key,label])=>numeric(label,m.values?.[key])).join('')+'</dl>').join('');
  return `<details class="fmp-context"><summary>FMP: Finanzdaten & langfristiger Kurskontext</summary><p class="small">Ergänzende Recherche aus bereits gespeicherten Daten. Diese Ansicht löst keine Abfrage aus und ändert keine Handelsregel.</p>
    ${context.company_name?'<p><strong>'+esc(context.company_name)+'</strong> · '+esc(context.instrument_type||'Typ unbekannt')+'<br>'+esc([context.sector,context.industry].filter(Boolean).join(' · '))+'</p>':''}${context.observed_at?'<p class="small">Referenzstand: '+esc(observationTime(context.observed_at))+'</p>':''}${daily.available?'<p>Tagesdaten bis '+esc(daily.as_of||'unbekannt')+' · '+esc(daily.bars??'unbekannt')+' Kerzen</p><dl class="evidence-grid">'+numeric('20-Tage-Durchschnitt',daily.sma20)+numeric('200-Tage-Durchschnitt',daily.sma200)+numeric('Relatives Handelsvolumen',daily.relative_volume,'×')+numeric('Mittlere Tagesschwankung (ATR)',daily.atr14)+numeric('Rückgang vom 252-Tage-Hoch (Anteil)',daily.drawdown_252)+'</dl>':'<p class="small">Tagesdaten noch nicht ausreichend für den gespeicherten Kontext.</p>'}
    ${annual.available?'<p>Geschäftsjahr '+esc(latest.fiscal_year||latest.end||'unbekannt')+' · '+esc(annual.currency||latest.currency||'Währung unbekannt')+'</p><dl class="evidence-grid">'+numeric('Umsatz',latest.revenue,annual.currency)+numeric('Operativer Cashflow',latest.operating_cashflow,annual.currency)+numeric('Freier Cashflow',latest.free_cashflow,annual.currency)+numeric('Gesamtschulden',latest.total_debt,annual.currency)+numeric('Liquide Mittel',latest.cash,annual.currency)+numeric('Verwässerte Aktienzahl',latest.diluted_shares)+'</dl>':'<p class="small">Jahresdaten nicht ausreichend: '+esc(annual.reason||'kein belastbarer Beleg')+'</p>'}
    <p class="small">Vergleichszeitraum: ${esc(period.from||'unbekannt')} bis ${esc(period.to||'unbekannt')} · ${period.comparable===true?'vergleichbare aufeinanderfolgende Geschäftsjahre':period.comparable===false?'kein belastbarer Vorjahresvergleich':'Vergleichbarkeit unbekannt'}${period.reason?' · '+esc(period.reason):''}</p>
    ${metrics}${(annual.errors||[]).length?'<ul>'+annual.errors.map(error=>'<li>'+esc(typeof error==='string'?error:JSON.stringify(error))+'</li>').join('')+'</ul>':''}<p class="small">Belege: ${(context.source_ids||[]).map(esc).join(', ')||'noch nicht zugeordnet'}</p></details>`;
}

function protectionEvidenceView(proof={}, history=[]) {
  if(!proof||!Object.keys(proof).length)return protectionPlanHistoryView(history);
  const value=x=>knownNumber(x)?Number(x).toLocaleString('de-DE',{maximumFractionDigits:10}):'nicht belegt';
  const rates=x=>x?'Stop '+value(x.stop)+' · Ziel '+value(x.take_profit):'kein Sendebeleg in diesem Ausschnitt';
  return `<details class="protection-evidence"><summary>Schutzprüfung: gewünschte und bestätigte Werte</summary><p>${proof.runtime_fresh===false?'Gespeicherter Stand ist veraltet. ':''}${proof.confirmed===true?'Preisabgleich im gespeicherten Snapshot bestätigt.':'Preisabgleich noch nicht bestätigt.'}</p>
  <dl class="evidence-grid"><dt>Gewünscht</dt><dd>${esc(rates(proof.requested))}</dd><dt>Gesendet</dt><dd>${esc(rates(proof.sent))}${proof.sent?.state?' · '+esc(proof.sent.state):''}</dd><dt>Brokerbeobachtung</dt><dd>${(proof.observed||[]).map(row=>'Position '+esc(row.position_id||'unbekannt')+' · '+esc(rates(row))).join('<br>')||'nicht belegt'}</dd><dt>Preispräzision</dt><dd>${proof.precision?.status==='PROVEN'?'Brokerregel belegt':'Brokerregel nicht belegt'}${proof.precision?.source?' · '+esc(proof.precision.source):''}</dd></dl>
  <p class="small">${esc(proof.reason_code||'Kein Fehlercode gespeichert')} · Stand ${esc(observationTime(proof.observed_at))}<br>Eine sichtbare kleine Preisabweichung allein bestätigt keine gültige Broker-Rundungsregel.</p>${protectionPlanHistoryView(history)}</details>`;
}

function protectionPlanHistoryView(history=[]) {
  if(!Array.isArray(history)||!history.length)return '';
  const value=x=>knownNumber(x)?Number(x).toLocaleString('de-DE',{maximumFractionDigits:10}):'nicht belegt';
  const rates=plan=>'Stop '+value(plan?.stop)+' · Ziel '+value(plan?.take_profit);
  const latest=history[history.length-1];
  return `<p><strong>Zuletzt festgelegter wirksamer Schutzplan: ${esc(rates(latest.effective_plan))}</strong></p><p class="small">Die Planhistorie belegt die frühere Entscheidung; der aktuelle Schutz wird separat beim Broker geprüft.</p><details class="protection-plan-history"><summary>Ursprünglicher Plan und ${history.length} dokumentierte Planänderungen</summary>${history.map(receipt=>`<article><p><strong>${esc(observationTime(receipt.decided_at))}</strong> · ${esc(receipt.method==='ADOPT_EXISTING_BROKER_PROTECTION'?'Vorhandenen Broker-Schutz übernommen':receipt.method||'Methode unbekannt')}</p><dl class="evidence-grid"><dt>Vorheriger Plan</dt><dd>${esc(rates(receipt.original_plan))}</dd><dt>Neu festgelegt</dt><dd>${esc(rates(receipt.effective_plan))}</dd><dt>Positionen</dt><dd>${esc((receipt.position_ids||[]).join(', '))} · ${esc(receipt.quantity??'Menge unbekannt')}</dd></dl><p class="small">${esc(receipt.reason||'Grund nicht belegt')}<br>Entscheidungsbeleg ${esc(receipt.decision_id||'nicht belegt')}</p></article>`).join('')}</details>`;
}

function accountRiskView(risk={}) {
  const label={BLOCKED:'Neue Käufe durch Kontorisikoprüfung gesperrt',REPORTED:'Kontorisikoprüfung gespeichert',STALE:'Kontorisikoprüfung veraltet',UNKNOWN:'Kontorisikoprüfung nicht belegt'};
  return `<div class="account-risk"><h4>Risikobasis des Kontos</h4>${badge(label[risk.state]||label.UNKNOWN,risk.state==='BLOCKED'||risk.state==='STALE'?'warn':'')}<p class="small">${esc(risk.detail||'Keine separate Prüfung gespeichert.')}<br>Eigentumszuordnung und Positionsschutz werden separat geprüft. Eine Kaufsperre allein sperrt keine Positionsverwaltung.</p></div>`;
}

function candleQualityView(quality) {
  const rows=Array.isArray(quality)?quality:[];
  if(!rows.length)return '';
  const freshness={CURRENT:'Zeitlich aktuell',STALE:'Veraltet',MISSING:'Fehlend',FUTURE_OR_OPEN:'Noch offen oder Zeitangabe in der Zukunft',INVALID:'Ungültige Kerzendaten'};
  return '<details class="candle-quality-list"><summary>Kerzenqualität je Instrument ('+rows.length+')</summary><p class="small">Verbindung, Datenfrische und Handelsaktivität werden getrennt bewertet. Nullvolumen ist keine bestätigte Verbindungsstörung; die bestehende Volumenregel für neue Käufe bleibt wirksam.</p>'+rows.slice(0,60).map(q=>{
    const activity=q.recent_active_rows===0?'Kein Volumen in den letzten '+q.recent_rows+' Kerzen':knownNumber(q.recent_active_rows)?q.recent_active_rows+' der letzten '+q.recent_rows+' Kerzen mit Volumen':'Aktivität nicht belegt';
    return '<article><h4>'+esc(q.instrument||'unbekannt')+' · '+esc(q.environment||'Umgebung unbekannt')+' · '+esc(q.timeframe||'Zeitraster unbekannt')+'</h4><dl class="evidence-grid"><dt>Datenlieferung</dt><dd>'+esc(q.source_receipt?.receipt_id?'Brokerantwort mit Rohbeleg gespeichert':'Separater Rohbeleg nicht vorhanden')+'</dd><dt>Datenfrische</dt><dd>'+esc(freshness[q.freshness]||'Aktualität unbekannt')+' · letzte Kerze geschlossen '+esc(q.latest_close_utc||q.latest_closed_at||'unbekannt')+'</dd><dt>Handelsaktivität</dt><dd>'+esc(activity)+(q.flat_close===true?' · Schlusskurse unverändert':'')+'</dd></dl><p class="small">'+esc(q.zero_volume_rows??'unbekannt')+'/'+esc(q.rows??'unbekannt')+' Kerzen ohne Volumen'+(knownNumber(q.source_receipt?.raw_zero_volume_rows)?' · Bereits in der Brokerantwort: '+q.source_receipt.raw_zero_volume_rows+'/'+q.source_receipt.hashed_rows+' Zeilen ohne Volumen':'')+' · '+esc(q.synthetic_gap_rows??'unbekannt')+' ergänzte Lücken'+(String(q.environment).toUpperCase()==='DEMO'&&q.recent_active_rows===0?'<br>DEMO meldet für diese Kerzen keine Handelsaktivität. Daraus folgt keine Aussage über das LIVE-Volumen.':'')+'<br>Die Verwaltung bestehender Positionen richtet sich weiter nach deren eigenen Schutz- und Exitprüfungen.<br>'+esc((q.reasons||[]).join(' · '))+'</p></article>';
  }).join('')+(rows.length>60?'<p>Anzeige auf 60 Instrumente begrenzt.</p>':'')+'</details>';
}

function providerHistoryView(source={}) {
  const historical=source.error_scope==='HISTORICAL',current=source.error_scope==='CURRENT';
  const age=knownNumber(source.error_age_seconds)?' · vor '+Math.floor(Number(source.error_age_seconds)/3600)+' Stunden':'';
  return `<p class="small">Aktuelle Wirkung: ${esc(source.impact||'Nicht separat belegt')}${source.coverage?.complete===false?'<br>Teilbelege vorhanden; fehlende Einträge beweisen keine Abwesenheit.':''}</p>${historical||current?'<details class="provider-error-history"><summary>'+esc(historical?'Historischer Quellenfehler':'Fehler im aktuellen Prüfstand')+esc(age)+'</summary><p class="small">'+esc(source.last_error_detail||source.detail||'Fehlerdetail nicht gespeichert')+'<br>Fehlerzeit: '+esc(observationTime(source.error_at))+(historical?'<br>Dieser ältere Fehler ist kein Beleg einer aktuellen Handelsblockade.':'')+'</p></details>':''}`;
}
