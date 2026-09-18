(() => {
// Positionen verwalten (neu in v8.1.5).
//
// Georg am 25.08.2026: eine selbst gekaufte Aktie liess sich dem Bot nicht
// uebergeben -- die alte Pruefung verlangte "Stop < Einstand < Ziel" und war
// bei einer Position im Minus unerfuellbar. Jetzt wird gegen den AKTUELLEN
// KURS geprueft, und diese Seite ist die Stelle dafuer.
//
// Die Seite fasst weder Broker noch Positionsbuch an. Sie zeigt die
// Momentaufnahme des Handelskerns und legt Auftraege ab, die der Kern in
// seiner eigenen Schleife am echten Kurs ausfuehrt.

let stand = null;

function zahl(v, stellen = 2, vorzeichen = false) {
  if (v === null || v === undefined || v === '') return '–';
  const n = Number(v);
  if (!isFinite(n)) return '–';
  return n.toLocaleString('de-DE', {
    minimumFractionDigits: stellen, maximumFractionDigits: stellen,
    ...(vorzeichen ? { signDisplay: 'always' } : {})
  });
}

function verwaltungsmarke(modus, row={}) {
  const m = String(modus || '').toUpperCase();
  if (m === 'AUTO') return badge('BOT VERWALTET', 'ok');
  if (m === 'PENDING_TAKEOVER') return badge('ÜBERNAHME LÄUFT', 'warn');
  // v9.3: Ein eigener Kauf im eToro-Propagationsfenster ist KEIN
  // Fremdbestand. Bis 9.2 stand hier "NUR BEOBACHTET" mit der Herkunft
  // BROKER_EXISTING -- fachlich falsch und für den Nutzer irreführend.
  if (m === 'PENDING_CONFIRMATION') return badge(row.eigentum==='VERIFIED'?'KAUF BESTÄTIGT · SCHUTZPRÜFUNG OFFEN':'KAUFZUORDNUNG OFFEN', 'warn');
  return badge('NUR BEOBACHTET', '');
}

// v9.3: Eigentum, Abgleich und Verwaltung getrennt zeigen — dieselbe
// Trennung wie auf der Kryptoseite seit 9.2.
function zuordnung(x) {
  const texte = {
    VERIFIED: '<span class="ok">Eigentum über positionId bewiesen</span>',
    PENDING: '<span class="warn">Eigener Kauf · positionId noch nicht im Depot</span>',
    EXTERNAL: '<span class="small">Fremdbestand beim Broker</span>',
    UNPROVABLE: '<span class="bad">ID-Kette unvollständig</span>',
  };
  const e = texte[String(x.eigentum || '')] || '<span class="small">Eigentum unbekannt</span>';
  const ids = (x.position_ids || []).length
    ? `<br><span class="small">positionId ${esc((x.position_ids || []).join(', '))}</span>` : '';
  return `${e}${ids}`;
}

function kursHinweis(x) {
  const q = String(x.kursquelle || '');
  if (!q || q === 'UNBEKANNT') return '';
  const namen = {
    ETORO_PNL_CLOSE_RATE: 'Depotkurs',
    ETORO_RATES_BID: 'Bid',
    ETORO_RATES_ASK: 'Ask',
    ETORO_RATES_LAST: 'letzte Ausführung',
  };
  const alter = (x.kursalter != null && Number(x.kursalter) >= 60)
    ? ` · ${Math.round(Number(x.kursalter) / 60)} min alt` : '';
  return `<div class="small">${esc(namen[q] || q)}${esc(alter)}</div>`;
}

function ortszeit(utc) {
  if (!utc) return '';
  const d = new Date(String(utc).endsWith('Z') || String(utc).includes('+') ? utc : utc + 'Z');
  if (isNaN(d)) return String(utc).replace('T', ' ').slice(0, 19);
  return d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function exitHinweis(x) {
  const state = String(x.exit_state || 'IDLE').toUpperCase();
  if (state === 'IDLE') return '';
  const names = {RETRY_WAIT:'Verkauf wartet', UNCLEAR:'Verkauf unklar – kein zweiter Auftrag',
    SUBMITTING:'Verkauf wird übermittelt', ACCOUNTING_PENDING:'Verkauf: Buchung offen',
    MANUAL_EXIT_PENDING:'Manueller Verkauf wird geklärt'};
  const next = x.exit_retry_after ? ` · frühestens ${ortszeit(x.exit_retry_after)} (Ortszeit)` : '';
  const count = Number(x.exit_fehlversuche || 0);
  return `<div class="small warn exit-status">${esc(names[state] || state)}${count ? ` · Fehlversuche ${count}` : ''}${esc(next)}</div>`;
}

function zeile(x,std) {
  const record=String(x.record_id||''), key=record.replace(/[^A-Za-z0-9_-]/g,'_');
  const mode=String(x.verwaltung||'').toUpperCase(), known=x.kurs!=null&&Number(x.kurs)>0;
  const canRequest=known&&!['AUTO','PENDING_TAKEOVER','PENDING_CONFIRMATION'].includes(mode)&&record;
  return `<article class="trade-card"><div class="trade-card-head"><h3>${esc(x.symbol)}</h3><div>${contextBadge(x,'etoro')}</div></div><div class="trade-facts"><div><span>Menge</span><strong>${zahl(x.menge,4)}</strong></div><div><span>Kurs</span><strong>${known?zahl(x.kurs,2)+' '+esc(x.waehrung||'?'):'unbekannt'}</strong>${kursHinweis(x)}</div><div><span>Unrealisiert</span><strong>${zahl(x.unrealisiert,2,true)} ${esc(x.waehrung||'?')}</strong></div><div><span>Verwaltung</span>${verwaltungsmarke(x.verwaltung,x)}</div></div>${exitHinweis(x)}<details class="trade-more"><summary>Details & Verwaltung</summary><div class="trade-detail-body">${zuordnung(x)}${protectionEvidenceView(x.protection_evidence||{},x.protection_plan_history||[])}${accountRiskView(x.account_risk_display||{})}${fmpContextView(x.fmp_context||{})}<p>Einstand ${zahl(x.einstand,2)} ${esc(x.waehrung||'?')} · seit ${esc(ortszeit(x.seit))}<br>Stop ${zahl(x.stop,2)} / Ziel ${zahl(x.ziel,2)} ${esc(x.waehrung||'?')}</p><p>${esc(x.notiz||'')}</p>
    ${mode==='AUTO'?`<button class="secondary observe-position" data-record="${esc(record)}">Nur beobachten</button>`:canRequest?`<button class="takeover-form" data-key="${esc(key)}">Dem Bot übergeben</button>`:`<p class="small">${mode.startsWith('PENDING')?'Bestätigung durch den Handelskern läuft.':'Übergabe benötigt einen aktuellen Kurs und eine eindeutige Zuordnung.'}</p>`}
    ${canRequest?`<div id="form-${esc(key)}" class="hidden section"><div class="form-grid"><label for="stop-${esc(key)}">Stop in % unter aktuellem Kurs<input type="number" id="stop-${esc(key)}" min="0.1" max="50" step="0.1" value="${esc(std.stop)}"></label><label for="ziel-${esc(key)}">Ziel in % über aktuellem Kurs<input type="number" id="ziel-${esc(key)}" min="0.1" max="200" step="0.1" value="${esc(std.ziel)}"></label></div><p class="small">Der Handelskern berechnet Stop und Ziel am aktuellen Kurs. Die Übergabe wird erst nach Prüfung des Brokerschutzes bestätigt.</p><button class="takeover-submit" data-record="${esc(record)}" data-key="${esc(key)}">Übergabe anfordern</button></div>`:''}</div></details></article>`;
}
function tabelle(e,std) {
  const rows=e?.positionen||[];
  return rows.length?`<div class="trade-card-grid">${rows.map(row=>zeile(row,std)).join('')}</div>`:'<p class="small">Keine Depotpositionen übermittelt.</p>';
}
function bindPositionActions() {
  document.querySelectorAll('.observe-position').forEach(button=>button.onclick=()=>beobachten(button.dataset.record));
  document.querySelectorAll('.takeover-form').forEach(button=>button.onclick=()=>uebernahmeFormular(button.dataset.key));
  document.querySelectorAll('.takeover-submit').forEach(button=>button.onclick=()=>uebernehmen(button.dataset.record,button.dataset.key));
}

function exposureBadge(klasse) {
  const k = String(klasse || '').toUpperCase();
  const map = {
    CASH: ['CASH', ''],
    BOT_MANAGED: ['BOT VERWALTET', 'ok'],
    ACCOUNT_ASSET: ['KONTO-ASSET', ''],
    EXTERNAL_HOLDING: ['EXTERNER BESTAND', 'warn'],
    RESIDUAL_EXPOSURE: ['UNKLARE RESTPOSITION', 'bad'],
    UNKNOWN_EXPOSURE: ['UNKLARER BESTAND', 'bad'],
  };
  const row = map[k] || [k || 'NOCH NICHT KLASSIFIZIERT', ''];
  return badge(row[0], row[1]);
}

function okxKonto(konto) {
  if (!konto?.vorhanden) {
    return '<p>Der Kryptohandelskern hat noch keinen Kontostand geschrieben.</p>';
  }
  const guthaben = konto.guthaben || {};
  const exposure = konto.exposure || {};
  const nachWaehrung = {};
  for (const row of (exposure.bestaende || [])) {
    const ccy = String(row.waehrung || '').toUpperCase();
    if (!ccy) continue;
    if (!nachWaehrung[ccy]) nachWaehrung[ccy] = [];
    nachWaehrung[ccy].push(row);
  }
  const waehrungen = Object.keys(guthaben).sort();
  const modus = String(konto.modus || '').toUpperCase();
  const erklaerung = modus === 'DEMO'
    ? 'OKX stellt im DEMO-Konto Demo-Startguthaben bereit. Es ist kein Kaufauftrag und wird vom Bot nicht als eigene Position verkauft.'
    : 'Spot-Salden sind verfügbare Konto-Assets. Nur ein exakt belegter Bot-Einstieg erscheint zusätzlich als verwaltete Position.';
  if (!waehrungen.length) return `<p>${esc(erklaerung)} Noch keine Guthaben-Momentaufnahme vorhanden.</p>`;
  return `${contextBadge(konto,'okx')}<p class="small">${esc(erklaerung)}${konto.werte_veraltet?' Letzter bekannter Stand – nicht aktuell.':''}</p><table><thead><tr>
    <th>Währung</th><th>Gesamt</th><th>Frei</th><th>Einordnung</th></tr></thead><tbody>
    ${waehrungen.map(ccy => {
      const x = guthaben[ccy] || {};
      const rows = nachWaehrung[ccy] || [];
      const einordnung = rows.length
        ? rows.map(r => `${exposureBadge(r.klasse)}<div class="small">${esc(r.begruendung || '')}</div>`).join('')
        : '<span class="small">Reconciliation läuft noch</span>';
      return `<tr><td><strong>${esc(ccy)}</strong></td><td>${zahl(x.gesamt, 8)}</td>
        <td>${zahl(x.frei, 8)}</td><td>${einordnung}</td></tr>`;
    }).join('')}</tbody></table>`;
}

function auftragsTabelle(a) {
  const offen = a?.offen || [], fertig = a?.erledigt || [];
  if (!offen.length && !fertig.length) return '<p>Noch nichts beauftragt.</p>';
  const zeilen = [
    ...offen.map(x => `<tr><td>${esc(ortszeit(x.erstellt_am))}</td><td>${esc(x.symbol)}</td>
      <td>${esc(x.aktion)}</td><td>${badge('WARTET AUF DEN KERN', 'warn')}</td>
      <td>Wird beim nächsten Durchlauf ausgeführt.</td></tr>`),
    ...fertig.map(x => `<tr><td>${esc(ortszeit(x.erledigt_am))}</td><td>${esc(x.symbol)}</td>
      <td>${esc(x.aktion)}</td><td>${x.ok ? badge('AUSGEFÜHRT', 'ok') : badge('ABGELEHNT', 'bad')}</td>
      <td>${esc(x.detail || '')}</td></tr>`),
  ];
  return `<table><thead><tr><th>Zeit</th><th>Wert</th><th>Aktion</th><th>Status</th><th>Ergebnis</th></tr></thead>
    <tbody>${zeilen.join('')}</tbody></table>`;
}

function uebernahmeFormular(symbol) {
  document.querySelector(`#form-${CSS.escape(symbol)}`)?.classList.toggle('hidden');
}

function positionFuerRecord(recordId) {
  return (stand?.etoro?.positionen || []).find(x => String(x.record_id || '') === String(recordId));
}

function identityPayload(x) {
  return {
    symbol: x.symbol, record_id: x.record_id,
    position_ids: x.position_ids || [], instrument_id: x.instrument_id || '',
    account_fingerprint: x.account_fingerprint || '', snapshot_id: x.snapshot_id || '',
  };
}

async function uebernehmen(recordId, domKey) {
  const xrow = positionFuerRecord(recordId);
  if (!xrow) return melde('Position ist nicht mehr im aktuellen Snapshot. Bitte neu laden.', false);
  const stop = Number(document.querySelector(`#stop-${CSS.escape(domKey)}`).value);
  const ziel = Number(document.querySelector(`#ziel-${CSS.escape(domKey)}`).value);
  try {
    const x = await api('/api/positions/uebernehmen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...identityPayload(xrow), stop_pct: stop, ziel_pct: ziel }),
    });
    melde(x.detail, true);
    document.querySelectorAll('#etoro-tabelle details').forEach(node=>node.open=false);
    load();
  } catch (e) { melde(e.message, false); }
}

async function beobachten(recordId) {
  const xrow = positionFuerRecord(recordId);
  if (!xrow) return melde('Position ist nicht mehr im aktuellen Snapshot. Bitte neu laden.', false);
  if (!confirm(`eToro · ${rowContext(xrow,'etoro').environment} · ${xrow.symbol} · positionId ${(xrow.position_ids || []).join(', ')} nur noch beobachten? Der Bot verkauft genau diese Position dann nicht mehr selbst.`)) return;
  try {
    const x = await api('/api/positions/beobachten', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(identityPayload(xrow)),
    });
    melde(x.detail, true);
    document.querySelectorAll('#etoro-tabelle details').forEach(node=>node.open=false);
    load();
  } catch (e) { melde(e.message, false); }
}

function melde(text, ok) {
  document.querySelector('#hinweis').innerHTML =
    `<p class="alert ${ok ? 'success' : 'danger'}">${esc(text)}</p>`;
}

async function load() {
  try {
    stand = await api('/api/positions');
    const std = { stop: stand.standard_stop_pct, ziel: stand.standard_ziel_pct };
    const own=x=>['BOT','USER_MANAGED'].includes(String(x.herkunft||'').toUpperCase())
      &&['VERIFIED','USER_AUTHORIZED'].includes(String(x.eigentum||'').toUpperCase())
      &&(x.owned_position_ids||[]).length>0;
    const all=stand.etoro?.positionen||[];
    const openView=document.querySelector('#view-offen');
    if(openView&&!document.querySelector('#own-stock-management')){
      const panel=document.createElement('details');panel.className='panel section';
      panel.innerHTML='<summary>Verwaltung eigener Aktienpositionen</summary><div id="own-stock-management"></div>';
      openView.append(panel);
    }
    const ownTarget=document.querySelector('#own-stock-management');
    if(ownTarget&&!ownTarget.querySelector('details[open]')){
      ownTarget.innerHTML=tabelle({...stand.etoro,positionen:all.filter(own)},std);
    }

    // Do not replace an active form or expanded position during background polling.
    const editing=document.querySelector('#etoro-tabelle details[open]');
    if(!editing){document.querySelector('#etoro-tabelle').innerHTML = tabelle({...stand.etoro,positionen:openView?all.filter(x=>!own(x)):all}, std);bindPositionActions();}
    document.querySelector('#okx-konto').innerHTML = okxKonto(stand.okx_konto);

    document.querySelector('#auftraege').innerHTML = auftragsTabelle(stand.auftraege);
    const e = stand.etoro || {};
    if (editing) {
      document.querySelector('#etoro-stand').textContent='Ansicht angehalten, solange Details geöffnet sind. Vor einer Aktion wird der Bestand erneut geprüft.';
      return;
    }
    document.querySelector('#etoro-stand').textContent = e.updated_at
      ? `Stand ${ortszeit(e.updated_at)}${e.kurse_verfuegbar ? '' : ' · ohne aktuelle Kurse'}`
      : 'Der Handelskern hat noch keine Momentaufnahme geschrieben.';
  } catch (e) {
    document.querySelector('#etoro-tabelle').innerHTML = `<div class="alert danger">${esc(e.message)}</div>`;
  }
}

nexusPoll(load, 20000);

Object.assign(window, {uebernehmen, beobachten, uebernahmeFormular});
})();
