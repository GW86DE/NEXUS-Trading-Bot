let current = 1, pages = 1;
const brokerFilter = document.getElementById('broker');
const assetFilter = document.getElementById('asset');
const statusFilter = document.getElementById('status');
const sourceFilter = document.getElementById('source');
const symbolFilter = document.getElementById('symbol');
const dayFilter = document.getElementById('day');
const countNode = document.getElementById('count');
const decisionRowsNode = document.getElementById('decisionRows');
const pageNode = document.getElementById('page');
const prevButton = document.getElementById('prev');
const nextButton = document.getElementById('next');
const sysSearchNode = document.getElementById('sysSearch');
const sysLevelNode = document.getElementById('sysLevel');
const systemRowsNode = document.getElementById('systemRows');

// 9.5.6: Coin- und Aktiennamen im Logbuch hervorheben.
//
// Die Liste kommt vom Server (Universum, offene Positionen, Handelsbuch,
// feste Kernlisten). Bewusst KEIN Muster fuer Grossbuchstaben -- damit
// wuerden INFO, WARNING, OKX, USDC, HANDEL und KRYPTO mitleuchten.
let bekannteSymbole = [];
let symbolMuster = null;

function symbolMusterBauen(symbole) {
  bekannteSymbole = Array.isArray(symbole) ? symbole.filter(Boolean) : [];
  if (!bekannteSymbole.length) { symbolMuster = null; return; }
  // Laengste zuerst, damit z.B. BTCUSD vor BTC greift.
  const teile = [...bekannteSymbole]
    .sort((a, b) => b.length - a.length)
    .map(s => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  symbolMuster = new RegExp(`(?<![A-Z0-9])(${teile.join('|')})(?![A-Z0-9])`, 'g');
}

function symboleHervorheben(sicherer_text) {
  // Erwartet BEREITS escapten Text. Das Muster ist gross- und
  // kleinschreibungsempfindlich und trifft deshalb nie in &amp; oder &quot;
  // hinein; die Ersetzung laeuft einmal von links nach rechts und liest
  // eingefuegtes Markup nicht erneut.
  if (!symbolMuster) return sicherer_text;
  return sicherer_text.replace(symbolMuster, '<span class="sym">$1</span>');
}

function qs() {
  return new URLSearchParams({
    page: current, per_page: document.getElementById('perPage')?.value||15, broker: brokerFilter.value,
    asset_type: assetFilter.value, status: statusFilter.value,
    source: sourceFilter.value, symbol: symbolFilter.value, day: dayFilter.value,
    search: document.querySelector('#filterSearch')?.value||'',
    strategy: document.querySelector('#filterStrategy')?.value||'',
    reason: document.querySelector('#filterReason')?.value||'',
  });
}

function reason(x) {
  const checks = Object.entries(x.signal_checks || {})
    .filter(([, value]) => value && value.passed === false)
    .map(([name]) => name.replaceAll('_', ' '));
  const metrics = Object.entries(x.metrics || {})
    .map(([key, value]) => `${key}=${typeof value === 'number' ? Number(value).toPrecision(5) : value}`)
    .join(' · ');
  const explanation=x.decision_explanation||{};
  const kind=['neutral','approved','warning'].includes(explanation.kind)?explanation.kind:'neutral';
  const text=explanation.text||'Für diesen Eintrag fehlt noch die verständliche Zusammenfassung; die gespeicherte Begründung steht in den Details.';
  return `<p class="decision-explanation ${kind}"><strong>${esc(text)}</strong></p>`
    + `<details class="decision-technical"><summary>Ausführliche Begründung & Messwerte</summary><p>${esc(x.reason || x.signal_reason || x.blocked_by || 'Keine zusätzliche Begründung gespeichert.')}</p>`
    + `${checks.length ? `<p class="small">Nicht erfüllt: ${esc(checks.join(', '))}</p>` : ''}`
    + `${metrics ? `<p class="small">${esc(metrics)}</p>` : ''}</details>`;
}

function sources(x) {
  const rows = Array.isArray(x) ? x : [];
  return rows.slice(0, 5)
    .map(v => typeof v === 'string' ? v : (v.quelle || v.source || v.datenquelle || ''))
    .filter(Boolean).join(', ');
}

function strategy(x) {
  const mode = x.strategy_mode || '–', version = x.strategy_version || '';
  return `<strong>${esc(mode)}</strong>${version ? `<br><span class="small">${esc(version)}${x.strategy_parameter_hash ? ` · ${esc(String(x.strategy_parameter_hash).slice(0, 12))}` : ''}</span>` : ''}`;
}

// v9.3: Entscheidung und Ausführung sind zwei Zustände. Eine freigegebene,
// aber nicht ausgeführte Order stand bis 9.2 grün als "APPROVED" da — das
// sieht aus wie ein erfolgreicher Kauf. Beim WLD-Beispiel wurde die
// FOK-Order mit 0/270,94 storniert.
const AUSFUEHRUNGSTEXT = {
  FILLED: 'Vollständig ausgeführt',
  PARTIAL: 'Teilweise ausgeführt',
  CANCELED_NO_FILL: 'Kauf erlaubt – beim Broker nicht ausgeführt',
  SUBMITTED_NOT_FILLED: 'Kauf erlaubt – nicht ausgeführt',
  REJECTED: 'Vom Broker abgelehnt',
  EXPIRED: 'Beim Broker verfallen',
  UNKNOWN: '⚠️ Ausgang unklar',
};

// Grün ausschließlich für einen tatsächlich ausgeführten Kauf.
function entscheidungsmarke(x) {
  const s = String(x.status || '');
  const e = String(x.execution_status || '').toUpperCase();
  if (s === 'APPROVED') {
    if (e === 'FILLED') return badge('APPROVED', 'ok');
    if (!e || e === 'READY_TO_SUBMIT' || e === 'SUBMITTING') return badge('APPROVED', 'warn');
    return badge(e==='PARTIAL'||e==='PARTIALLY_FILLED'?'APPROVED · teilweise ausgeführt':'APPROVED · Ausführung prüfen', 'warn');
  }
  if (s === 'NO_SIGNAL') return badge(s, '');
  if (s.includes('BLOCK') || s === 'SYSTEM_ERROR') return badge(s, 'bad');
  return badge(s, 'warn');
}

function ausfuehrungsmarke(status) {
  const s = String(status || '').toUpperCase();
  if (!s) return '';
  const klasse = s === 'FILLED' ? 'ok'
    : s === 'PARTIAL' ? 'warn'
    : s === 'UNKNOWN' ? 'bad' : '';
  return badge(AUSFUEHRUNGSTEXT[s] || s, klasse);
}

function execution(x) {
  const events = (x.execution_events || []).slice(0, 3).map(e => e.event_type).join(' → ');
  const orders = (x.orders || []).map(o => `${o.role}:${o.status} ${o.filled_qty ?? '–'}/${o.requested_qty ?? '–'}`).join(' · ');
  return `${ausfuehrungsmarke(x.execution_status)}${events ? `<br><span class="small">${esc(events)}</span>` : ''}${orders ? `<br><span class="small">${esc(orders)}</span>` : ''}<br><span class="small">${esc(({DEMO:'DEMO',LIVE:'LIVE',CONFLICT:'Umgebung widersprüchlich'})[rowContext(x).environment]||'Umgebung unbekannt')} · Update ${esc(x.updated_at_local || '–')}</span>`;
}

function scanZeile(z) {
  const gates=(z.blockiert_nach_gate||[]).map(g=>`${g.anzahl} ${esc(g.gate)} (${esc((g.symbole||[]).join(', '))})`).join('; ');
  const teile=[`<strong>${z.geprueft}</strong> geprüft`];
  if (z.kauf_freigegeben) teile.push(`<span class="ok">${z.kauf_freigegeben} Kauf freigegeben (${esc((z.freigegeben_symbole||[]).join(', '))})</span>`);
  if (z.kaufwunsch_blockiert) teile.push(`${z.kaufwunsch_blockiert} Kaufwunsch blockiert: ${gates}`);
  if (z.sell_ohne_position) teile.push(`${z.sell_ohne_position} überkauft/Verkaufssignal ohne Position`);
  if (z.kein_signal) teile.push(`${z.kein_signal} ohne Signal`);
  if (z.fehler) teile.push(`<span class="warn">${z.fehler} Fehler (${esc((z.fehler_symbole||[]).join(', '))})</span>`);
  return `<p class="small scan-row"><span class="scan-time">${esc(observationTime(z.beendet_utc))}</span> · ${teile.join(' — ')}</p>`;
}

async function loadScanUebersicht() {
  const node=document.getElementById('scan-uebersicht');
  if (!node) return;
  try {
    const d=await api('/api/logs/scan-uebersicht?limit=6');
    const broker=Object.entries(d.broker||{});
    if (!broker.length) { node.innerHTML='<p class="small">Noch keine Scan-Übersicht gespeichert (entsteht mit dem ersten vollständigen Scannerzyklus nach dem Update).</p>'; return; }
    node.innerHTML=broker.map(([name,rows])=>`<details data-detail-key="scan-${esc(name)}" open><summary>${esc(brokerName(name))} · letzter Scan ${esc(observationTime(rows[0]?.beendet_utc))}: ${esc(String(rows[0]?.text||'').replace(/^[^:]+:\s*/,''))}</summary>${rows.map(scanZeile).join('')}</details>`).join('')
      + `<p class="small">${esc(d.detail||'')}</p>`;
  } catch (e) {
    node.innerHTML=`<p class="small">Scan-Übersicht nicht lesbar: ${esc(e.message)}</p>`;
  }
}

async function loadDecisions(page = 1) {
  current = page;
  loadScanUebersicht();
  try {
    const d = await api('/api/logs/decisions?' + qs());
    pages = d.pages || 1;
    const groups=groupStartupDecisions(d.rows||[]);
    countNode.textContent = `${d.total} gespeicherte Entscheidungen · ${groups.length} Anzeigen auf dieser Seite. Gleiche Anlaufsperren sind aufklappbar; keine Entscheidung wird entfernt.`;
    decisionRowsNode.innerHTML = groups.map(group => group.rows.length===1?decisionRow(group.rows[0]):startupGroupRow(group)).join('') || '<tr><td colspan="9">Keine Treffer</td></tr>';
    pageNode.textContent = `Seite ${current} von ${pages}`;
    prevButton.disabled = current <= 1;
    nextButton.disabled = current >= pages;
  } catch (e) {
    decisionRowsNode.innerHTML = `<tr><td colspan="9">${esc(e.message)}</td></tr>`;
  }
}

function groupStartupDecisions(rows) {
  const groups=[],byKey=new Map();
  for(const row of rows){
    const key=row.startup_repetition?.key;
    if(!key){groups.push({rows:[row]});continue;}
    if(!byKey.has(key)){const group={key,rows:[]};byKey.set(key,group);groups.push(group);}
    byKey.get(key).rows.push(row);
  }
  return groups;
}

function decisionRow(x) {
  return `<tr>
      <td>${esc(x.created_at_local || '')}</td>
      <td>${contextBadge(x)}</td>
      <td><span class="sym">${esc(x.symbol)}</span><br><span class="small">${esc(x.asset_type)}</span></td>
      <td>${strategy(x)}</td>
      <td>${entscheidungsmarke(x)}</td>
      <td>${reason(x)}</td>
      <td>${esc(x.hauptquelle || '')}</td>
      <td class="source-list">${esc(sources(x.sources))}</td>
      <td>${execution(x)}</td></tr>`;
}

function startupGroupRow(group) {
  const row=group.rows[0],oldest=group.rows[group.rows.length-1];
  return `<tr class="startup-repetition"><td>${esc(oldest.created_at_local||'')}<br>bis ${esc(row.created_at_local||'')}</td><td>${contextBadge(row)}</td><td><span class="sym">${esc(row.symbol)}</span><br>${esc(row.startup_repetition.timeframe)}</td><td>${strategy(row)}</td><td>${entscheidungsmarke(row)}<br>${group.rows.length} Wiederholungen</td><td colspan="4">${reason(row)}<details><summary>${group.rows.length} vollständige Einzelbelege dieser Seite anzeigen</summary><p class="small">Gleicher gespeicherter Broker, Symbol, Zeitraster, Strategiestand und Sperrgrund. ${row.startup_repetition.account_bound?'Gleiches belegtes Konto.':'Kontozuordnung in diesen Altbelegen nicht belegt.'} ${row.startup_repetition.instrument_proven?'Gleiche belegte Instrument-ID.':'Instrument-ID in diesen Altbelegen nicht belegt.'} Die Einzelentscheidungen bleiben gespeichert.</p>${group.rows.map(x=>`<article><h4>Entscheidung ${esc(x.id)} · ${esc(x.created_at_local)}</h4>${reason(x)}<p class="small">Hauptquelle: ${esc(x.hauptquelle||'nicht belegt')} · Quellen: ${esc(sources(x.sources)||'nicht belegt')}</p>${execution(x)}</article>`).join('')}</details></td></tr>`;
}

async function loadSystem() {
  try {
    const p = new URLSearchParams({search: sysSearchNode.value, level: sysLevelNode.value, broker:document.getElementById('sysBroker').value,limit: 300});
    const d = await api('/api/logs/scoped?' + p);
    symbolMusterBauen(d.symbole);
    systemRowsNode.innerHTML = (d.entries||[]).map(row=>`<span class="log-scope ${['etoro','okx'].includes(row.broker)?row.broker:'system'}">[${esc(brokerName(row.broker))}]</span> ${symboleHervorheben(esc(row.text))}`).join('\n');
  } catch (e) {
    systemRowsNode.textContent = e.message;
  }
}

prevButton.onclick = () => loadDecisions(Math.max(1, current - 1));
nextButton.onclick = () => loadDecisions(Math.min(pages, current + 1));
loadDecisions();
loadSystem();
