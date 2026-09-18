let tradeDaten = {};
let chartDaten = null;
let ausgewaehlt = null;
let manualDaten = {commands:[],locks:[]};

const zahl = (v, stellen = 2) => v === null || v === undefined || v === ''
  ? 'unbekannt'
  : Number(v).toLocaleString('de-DE', {minimumFractionDigits: stellen, maximumFractionDigits: stellen});
const zeit = v => {
  if (!v) return '–';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString('de-DE', {dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Berlin'});
};
const modus = r => r.entry_strategy_mode || r.strategie_version || 'UNBEKANNT';

function metricKarte(titel, wert, zusatz = '', klasse = '') {
  return `<article class="card"><h3>${esc(titel)}</h3><div class="metric ${klasse}">${esc(wert)}</div><div class="small">${esc(zusatz)}</div></article>`;
}

function zeichneKennzahlen() {
  const k=tradeDaten.kennzahlen||{};
  const groups=tradeDaten.context_groups||[];
  document.querySelector('#trade-kennzahlen').innerHTML=[
    ...groups.map(g=>`<article class="domain-results ${g.broker==='okx'?'okx':'etoro'}">${contextBadge({display_context:g})}<h3>${esc(g.currency==='UNKNOWN'?'Währung unbekannt':g.currency)} · ${esc(g.bound?'abgegrenztes Konto':'Altbestand ohne Kontobindung')}</h3><div class="domain-figures"><div><strong>${g.summe_netto==null?'unbekannt':esc(zahl(g.summe_netto)+' '+g.currency)}</strong><span>Bestätigtes Netto dieser Gruppe</span></div><div><strong>${g.gebuehren==null?'unbekannt':esc(zahl(g.gebuehren)+' '+g.currency)}</strong><span>Bestätigte Gebühren</span></div><div><strong>${g.offen} offen · ${g.geschlossen} geschlossen</strong><span>${g.ohne_bestaetigtes_netto} ohne bestätigtes Netto</span></div></div></article>`)
  ].join('');
}

function waehrungHinweis() {
  const b = document.querySelector('#trade-broker')?.value || '';
  return b === 'etoro' ? 'Kontowährung' : b === 'okx' ? 'Quote' : 'je Trade';
}

function pnlText(v, w = '') {
  if (v === null || v === undefined) return '<span class="small">unbekannt</span>';
  const klasse = Number(v) > 0 ? 'trade-pos' : Number(v) < 0 ? 'trade-neg' : '';
  return `<span class="${klasse}">${esc(zahl(v))} ${esc(w || '')}</span>`;
}

function pnlMitProzent(v, pct, w = '') {
  const basis = pnlText(v, w);
  const klasse = Number(pct) > 0 ? 'trade-pos' : Number(pct) < 0 ? 'trade-neg' : '';
  return `${basis}${pct === null || pct === undefined ? '' : `<br><span class="small ${klasse}">${esc(zahl(pct, 2))} %</span>`}`;
}


function geschlossenesErgebnis(r) {
  const status = String(r.ergebnis_status || 'PROVISIONAL');
  if (status === 'CONFIRMED') {
    const value = pnlMitProzent(r.netto_pnl, r.netto_pnl_pct, r.waehrung);
    const fx = r.eur_valuation;
    if (!fx) return value;
    const basis = (fx.reference_qualities || []).includes('ECB_DAILY_REFERENCE') ? 'EZB-Tagesreferenz' : 'historische Referenzkurse';
    return `${value}<br><span class="small">${fx.cross_currency ? 'In EUR bewertet' : `EUR-Bewertung: ${esc(zahl(fx.net_eur, 4))} EUR`} · ${esc(basis)}</span>`;
  }
  if (status !== 'FEES_UNKNOWN' && r.netto_pnl !== null && r.netto_pnl !== undefined)
    return `${pnlText(r.netto_pnl, r.waehrung)}<br><span class="warn small">vorläufig · nicht in bestätigter Summe</span>`;
  const brutto = r.brutto_pnl == null ? '' : `<br><span class="small">Brutto: ${esc(zahl(r.brutto_pnl))} ${esc(r.waehrung || '')}</span>`;
  const entryCost = r.broker==='etoro' && r.entry_fee_quality==='CONFIRMED' && r.einstieg_gebuehr!=null
    ? `<br><span class="small">Einstiegskosten bestätigt: ${esc(zahl(r.einstieg_gebuehr))} ${esc(r.waehrung||'')}</span>` : '';
  return `<span class="warn small">${status === 'FEES_UNKNOWN' ? 'Gebühren offen · Netto unbekannt' : 'Ergebnis unbekannt'}</span>${brutto}${entryCost}`;
}

function brokerErgebnisBeleg(r) {
  if(r.broker!=='etoro'||!r.ausgestiegen_am||!r.broker_result_quality)return '';
  const a=r.broker_result_assessment||{};
  return `<div class="broker-result-evidence"><p><strong>Vom Broker gemeldetes Historienergebnis:</strong> ${pnlText(r.broker_reported_net_pnl,r.broker_result_currency)}</p><p class="small">Historienfeld „fees“: ${esc(zahl(r.broker_reported_fees))} ${esc(r.broker_result_currency||'Währung unbekannt')} · Belegte Einstiegskosten: ${esc(zahl(a.confirmed_entry_costs))} ${esc(r.waehrung||'')}</p><p class="small">${esc(a.reason||'Der Umfang der enthaltenen Gebühren ist noch zu prüfen.')} Das Historienergebnis wird separat vom vollständig abgeglichenen NEXUS-Netto geführt.</p><p class="small">Beleg ${esc(r.broker_result_receipt_hash||'nicht vorhanden')}</p></div>`;
}

function statusDeutsch(v) {
  return ({PENDING_CONFIRMATION:'Abgleich läuft',RESIDUAL_EXPOSURE:'Restbestand ohne Positionsbuch',
    UNPROVABLE:'Brokerbeweis fehlt',ACCOUNT_MISMATCH:'Anderes Brokerkonto',
    EXTERNAL_OBSERVE:'Externer Bestand · nur beobachten',CONFIRMED_OPEN:'Bestätigte Botposition',STRATEGY_MIGRATION_REQUIRED:'Bestätigte Botposition · Strategie-Migration erforderlich',ACCOUNT_MISMATCH:'Anderes Brokerkonto · keine automatische Übernahme'})[String(v||'').toUpperCase()] || (v || 'unbekannt');
}

function hatVerifiziertenBotnachweis(v) {
  return ['VERIFIED','VERIFIED_BROKER_FILL_CHAIN'].includes(String(v||'').toUpperCase());
}

// v9.1: management_note, exit_state, exit_retry_after und protection_detail
// wurden berechnet, ins JSON geschrieben -- und nie angezeigt. Der in 9.0.15
// eingefuehrte Retry-Cooldown und die 24-Stunden-Sperre nach einem unklaren
// Ausstieg waren damit in der Oberflaeche unsichtbar, obwohl der Changelog das
// Gegenteil behauptet. Eine pausierte Position stand dort als "Offen · Schutz
// aktiv".
function exitZustandText(r) {
  const s = String(r.exit_state || 'IDLE').toUpperCase();
  if (s === 'IDLE' || s === '') return '';
  const bis = r.exit_retry_after ? ` · nächster Versuch ${zeit(r.exit_retry_after)}` : '';
  const texte = {
    SUBMITTING: 'Verkauf läuft',
    RETRY_WAIT: 'Verkauf fehlgeschlagen, Wartezeit',
    UNCLEAR: '⚠️ Verkaufszustand UNKLAR — kein zweiter Versuch',
    MANUAL: 'manuell verwaltet',
    MANUAL_EXIT_PENDING: 'manueller Verkauf angefordert',
  };
  return `<br><span class="small">${esc(texte[s] || s)}${esc(bis)}</span>`;
}

// v9.2: Eigentum, Abgleich und Verwaltung sind DREI Zustaende und werden
// jetzt getrennt gezeigt. Vorher gab es nur einen gemischten Status: eine
// lueckenlos bewiesene, aber pausierte Botposition stand dort als
// "Externer Bestand · ohne identische ID-Kette" -- fachlich falsch.
function zuordnung(r) {
  const eigentumTexte = {
    BEWIESEN: '<span class="ok">Eigentum bewiesen</span>',
    UNVOLLSTAENDIG: '<span class="warn">Eigentum unvollständig</span>',
    KEIN_BEWEIS: '<span class="bad">kein Brokerbeweis</span>',
  };
  const e = eigentumTexte[String(r.eigentum_status || '')] || '<span class="small">Eigentum unbekannt</span>';
  const a = statusDeutsch(r.abgleich_status || r.reconciliation_status);
  const modus = String(r.management_mode || '').toUpperCase();
  const modusTexte = {AUTO: 'automatisch', MANUELL: 'manuell', OBSERVE: 'nur beobachtet', BEOBACHTEN: 'nur beobachtet', PENDING_CONFIRMATION: 'Kauf bestätigt · Schutzprüfung offen'};
  const v = r.verwaltung_status
    ? `Verwaltung: ${esc(r.verwaltung_status)}`
    : (modusTexte[modus] ? `Verwaltung: ${modusTexte[modus]}` : '');
  const migriert = r.strategie_migriert_am
    ? `<br><span class="small">Strategie migriert${r.strategie_migriert_von ? ' von '+esc(r.strategie_migriert_von) : ''} · ${esc(zeit(r.strategie_migriert_am))}</span>`
    : '';
  return `${e}<br><span class="small">Abgleich: ${esc(a)}</span>${v ? `<br><span class="small">${v}</span>` : ''}${migriert}`;
}

function tradeGrund(r, offen, klaerung) {
  // In der Klaerungstabelle steht der Abgleichstatus schon in der Spalte
  // "Zuordnung". Hier steht deshalb, was die Zeile konkret braucht.
  if (klaerung) {
    if (r.ausgestiegen_am) {
      const check=r.ergebnis_recherche||{};
      return 'Als geschlossen erfasst · Ergebnisabgleich offen. ' + esc((r.missing_receipts || ['Vollständiger Ergebnisbeleg fehlt']).join('; '))
        + (check.checked_at ? '<br><span class="small">Letzter belegbezogener Abruf: '+esc(new Date(Number(check.checked_at)*1000).toLocaleString('de-DE'))+' · '+esc(check.detail||check.status)+'</span>' : '');
    }
    const s = String(r.abgleich_status || r.reconciliation_status || '').toUpperCase();
    if (s === 'STRATEGY_MIGRATION_REQUIRED')
      return 'Strategieversion hat gewechselt. Eigentum und Broker-Schutz bleiben; '
           + 'nur die automatischen Ausstiege pausieren bis zur Migration.';
    if (s === 'ACCOUNT_MISMATCH')
      return 'Der Bestand gehört zu einem anderen Brokerkonto. Keine automatische Übernahme.';
    if (String(r.eigentum_status || '') === 'BEWIESEN')
      return 'Brokerbeweis vorhanden — der zuständige Brokerabgleich muss die genaue Zuordnung prüfen.';
    return 'Ohne vollständige Broker-ID-Kette. Nur beobachten, kein Auto-Verkauf.';
  }
  if (!offen) return esc(r.exit_grund || 'unbekannt');
  const p = String(r.protection_status || '').toUpperCase();
  const modus = String(r.management_mode || '').toUpperCase();
  const manual = modus === 'MANUELL' ? ' · manuell verwaltet'
               : modus === 'BEOBACHTEN' ? ' · nur beobachtet (kein Auto-Ausstieg)' : '';
  const notiz = r.management_note ? `<br><span class="small">${esc(r.management_note)}</span>` : '';
  const zustand = exitZustandText(r);
  if (p === 'UNKNOWN_BROKER_STATE' || r.broker_state === 'BROKER_STATE_UNKNOWN') return `Offen · Bestand bei OKX ungeklärt. Kein Verkaufsbeleg; aktueller Schutz nicht bestätigt.${notiz}${zustand}`;
  if (p === 'ACTIVE') return `Offen · ${esc(brokerName(r.broker))}-Schutz bestätigt${r.protection_algo_id ? ' · Algo '+esc(r.protection_algo_id) : ''}${manual}${notiz}${zustand}`;
  if (r.broker==='etoro' && r.protection_snapshot_fresh && r.stop_price!=null && r.broker_take_profit!=null)
    return `Offen · Broker-SL/TP gelesen · Abgleich mit geplanten Werten offen${manual}${notiz}${zustand}`;
  // 9.5.7: "Broker-Schutz fehlt" war zu absolut. Am 03.09.2026 stand das an
  // HYPE, obwohl im Konto sehr wohl eine Schutzorder lag (algoId
  // 3889992275388477441) -- NEXUS konnte sie nur nicht zuordnen. Der Text
  // beschreibt jetzt, was NEXUS wirklich weiss, und nennt den Grund.
  if (p === 'MISSING') {
    const grund = r.protection_detail
      ? `<br><span class="small">${esc(r.protection_detail)}</span>` : '';
    return `Offen · ACHTUNG: kein bestaetigter Broker-Schutz${manual}${grund}${notiz}${zustand}`;
  }
  if (p === 'LOST') return `Offen · ⚠️ Schutz während eines Verkaufs storniert — wird neu gesetzt${notiz}${zustand}`;
  if (p === 'UNKNOWN_AFTER_AMEND') return `Offen · ⚠️ TP/SL-Änderung unbestätigt — bitte im zuständigen Brokerkonto prüfen${notiz}${zustand}`;
  return `Offen · Schutzabgleich läuft${manual}${notiz}${zustand}`;
}

function liveKurs(r) {
  if (r.current_price === null || r.current_price === undefined)
    return '<span class="small">Aktueller Kurs fehlt</span>'+(r.reference_price==null?'':`<br><span class="small">Letzter Referenzkurs: ${esc(zahl(r.reference_price,6))} ${esc(r.market_quote_ccy||r.waehrung||'')} · ${esc(r.reference_source||'')}${r.reference_at?' · '+esc(zeit(r.reference_at)):''}</span>`);
  return `${esc(zahl(r.current_price, 8))} ${esc(r.market_quote_ccy || r.waehrung || '')}<br><span class="small">${esc(r.current_source || brokerName(r.broker))}${r.current_at ? ' · '+esc(zeit(r.current_at)) : ''}</span>`;
}

function schutzZiele(r) {
  const stop = r.stop_price == null ? 'unbekannt' : `${zahl(r.stop_price, 8)}${r.stop_distance_pct == null ? '' : ` (${zahl(r.stop_distance_pct, 2)} %)`}`;
  const roi = r.active_roi_price == null ? '' : `<br><span class="small">Aktives ROI ${zahl(r.active_roi_pct, 1)} %: ${zahl(r.active_roi_price, 8)}</span>`;
  const brokerTp = r.broker_take_profit == null ? '' : `<br><span class="small">Broker-TP: ${zahl(r.broker_take_profit, 8)}</span>`;
  const planned = r.broker==='etoro' && (r.protection_status!=='ACTIVE' || r.stop_price==null || r.broker_take_profit==null) ? `<br><span class="small">Geplante Werte · Bestätigung offen: SL ${esc(zahl(r.planned_stop,8))} · TP ${esc(zahl(r.planned_take_profit,8))}</span>` : '';
  const age = r.broker==='etoro' && !r.protection_snapshot_fresh ? '<br><span class="small">Aktueller Broker-Schutzabgleich ausstehend</span>' : '';
  return `<span>${r.broker==='etoro'?'Broker-SL':'SL'}: ${esc(stop)}</span>${roi}${brokerTp}${planned}${age}`;
}

const tradePages = {open: 1, closed: 1, review: 1};
function nativeExitNote(r) {
  const n=r.native_exit;if(!n?.native_currency)return '';
  if (r.eur_valuation) return `<p class="small">Originalverkauf über ${esc(n.inst_id)}: ${esc(n.gross_quantity)} Stück zu ${esc(zahl(n.avg_price,8))} ${esc(n.native_currency)}. Nettoerlös: ${esc(n.net_proceeds)} ${esc(n.native_currency)}. Originaleinstand: ${esc(zahl(r.native_entry_price,8))} ${esc(r.native_entry_currency)}. Das EUR-Ergebnis verwendet belegte historische Referenzkurse. Rest: ${esc(n.residual)} ${esc(r.symbol)}.</p>`;
  return `<p class="alert">Verkauft über ${esc(n.inst_id)}: ${esc(n.gross_quantity)} Stück zu ${esc(zahl(n.avg_price,8))} ${esc(n.native_currency)}. Netto-Verkaufserlös ${esc(n.net_proceeds)} ${esc(n.native_currency)} — nicht der Gewinn. Einstand in ${esc(r.waehrung)}; historischer Wechselkurs fehlt. Rest: ${esc(n.residual)} ${esc(r.symbol)}.</p>`;
}
function tradeTabelle(rows, offen, klaerung = false) {
  const kind = klaerung ? 'review' : offen ? 'open' : 'closed';
  if (!rows.length) return `<div class="empty-state"><strong>${klaerung ? 'Kein Eintrag zur Klärung' : offen ? 'Keine bestätigte offene Botposition' : 'Noch kein Verkauf in dieser Auswahl'}</strong><p>${offen ? 'Kontoguthaben und manuelle Depotpositionen findest du unter „Guthaben & Verwaltung“.' : 'Wähle bei Bedarf einen längeren Zeitraum.'}</p></div>`;
  const pages = Math.ceil(rows.length / 12);
  const page = tradePages[kind] = Math.min(pages, Math.max(1, tradePages[kind]));
  const body = rows.slice((page - 1) * 12, page * 12).map(r => {
    // A sold trade can need receipts, but never another sell operation.
    const rowOpen = offen && !r.ausgestiegen_am;
    const id = Number(r.trade_id), isOKX = String(r.broker).toLowerCase() === 'okx';
    const protection = String(r.protection_status || '').toUpperCase();
    const warning = rowOpen && !klaerung && protection !== 'ACTIVE';
    const manual = rowOpen && !klaerung && isOKX && hatVerifiziertenBotnachweis(r.ownership_status);
    const exitQuantity = r.native_exit?.gross_quantity == null ? r.menge : Number(r.native_exit.gross_quantity);
    const exitPrice = r.native_exit?.avg_price ?? r.ausstieg_preis;
    const exitCurrency = r.native_exit?.native_currency || r.waehrung || '?';
    return `<article class="trade-row trade-card ${id === ausgewaehlt ? 'selected' : ''} ${warning || klaerung ? 'needs-attention' : ''}" data-trade-id="${id}">
      <div class="trade-card-head"><div><h3>${esc(r.symbol || '?')}</h3><span class="small">${esc(r.instrument || r.broker_position_id || '')}</span></div><div class="trade-card-context">${contextBadge(r)}</div></div>
      <div class="trade-facts"><div><span>${rowOpen ? 'Restmenge' : 'Verkaufte Menge'}</span><strong>${esc(zahl(rowOpen ? r.menge : exitQuantity, 8))}</strong></div><div><span>Einstieg</span><strong>${esc(zahl(r.einstieg_preis, 6))} ${esc(r.waehrung || '?')}</strong></div><div><span>${rowOpen ? 'Aktueller Kurs' : 'Verkaufskurs'}</span><strong>${rowOpen ? liveKurs(r) : esc(zahl(exitPrice, 6) + ' ' + exitCurrency)}</strong></div><div><span>${rowOpen ? esc(r.open_result_label||'Offenes Ergebnis · geschätzt') : 'Ergebnis nach Gebühren'}</span><strong>${rowOpen ? pnlMitProzent(r.open_display_pnl ?? r.open_net_pnl, r.open_display_pct ?? r.open_net_pct, r.market_quote_ccy || r.waehrung) : geschlossenesErgebnis(r)}</strong></div></div>
      <div class="trade-condition ${warning || klaerung ? 'warn' : ''}">${tradeGrund(r, rowOpen, klaerung)}</div>
      ${r.ausgestiegen_am&&r.ergebnis_status==='FEES_UNKNOWN'?'<p class="small"><strong>Historischer Abschluss: Kosten offen, Netto unbekannt.</strong> Dieser Eintrag beschreibt keine aktuelle Position und keinen aktuellen Schutzfehler.</p>':''}${brokerErgebnisBeleg(r)}${nativeExitNote(r)}${r.broker==='etoro'&&r.ausgestiegen_am&&(['UNKNOWN','USER_CONFIRMED','EXPECTED_UNVERIFIED'].includes(r.fee_quality)||r.ergebnis_status==='FEES_UNKNOWN')?`<p>${r.fee_quality==='USER_CONFIRMED'?'<strong>Abrechnung vom Nutzer bestätigt · Barbestandsabgleich</strong><br>':''}${r.fee_quality==='EXPECTED_UNVERIFIED'?'<strong>Abschlussgebühr als Erwartungswert eingetragen · nicht belegt.</strong> Der nächste Barbestandsbeleg ersetzt ihn; eine Bestätigung hier ebenfalls.<br>':''}<button class="secondary etoro-settlement" data-trade-id="${id}">Abschlusskosten prüfen</button></p>`:''}
      ${rowOpen ? `<div class="trade-protection">${schutzZiele(r)}</div>` : ''}
      <div class="trade-card-actions">${isOKX ? `<button class="secondary chart-select" data-trade-id="${id}">Kerzen</button>` : '<span class="small">Kerzenansicht nur für OKX</span>'}
        <details class="trade-more"><summary>Details${manual ? ' & Aktionen' : ''}</summary><div class="trade-detail-body"><p>Kauf ${esc(zeit(r.eingestiegen_am))}${rowOpen ? '' : '<br>Verkauf ' + esc(zeit(r.ausgestiegen_am))}</p>${rowOpen ? zuordnung(r) : ''}${protectionEvidenceView(r.protection_evidence||{},r.protection_plan_history||[])}${rowOpen&&r.broker==='etoro'?accountRiskView(r.account_risk_display||{}):''}${r.broker==='etoro'&&r.fmp_context?fmpContextView(r.fmp_context):''}<p>Strategie: ${esc(modus(r))}<br>Signal: ${esc(r.enter_tag || 'unbekannt')}</p><p class="small">Trade ${id} · Kauforder ${esc(r.entry_order_id || 'unbekannt')}${r.exit_order_id ? '<br>Verkaufsorder ' + esc(r.exit_order_id) : ''}</p>
        ${manual ? `<label>Manuelle Aktion<select class="manual-action" data-trade-id="${id}" data-symbol="${esc(r.symbol)}"><option value="SET_PROTECTION">Stop / Ziel ändern</option><option value="SELL">Preisbegrenzt verkaufen</option></select></label><button class="secondary manual-submit" data-trade-id="${id}" data-symbol="${esc(r.symbol)}">Auftrag vorbereiten</button>` : ''}
        ${rowOpen && isOKX ? `<label>Abgleich<select class="reconcile-action" data-trade-id="${id}" data-symbol="${esc(r.symbol)}"><option value="RECHECK">Erneut mit OKX prüfen</option>${klaerung ? '<option value="CONFIRM_ACCOUNT_ASSET">Als Konto-Asset bestätigen</option>' : ''}</select></label><button class="secondary reconcile-submit" data-trade-id="${id}" data-symbol="${esc(r.symbol)}">Abgleich anfordern</button>` : ''}
        ${klaerung && r.notiz ? '<p>' + esc(r.notiz) + '</p>' : ''}</div></details></div></article>`;
  }).join('');
  const pager = pages > 1 ? `<div class="pager"><button class="secondary trade-page" data-kind="${kind}" data-page="${page - 1}" ${page === 1 ? 'disabled' : ''}>Zurück</button><span>${page} / ${pages} · ${rows.length} Einträge</span><button class="secondary trade-page" data-kind="${kind}" data-page="${page + 1}" ${page === pages ? 'disabled' : ''}>Weiter</button></div>` : '';
  return `<div class="trade-card-grid">${body}</div>${pager}`;
}

function verbindeZeilen() {
  document.querySelectorAll('.etoro-settlement').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();zeigeEtoroAbrechnung(Number(b.dataset.tradeId));}));
  document.querySelectorAll('.chart-select').forEach(button => button.addEventListener('click', () => {
    const panel = document.querySelector('#candle-panel');
    if (panel) panel.open = true;
    ladeChart(Number(button.dataset.tradeId));
    panel?.scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }));
  document.querySelectorAll('.trade-page').forEach(button => button.addEventListener('click', () => {
    tradePages[button.dataset.kind] = Number(button.dataset.page); zeichneTabellen();
    document.querySelector('.trade-tabs')?.scrollIntoView({block:'start'});
  }));
  document.querySelectorAll('.reconcile-action, .reconcile-submit').forEach(node => {
    node.addEventListener('click', e => e.stopPropagation());
  });
  document.querySelectorAll('.manual-action, .manual-submit').forEach(node => {
    node.addEventListener('click', e => e.stopPropagation());
  });
  document.querySelectorAll('.manual-submit').forEach(button => {
    button.addEventListener('click', () => prepareManualOrder(Number(button.dataset.tradeId)));
  });
  document.querySelectorAll('.reconcile-submit').forEach(button => {
    button.addEventListener('click', async e => {
      e.stopPropagation();
      const id = Number(button.dataset.tradeId), symbol = button.dataset.symbol;
      const select = document.querySelector(`.reconcile-action[data-trade-id="${id}"]`);
      const action = select?.value || 'RECHECK';
      const labels = {RECHECK:'erneut mit OKX prüfen', CONFIRM_ACCOUNT_ASSET:'als Konto-Asset bestätigen', DELETE_LOCAL:'lokal löschen'};
      if (!confirm(`${symbol}: Eintrag wirklich ${labels[action]}?\n\nEs wird niemals Guthaben bei OKX verkauft oder gelöscht.`)) return;
      button.disabled = true;
      try {
        const result = await api(`/api/trades/${id}/reconciliation`, {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({action, symbol})
        });
        alert(result.detail);
        await ladeTrades();
      } catch (err) {
        alert(err.message);
      } finally { button.disabled = false; }
    });
  });
}

function prepareManualOrder(id) {
  const row=(tradeDaten.offene_trades||[]).find(x=>Number(x.trade_id)===id);
  if(!row || !hatVerifiziertenBotnachweis(row.ownership_status))return;
  const action=document.querySelector(`.manual-action[data-trade-id="${id}"]`)?.value;
  if(!['SELL','SET_PROTECTION'].includes(action))return;
  document.querySelector('#manual-order-dialog')?.remove();
  const dialog=document.createElement('dialog');dialog.id='manual-order-dialog';dialog.className='order-dialog';
  dialog.setAttribute('aria-labelledby','manual-order-title');
  const sell=action==='SELL', phrase=`${action} ${row.symbol}`;
  dialog.innerHTML=`<form id="manual-order-form"><div class="section-head"><h2 id="manual-order-title">${sell?'Verkauf vorbereiten':'Stop und Ziel ändern'} · ${esc(row.symbol)}</h2><button type="button" class="secondary dialog-cancel" aria-label="Dialog schließen">Schließen</button></div>${contextBadge(row)}<p>${sell?'Die gesamte bestätigte Botposition wird preisbegrenzt verkauft. Eine Teilausführung ist möglich; der Rest wird erneut geschützt.':'Diese Position wird anschließend manuell verwaltet. Automatische Strategieausstiege sind dann deaktiviert; Stop und Ziel bleiben beim Broker.'}</p><p><strong>${esc(zahl(row.menge,8))} ${esc(row.symbol)}</strong> · Trade ${id}</p>${sell?'<label for="manual-lock">Wiedereinstieg sperren</label><select id="manual-lock"><option value="1H">1 Stunde</option><option value="6H" selected>6 Stunden</option><option value="DAY">Bis Tagesende</option><option value="MANUAL">Bis zur manuellen Freigabe</option></select>':`<div class="form-grid"><label for="manual-stop">Stop-Loss (${esc(row.waehrung||'?')})<input id="manual-stop" inputmode="decimal" value="${esc(row.stop_price??'')}" required></label><label for="manual-take">Take-Profit (${esc(row.waehrung||'?')})<input id="manual-take" inputmode="decimal" value="${esc(row.broker_take_profit??'')}" required></label></div>`}<label for="manual-confirm">Zur Bestätigung ${esc(phrase)} eingeben</label><input id="manual-confirm" autocomplete="off" autocapitalize="characters" spellcheck="false" required><p id="manual-order-error" role="status"></p><div class="toolbar"><button type="button" class="secondary dialog-cancel">Abbrechen</button><button type="submit" class="danger" id="manual-order-submit">${sell?'Verkauf beauftragen':'Änderung beauftragen'}</button></div><p class="small">Der Handelskern prüft Bestand, Konto und Preis erneut. Erst die Brokerbestätigung belegt eine Ausführung.</p></form>`;
  document.body.append(dialog);
  dialog.querySelectorAll('.dialog-cancel').forEach(button=>button.addEventListener('click',()=>dialog.close()));
  dialog.addEventListener('close',()=>dialog.remove());
  dialog.querySelector('form').addEventListener('submit',async event=>{
    event.preventDefault();
    const error=dialog.querySelector('#manual-order-error'), confirmation=dialog.querySelector('#manual-confirm').value.trim();
    if(confirmation!==phrase){error.textContent='Die Bestätigung stimmt noch nicht überein.';return;}
    const payload={action,confirmation};
    if(sell)payload.lock=dialog.querySelector('#manual-lock').value;
    else{
      payload.stop=Number(dialog.querySelector('#manual-stop').value.replace(',','.'));
      payload.take_profit=Number(dialog.querySelector('#manual-take').value.replace(',','.'));
      if(!Number.isFinite(payload.stop)||!Number.isFinite(payload.take_profit)||payload.stop<=0||payload.take_profit<=payload.stop){error.textContent='Bitte positive Kurse eingeben. Das Ziel muss über dem Stop liegen.';return;}
    }
    const submit=dialog.querySelector('#manual-order-submit');submit.disabled=true;error.textContent='Auftrag wird gespeichert …';
    try{
      const result=await api(`/api/manual-trades/${id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      dialog.close();await ladeTrades();document.querySelector('#trade-attention').innerHTML=`<p class="alert">${esc(result.detail)} <button class="text-button" onclick="selectTradeView('bestand')">Auftragsstatus ansehen</button></p>`;
    }catch(err){error.textContent=err.message+' Bitte zuerst den Auftragsstatus unter „Guthaben & Verwaltung“ prüfen.';}
  });
  dialog.showModal();
}

function zeichneTabellen() {
  const inventory=document.querySelector('#okx-restbestaende');
  if(inventory){
    const rows=tradeDaten.restbestaende||[];
    inventory.innerHTML=rows.length?`<h3>Belegte, nicht verkaufte Restbestände</h3><p class="small">Diese Mengen sind kein zusätzlicher Verkauf und kein realisierter Verlust. Die Kostenbasis bleibt erhalten. Der heutige Kontobestand wird davon unabhängig abgefragt.</p><div class="trade-cards">${rows.map(r=>`<article class="trade-card"><div class="trade-card-head"><h3>${esc(r.symbol)}</h3>${contextBadge(r)}</div><p>${esc(r.instrument)} · ursprünglicher Trade ${esc(r.trade_id)}</p><div class="trade-facts"><div><span>Nicht verkaufte Menge</span><strong>${esc(r.quantity_text)}</strong></div><div><span>Erhaltene Kostenbasis</span><strong>${esc(r.cost_basis_text)} ${esc(r.waehrung)}</strong></div></div><p class="small">Beleggestützt zugeordnet · kein Verkaufsauftrag</p></article>`).join('')}</div>`:'<p class="small">Keine separat belegten Restbestände in dieser Auswahl.</p>';
  }
  document.querySelector('#open-trades').innerHTML = tradeTabelle(tradeDaten.offene_trades || [], true);
  document.querySelector('#reconcile-trades').innerHTML = tradeTabelle(tradeDaten.klaerungs_trades || [], true, true);
  document.querySelector('#closed-trades').innerHTML = tradeTabelle(tradeDaten.geschlossene_trades || [], false);
  verbindeZeilen();
}

function zeichneManualStatus(){
  const commands=(manualDaten.commands||[]).slice(0,8), locks=manualDaten.locks||[];
  if(!commands.length&&!locks.length){document.querySelector('#manual-trade-status').innerHTML='<p class="small">Keine manuellen Aufträge oder aktiven Sperren.</p>';return;}
  const c=commands.length?`<h3>Letzte Aufträge</h3><table class="trade-table"><thead><tr><th>Zeit</th><th>Wert</th><th>Aktion</th><th>Status</th><th>Details</th></tr></thead><tbody>${commands.map(x=>`<tr><td>${esc(zeit(x.created_at))}</td><td><strong>${esc(x.symbol||'?')}</strong></td><td>${esc(x.action||'')}</td><td>${badge(x.resolved_by?'HISTORIE · SPÄTER GESCHLOSSEN':x.status==='FAILED'?'FEHLVERSUCH':x.status||'UNBEKANNT',x.resolved_by?'':['SUCCEEDED','PARTIAL'].includes(x.status)?'ok':x.status==='FAILED'||x.status==='UNCLEAR'?'bad':'warn')}</td><td>${esc(x.history_note||x.detail||'wartet auf den Handelskern')}</td></tr>`).join('')}</tbody></table>`:'';
  const l=locks.length?`<h3>Aktive Wiedereinstiegssperren</h3><table class="trade-table"><thead><tr><th>Wert</th><th>Bis</th><th>Grund</th><th>Aktion</th></tr></thead><tbody>${locks.map(x=>`<tr><td><strong>${esc(x.symbol||'?')}</strong></td><td>${x.until?esc(zeit(x.until)):'bis zur manuellen Freigabe'}</td><td>${esc(x.reason||'')}</td><td><button class="secondary release-lock" data-lock-id="${esc(x.id||'')}">Sperre aufheben</button></td></tr>`).join('')}</tbody></table>`:'';
  document.querySelector('#manual-trade-status').innerHTML=c+l;
  document.querySelectorAll('.release-lock').forEach(button=>button.addEventListener('click',async()=>{
    if(!confirm('Wiedereinstiegssperre wirklich aufheben? Der nächste Scan darf den Coin wieder kaufen.')) return;
    button.disabled=true;
    try{const result=await api(`/api/manual-trades/locks/${button.dataset.lockId}/release`,{method:'POST'});alert(result.detail);await ladeManualStatus();}
    catch(err){alert(err.message);}finally{button.disabled=false;}
  }));
}

async function ladeManualStatus(){
  try{manualDaten=await api('/api/manual-trades');zeichneManualStatus();}
  catch(e){document.querySelector('#manual-trade-status').innerHTML=`<p class="small trade-neg">${esc(e.message)}</p>`;}
}

function svgText(x, y, text, anchor = 'start', klasse = '') {
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" class="${klasse}">${esc(text)}</text>`;
}

function zeichneProfit() {
  const target=document.querySelector('#profit-chart'),legend=document.querySelector('#profit-legend');
  if(!document.querySelector('#profit-series')){
    const field=document.createElement('label');
    field.className='field';field.textContent='Ergebnisdarstellung ';
    const select=document.createElement('select');select.id='profit-series';
    for(const [value,label] of [['net','Bestätigtes Netto'],['gross','Brutto vor Gebühren']]){
      const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option);
    }
    if(!(tradeDaten.context_groups||[]).some(g=>(g.curve||[]).length))select.value='gross';
    select.addEventListener('change',zeichneProfit);field.append(select);target.before(field);
  }
  const gross=document.querySelector('#profit-series')?.value==='gross';
  const series=g=>gross?(g.gross_curve||[]):(g.curve||[]);
  const groups=(tradeDaten.context_groups||[]).filter(g=>series(g).length);
  if(legend)legend.textContent=gross?'Brutto vor Gebühren; unvollständige Abschlüsse werden ausgelassen. Getrennt je Konto und Währung.':'Bestätigtes Netto. Eigene Achse je Broker, Umgebung, Konto und Währung. Keine Währungsumrechnung.';
  if(!groups.length){target.innerHTML=`<div class="trade-empty">${gross?'Keine belegten Bruttoergebnisse in der Auswahl.':'Noch keine bestätigten Nettoergebnisse. Wähle „Brutto vor Gebühren“, um belegte Kursergebnisse anzuzeigen.'}</div>`;return;}
  target.innerHTML=groups.map(g=>{
    const rows=series(g).filter(r=>r.wert!=null&&Number.isFinite(Number(r.wert)));
    const width=640,height=210,l=70,right=20,top=24,bottom=28;
    let low=Math.min(0,...rows.map(r=>r.wert)),high=Math.max(0,...rows.map(r=>r.wert));
    if(low===high){low-=1;high+=1;}
    const y=v=>top+(high-v)*(height-top-bottom)/(high-low);
    const x=i=>l+(rows.length===1?.5:i/(rows.length-1))*(width-l-right);
    const line=rows.map((r,i)=>`${x(i)},${y(r.wert)}`).join(' ');
    const col=g.broker==='etoro'?'#53d7b1':'#a4b5ff';
    // Both series remain explicitly named; gross never becomes risk-booked net.
    return `<section class="domain-results ${g.broker}">${contextBadge({display_context:g})}<h3>Kumuliertes Netto · ${esc(g.currency)}</h3><svg viewBox="0 0 ${width} ${height}" class="chart-svg" aria-label="${esc(g.label)} ${esc(g.currency)}">${[0,1,2,3,4].map(i=>{const v=low+(high-low)*i/4;return `<line x1="${l}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" class="chart-grid"/>${svgText(l-8,y(v)+4,zahl(v),'end','chart-label')}`}).join('')}<line x1="${l}" y1="${y(0)}" x2="${width-right}" y2="${y(0)}" class="zero-line"/><polyline points="${line}" fill="none" stroke="${col}" stroke-width="3"/>${rows.map((r,i)=>`<circle cx="${x(i)}" cy="${y(r.wert)}" r="4" fill="${col}"><title>${esc(r.symbol)} · ${esc(zeit(r.zeit))} · ${esc(zahl(r.pnl))} ${esc(g.currency)}</title></circle>`).join('')}</svg><p class="small">${esc(zeit(rows[0].zeit))} bis ${esc(zeit(rows.at(-1).zeit))}</p></section>`;
  }).join('');
  target.setAttribute('aria-label',gross?'Kumulierte Bruttoergebnisse vor Gebühren':'Bestätigte kumulierte Nettoergebnisse');
  if(gross)target.querySelectorAll('h3').forEach((node,i)=>{
    node.textContent=`Kumuliertes Brutto vor Gebühren · ${groups[i].currency}`;
    const note=document.createElement('p');note.className='small';
    note.textContent=`${groups[i].gross_missing||0} Abschlüsse ohne Bruttoergebnis ausgelassen. Kein Nettoergebnis.`;
    node.after(note);
  });
}

function candleZeitX(iso, candles, x) {
  const t = new Date(iso).getTime();
  let best = 0, dist = Infinity;
  candles.forEach((c, i) => { const d = Math.abs(new Date(c.zeit).getTime() - t); if (d < dist) { dist = d; best = i; } });
  return x(best);
}

// 10.4.0: Kerzenansicht mit ECharts (lokal, Apache-2.0) fuer OKX und eToro.
// Zoom per Mausrad/Pinch, Verschieben per Ziehen, Schieberegler, Fadenkreuz,
// Volumen-Teilfenster, Zeitrahmen-Wahl. Ohne ECharts (z. B. im Node-Test)
// bleibt die bisherige SVG-Darstellung als Rueckfall.
let kerzenChart = null;
const CHART_TOKENS = () => {
  const cs = getComputedStyle(document.documentElement);
  const t = k => (cs.getPropertyValue(k) || '').trim();
  return {bg: t('--panel') || '#101d31', text: t('--text') || '#e6edf7', muted: t('--muted') || '#91a4bc',
          line: t('--line') || '#263b57', green: t('--green') || '#39d98a', red: t('--red') || '#ff667a',
          blue: t('--blue') || '#4ca3ff', amber: t('--amber') || '#ffbd59'};
};

function zeichneZeitrahmen() {
  const bars = document.querySelector('#trade-chart-bars');
  if (!bars) return;
  const available = chartDaten?.available_bars || [];
  if (!chartDaten?.ok || !available.length) { bars.innerHTML = ''; return; }
  bars.innerHTML = available.map(b => `<button class="secondary chart-bar${b === chartDaten.bar ? ' active' : ''}" data-bar="${esc(b)}" aria-pressed="${b === chartDaten.bar}">${esc(b)}</button>`).join('')
    + `<span class="small">${esc(chartDaten.data_source || '')}${chartDaten.data_age_seconds != null ? ' · Stand vor ' + Math.round(chartDaten.data_age_seconds / 60) + ' min' : ''}</span>`;
  bars.querySelectorAll('[data-bar]').forEach(b => b.addEventListener('click', () => ladeChart(ausgewaehlt, b.dataset.bar)));
}

function kerzenIndexZuZeit(iso, candles) {
  const t = new Date(iso).getTime();
  let best = 0, dist = Infinity;
  candles.forEach((c, i) => { const d = Math.abs(new Date(c.zeit).getTime() - t); if (d < dist) { dist = d; best = i; } });
  return best;
}

function preisStellen(preis) {
  const p = Math.abs(Number(preis) || 0);
  return p < 0.0001 ? 8 : p < 0.01 ? 6 : p < 1 ? 5 : p < 100 ? 3 : 2;
}

function zeichneKerzenECharts(ziel, candles) {
  const tok = CHART_TOKENS();
  const dezimal = preisStellen(candles[candles.length - 1].close);
  const xs = candles.map(c => c.zeit);
  // Perzentil-Klemmung: Docht-Ausreisser werden nur in der Zeichnung auf die
  // Skala (1./99. Perzentil der Kerzenkoerper, vom Server) gekappt; das
  // Fadenkreuz zeigt weiterhin die echten Werte aus `candles`.
  const axis = chartDaten.axis || {};
  const klemme = v => (axis.min != null && v < axis.min) ? axis.min : (axis.max != null && v > axis.max) ? axis.max : v;
  const ohlc = candles.map(c => [Number(c.open), Number(c.close), klemme(Number(c.low)), klemme(Number(c.high))]);
  const vol = candles.map((c, i) => ({value: Number(c.volume || 0), itemStyle: {color: Number(c.close) >= Number(c.open) ? tok.green : tok.red, opacity: .45}}));
  const lines = [[chartDaten.stop, 'Stop-Loss', tok.red, 'insideEndTop'], [chartDaten.take_profit, 'Broker-TP', tok.green, 'insideEndTop'],
                 [chartDaten.active_roi_price, `Aktives ROI ${zahl(chartDaten.active_roi_pct, 1)}%`, tok.amber, 'insideEndTop'],
                 [chartDaten.current_price, 'Aktuell', tok.blue, 'insideEndBottom']].filter(l => l[0] != null);
  const marker = (obj, label, color, symbol) => obj && obj.preis != null ? [{
    name: label, coord: [kerzenIndexZuZeit(obj.zeit, candles), Number(obj.preis)], value: `${label} ${zahl(obj.preis, dezimal)}`,
    symbol, symbolSize: 18, itemStyle: {color}, label: {show: true, position: symbol === 'triangle' ? 'bottom' : 'top', color: tok.text, fontWeight: 700, fontSize: 11}}] : [];
  const points = [...marker(chartDaten.kauf, 'KAUF', tok.green, 'triangle'), ...marker(chartDaten.verkauf, 'VERKAUF', tok.red, 'diamond')];
  // Gleicher Trade, gleiche Daten: nur Groesse anpassen, Zoom des Nutzers bleibt.
  const reihe = `${chartDaten.trade_id}:${chartDaten.bar}`;
  const key = `${reihe}:${candles.length}:${candles[candles.length - 1].zeit}:${chartDaten.current_price}`;
  if (kerzenChart && kerzenChart.nexusKey === key && ziel.querySelector('canvas')) { kerzenChart.resize(); return; }
  let zoom = null;
  if (kerzenChart && kerzenChart.nexusReihe === reihe) {
    try { const dz = kerzenChart.getOption().dataZoom; if (dz && dz[0] && dz[0].start != null) zoom = {start: dz[0].start, end: dz[0].end}; } catch (e) { zoom = null; }
  }
  // Standardansicht: Trade-Zeitraum plus 20 Kerzen davor.
  const kaufIdx = chartDaten.kauf ? kerzenIndexZuZeit(chartDaten.kauf.zeit, candles) : Math.max(0, candles.length - 80);
  const start = zoom ? zoom.start : Math.max(0, (kaufIdx - 20) / candles.length * 100);
  const end = zoom ? zoom.end : 100;
  const option = {
    backgroundColor: 'transparent', animation: false,
    textStyle: {color: tok.text, fontFamily: 'inherit'},
    axisPointer: {link: [{xAxisIndex: 'all'}], label: {backgroundColor: tok.line, color: tok.text}},
    tooltip: {trigger: 'axis', axisPointer: {type: 'cross', crossStyle: {color: tok.muted}}, backgroundColor: tok.bg, borderColor: tok.line, textStyle: {color: tok.text, fontSize: 12},
      formatter: params => {
        const k = params.find(p => p.seriesType === 'candlestick'); if (!k) return '';
        const [o, c, l, h] = k.data.slice(1); const v = candles[k.dataIndex]?.volume;
        return `<strong>${esc(zeit(xs[k.dataIndex]))}</strong><br>O ${zahl(o, dezimal)} · H ${zahl(h, dezimal)}<br>T ${zahl(l, dezimal)} · S ${zahl(c, dezimal)}<br>Volumen ${zahl(v, 2)}`;
      }},
    grid: [{left: 64, right: 88, top: 22, height: '56%'}, {left: 64, right: 88, top: '70%', height: '14%'}],
    xAxis: [{type: 'category', data: xs, boundaryGap: true, axisLine: {lineStyle: {color: tok.line}}, axisLabel: {color: tok.muted, formatter: v => zeit(v).slice(0, 16)}, splitLine: {show: false}, min: 'dataMin', max: 'dataMax'},
            {type: 'category', gridIndex: 1, data: xs, boundaryGap: true, axisLabel: {show: false}, axisLine: {lineStyle: {color: tok.line}}, min: 'dataMin', max: 'dataMax'}],
    // Die Preisskala folgt dem sichtbaren Ausschnitt (Zoom); Schutzlinien
    // bleiben ueber eine unsichtbare Hilfsreihe immer im Bild.
    yAxis: [{scale: true, position: 'right', boundaryGap: ['6%', '6%'], axisLabel: {color: tok.muted, formatter: v => zahl(v, dezimal)}, splitLine: {lineStyle: {color: tok.line, opacity: .5}}, axisPointer: {label: {formatter: p => zahl(p.value, dezimal)}}},
            {scale: true, gridIndex: 1, position: 'right', splitNumber: 2, axisLabel: {color: tok.muted, formatter: v => zahl(v, 0)}, splitLine: {show: false}}],
    dataZoom: [{type: 'inside', xAxisIndex: [0, 1], start, end, zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false},
               {type: 'slider', xAxisIndex: [0, 1], start, end, bottom: 4, height: 18, borderColor: tok.line, fillerColor: 'rgba(76,163,255,.18)', handleStyle: {color: tok.blue}, textStyle: {color: tok.muted}, dataBackground: {lineStyle: {color: tok.muted}, areaStyle: {color: tok.line}}}],
    series: [
      {type: 'candlestick', name: 'Kerzen', data: ohlc, barWidth: '70%',
       itemStyle: {color: tok.green, color0: tok.red, borderColor: tok.green, borderColor0: tok.red},
       markPoint: {data: points, silent: true},
       markLine: {silent: true, symbol: 'none', lineStyle: {type: 'dashed', width: 1.2}, label: {color: tok.text, fontSize: 11},
                  data: lines.map(([v, label, color, position]) => ({yAxis: Number(v), name: label, lineStyle: {color}, label: {position, formatter: `${label} ${zahl(v, dezimal)}`}}))}},
      ...lines.map(([v, label]) => ({type: 'line', name: `${label} (Skala)`, data: xs.map(() => Number(v)), silent: true, showSymbol: false, lineStyle: {opacity: 0}, tooltip: {show: false}, animation: false})),
      {type: 'bar', name: 'Volumen', xAxisIndex: 1, yAxisIndex: 1, data: vol, large: true},
    ],
  };
  if (kerzenChart) { kerzenChart.dispose(); kerzenChart = null; }
  ziel.innerHTML = '';
  ziel.style.minHeight = (window.innerWidth < 600 ? 360 : 460) + 'px';
  kerzenChart = echarts.init(ziel, null, {renderer: 'canvas'});
  kerzenChart.nexusKey = key; kerzenChart.nexusReihe = reihe;
  kerzenChart.setOption(option, true);
}

function zeichneKerzenSVG(ziel, candles) {
  // Rueckfall ohne ECharts: statische Darstellung wie bis 10.3.1.
  const width = Math.max(320, ziel.clientWidth || 850), height = Math.max(310, Math.min(510, width * .54));
  const pad = {l: width < 500 ? 60 : 76, r: 22, t: 28, b: 52};
  const extra = [chartDaten.kauf?.preis, chartDaten.verkauf?.preis, chartDaten.stop, chartDaten.take_profit, chartDaten.active_roi_price, chartDaten.current_price].filter(v => v != null).map(Number);
  let min = Math.min(...candles.map(c => Number(c.low)), ...extra), max = Math.max(...candles.map(c => Number(c.high)), ...extra);
  const margin = Math.max((max-min) * .08, max * .001); min -= margin; max += margin;
  const plotW = width-pad.l-pad.r, plotH = height-pad.t-pad.b;
  const step = plotW / candles.length, bodyW = Math.max(2, Math.min(10, step*.62));
  const x = i => pad.l + step * (i + .5), y = v => pad.t + (max-Number(v))*plotH/(max-min);
  let grid = '';
  for (let i=0;i<=5;i++) { const v=min+(max-min)*i/5, yy=y(v); grid += `<line x1="${pad.l}" y1="${yy}" x2="${width-pad.r}" y2="${yy}" class="chart-grid"/>${svgText(pad.l-9,yy+4,zahl(v, Math.abs(v)<1?6:2),'end','chart-label')}`; }
  const bodies = candles.map((c,i) => {
    const up=Number(c.close)>=Number(c.open), top=y(Math.max(Number(c.open),Number(c.close))), bottom=y(Math.min(Number(c.open),Number(c.close)));
    return `<g class="candle ${up?'up':'down'}"><line x1="${x(i)}" y1="${y(c.high)}" x2="${x(i)}" y2="${y(c.low)}"/><rect x="${x(i)-bodyW/2}" y="${top}" width="${bodyW}" height="${Math.max(1,bottom-top)}"><title>${esc(zeit(c.zeit))}\nO ${esc(zahl(c.open,8))} · H ${esc(zahl(c.high,8))} · T ${esc(zahl(c.low,8))} · S ${esc(zahl(c.close,8))}</title></rect></g>`;
  }).join('');
  const lines = [[chartDaten.stop,'Stop-Loss','stop-line'],[chartDaten.take_profit,'Broker-TP','target-line'],[chartDaten.active_roi_price,`Aktives ROI ${zahl(chartDaten.active_roi_pct,1)}%`,'roi-line'],[chartDaten.current_price,'Aktuell','current-line']].filter(x=>x[0]!=null).map(([v,label,k])=>`<line x1="${pad.l}" y1="${y(v)}" x2="${width-pad.r}" y2="${y(v)}" class="${k}"/>${svgText(width-pad.r-5,y(v)-6,`${label} ${zahl(v,8)}`,'end','line-label')}`).join('');
  const marker = (obj, label, klasse, richtung) => {
    if (!obj || obj.preis == null) return '';
    const xx=candleZeitX(obj.zeit,candles,x), yy=y(obj.preis), p=richtung==='up'?`${xx},${yy-4} ${xx-8},${yy+12} ${xx+8},${yy+12}`:`${xx},${yy+4} ${xx-8},${yy-12} ${xx+8},${yy-12}`;
    const ly=richtung==='up'?yy+29:yy-19;
    return `<polygon points="${p}" class="${klasse}"><title>${esc(label)} · ${esc(zeit(obj.zeit))} · ${esc(zahl(obj.preis,8))}</title></polygon>${svgText(xx,ly,`${label} ${zahl(obj.preis,8)}`,'middle',`${klasse}-text`)}`;
  };
  const kauf=marker(chartDaten.kauf,'KAUF','buy-marker','up'), verkauf=marker(chartDaten.verkauf,'VERKAUF','sell-marker','down');
  const ticks=(width < 500 ? [0,candles.length-1] : [0,Math.floor((candles.length-1)/2),candles.length-1]).map(i=>svgText(x(i),height-16,zeit(candles[i].zeit),'middle','chart-label')).join('');
  ziel.innerHTML=`<svg viewBox="0 0 ${width} ${height}" class="chart-svg" aria-label="Kerzen für ${esc(chartDaten.instrument)}">${grid}${bodies}${lines}${kauf}${verkauf}${ticks}</svg>`;
}

function zeichneKerzen() {
  const ziel = document.querySelector('#trade-chart');
  const axisNote = document.querySelector('#trade-chart-axisnote');
  if (!chartDaten?.ok || !(chartDaten.candles || []).length) {
    if (kerzenChart) { kerzenChart.dispose(); kerzenChart = null; }
    ziel.innerHTML = `<div class="trade-empty">${esc(chartDaten ? (chartDaten.fehler || 'Keine Kerzendaten verfügbar.') : 'Wähle eine Position oder einen Verkauf.')}</div>`;
    document.querySelector('#trade-chart-legend').innerHTML = '';
    if (axisNote) axisNote.textContent = '';
    zeichneZeitrahmen();
    return;
  }
  const candles = chartDaten.candles;
  if (typeof echarts !== 'undefined' && echarts && typeof echarts.init === 'function') zeichneKerzenECharts(ziel, candles);
  else zeichneKerzenSVG(ziel, candles);
  zeichneZeitrahmen();
  if (axisNote) axisNote.textContent = (chartDaten.axis?.clipped ? `${chartDaten.axis.clipped} Docht-Ausreißer außerhalb der Skala (Werte im Fadenkreuz sichtbar). ` : '') + `${candles.length} Kerzen geladen.`;
  document.querySelector('#trade-chart-title').textContent=`${brokerName(chartDaten.display_context?.broker||'okx')} · ${chartDaten.display_context?.environment||'Umgebung unbekannt'} · ${chartDaten.instrument} · Trade ${chartDaten.trade_id}`;
  const stellen = preisStellen(chartDaten.letzter_schluss);
  document.querySelector('#trade-chart-info').textContent=`${chartDaten.bar}-Kerzen · nur abgeschlossen · letzter Schluss ${zahl(chartDaten.letzter_schluss,stellen)} ${chartDaten.waehrung || ''}${chartDaten.current_price ? ` · aktuell ${zahl(chartDaten.current_price,stellen)} (${chartDaten.current_source})` : ''}`;
  document.querySelector('#trade-chart-mode').textContent=chartDaten.entry_strategy_mode || 'UNBEKANNT';
  document.querySelector('#trade-chart-mode').className='status '+(String(chartDaten.entry_strategy_mode||'').includes('FREQTRADE')?'warn':'ok');
  document.querySelector('#trade-chart-legend').innerHTML='<span><i class="legend-buy"></i>Kauf</span><span><i class="legend-sell"></i>Verkauf</span><span><i class="legend-up"></i>Steigende Kerze</span><span><i class="legend-down"></i>Fallende Kerze</span>'+(chartDaten.stop!=null?'<span><i class="legend-stop"></i>Stop-Loss</span>':'')+(chartDaten.take_profit!=null?'<span><i class="legend-target"></i>Broker-TP</span>':'')+(chartDaten.active_roi_price!=null?'<span><i class="legend-roi"></i>Aktives ROI-Ziel</span>':'')+(chartDaten.current_price!=null?'<span><i class="legend-current"></i>Aktueller Verkauf-VWAP</span>':'');
}

let chartRequest=0, tradesRequest=0, chartBar='';
async function ladeChart(id, bar) {
  const request=++chartRequest;
  const wechsel = id !== ausgewaehlt || (bar !== undefined && bar !== chartBar);
  if (bar !== undefined) chartBar = bar; else if (id !== ausgewaehlt) chartBar = '';
  ausgewaehlt=id; document.querySelectorAll('.trade-row').forEach(row => row.classList.toggle('selected', Number(row.dataset.tradeId) === id));
  const row=[...(tradeDaten.offene_trades||[]),...(tradeDaten.klaerungs_trades||[]),...(tradeDaten.geschlossene_trades||[])].find(r=>Number(r.trade_id)===id)||{};
  // Stille Aktualisierung (Abfrage alle 20 s): laufende Ansicht samt Zoom bleibt stehen.
  if (wechsel || !kerzenChart) {
    chartDaten=null;
    document.querySelector('#trade-chart-title').textContent=`${brokerName(row.broker)} · ${row.symbol||'?'} · Trade ${id}`;
    document.querySelector('#trade-chart-info').textContent='';document.querySelector('#trade-chart-mode').textContent='LÄDT';
    document.querySelector('#trade-chart-legend').textContent='';
    if (kerzenChart) { kerzenChart.dispose(); kerzenChart = null; }
    document.querySelector('#trade-chart').innerHTML='<div class="trade-empty">Kerzen werden geladen …</div>';
  }
  try { const result=await api(`/api/trades/${id}/candles${chartBar?`?bar=${encodeURIComponent(chartBar)}`:''}`);if(request!==chartRequest)return;chartDaten=result;zeichneKerzen();if(!result.ok)document.querySelector('#trade-chart-mode').textContent='KEINE KERZEN'; }
  catch(e) {if(request!==chartRequest)return;chartDaten={ok:false,fehler:e.message};zeichneKerzen();}
}
async function ladeTrades() {
  const previousSelection=ausgewaehlt; const request=++tradesRequest; ++chartRequest;
  const chartLeeren=()=>{chartDaten=null;ausgewaehlt=null;if(kerzenChart){kerzenChart.dispose();kerzenChart=null;}
    document.querySelector('#trade-chart-title').textContent='Kerzen und Ausführungen';document.querySelector('#trade-chart-info').textContent='Wähle eine Position oder einen Verkauf (OKX und eToro).';
    document.querySelector('#trade-chart-mode').textContent='KEIN TRADE';document.querySelector('#trade-chart').innerHTML='<div class="trade-empty">Noch kein Trade ausgewählt.</div>';document.querySelector('#trade-chart-legend').textContent='';
    const bars=document.querySelector('#trade-chart-bars');if(bars)bars.innerHTML='';const note=document.querySelector('#trade-chart-axisnote');if(note)note.textContent='';};
  if(previousSelection===null)chartLeeren();
  const broker=document.querySelector('#trade-broker').value, tage=document.querySelector('#trade-tage').value;
  try {
    const result=await api(`/api/trades?broker=${encodeURIComponent(broker)}&tage=${encodeURIComponent(tage)}`);
    if(request!==tradesRequest)return;tradeDaten=result;
    zeichneKennzahlen();zeichneTabellen();zeichneProfit();updateTradeCounts();await Promise.all([ladeManualStatus(), ladeExecutions()]);
    const currentRows=[...(tradeDaten.offene_trades||[]),...(tradeDaten.klaerungs_trades||[]),...(tradeDaten.geschlossene_trades||[])];
    if(previousSelection && currentRows.some(r=>Number(r.trade_id)===previousSelection)) await ladeChart(previousSelection);
    else if(previousSelection!==null) chartLeeren();
    document.querySelector('#trade-note').textContent=tradeDaten.hinweis||'';
  }catch(e){if(request!==tradesRequest)return;document.querySelector('#trade-note').textContent=e.message;document.querySelector('#trade-attention').innerHTML='<p class="alert danger">Handelsdaten nicht aktuell: '+esc(e.message)+'. Sichtbare Positionen stammen vom letzten erfolgreichen Abruf.</p>';}
}


let activeExecutions = [];
let executionReadError = false;
let executionRequest = 0;
function updateTradeCounts() {
  const counts = {offen:(tradeDaten.offene_trades||[]).length, geschlossen:(tradeDaten.geschlossene_trades||[]).length, klaerung:(tradeDaten.klaerungs_trades||[]).length};
  Object.entries(counts).forEach(([name,count]) => { const n=document.querySelector('#count-'+name); if(n)n.textContent=count; });
  const warnings = (tradeDaten.offene_trades||[]).filter(r => String(r.protection_status||'').toUpperCase() !== 'ACTIVE');
  const text = [executionReadError ? 'Auftragsstatus derzeit nicht lesbar' : '', counts.klaerung ? `${counts.klaerung} Einträge benötigen Klärung` : '', activeExecutions.length ? `${activeExecutions.length} Aufträge noch im Abgleich` : '', warnings.length ? `${warnings.length} offene Positionen ohne bestätigten Schutz` : ''].filter(Boolean).join(' · ');
  const n=document.querySelector('#trade-attention'); if(n)n.innerHTML=text?`<p class="alert danger">${esc(text)}. <button class="text-button" onclick="selectTradeView('klaerung')">Abgleich ansehen</button></p>`:'';
}
async function ladeExecutions() {
  const request=++executionRequest;
  try {
    const data = await api('/api/executions?broker='+encodeURIComponent(document.querySelector('#trade-broker').value));
    if(request!==executionRequest)return;
    if(data.complete===false||data.error)throw new Error(data.error||'Die Auftragsquelle konnte nicht vollständig gelesen werden.');
    if(!Array.isArray(data.orders))throw new Error('Auftragsliste fehlt in der Antwort.');
    activeExecutions=data.orders; executionReadError=false;
    const target=document.querySelector('#execution-status');
    target.innerHTML=activeExecutions.length ? '<div class="execution-list">'+activeExecutions.map(executionCard).join('')+'</div>' : '<p class="small">Keine offenen Aufträge im gespeicherten Auftragsbuch. Das bestätigt keinen vollständigen Brokerabgleich; ältere ungeklärte Bestände stehen zusätzlich unten.</p>';
    bindExecutionDetails(target,activeExecutions);
    updateTradeCounts();
  } catch(e) {if(request!==executionRequest)return; executionReadError=true; updateTradeCounts(); document.querySelector('#execution-status').innerHTML=`<p class="alert danger">Auftragsabgleich nicht lesbar: ${esc(e.message)}</p>`;}
}
function executionLabel(state) {
  if(state==='POSITION_CLOSED_ORDER_UNPROVEN')return 'Position geschlossen · Auftragsausgang ungeklärt';
  return ({PREPARED:'Vorbereitet · noch nicht gesendet',SUBMITTING:'Wird übermittelt',OPEN:'Beim Broker offen',UNCLEAR:'Status unklar',PARTIALLY_FILLED:'Teilweise ausgeführt',PARTIALLY_FILLED_CANCELED:'Teil ausgeführt · Rest storniert',AWAITING_TERMINAL:'Ausgeführt · Abschluss offen',FILLED:'Ausgeführt',CANCELED:'Storniert',REJECTED:'Abgelehnt',EXPIRED:'Verfallen'})[state]||state;
}
function selectTradeView(name, focus=false) {
  if(!['offen','geschlossen','klaerung','bestand'].includes(name))name='offen';
  document.querySelectorAll('.trade-tabs [role=tab]').forEach(button=>{const selected=button.dataset.view===name;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;if(selected&&focus)button.focus();});
  document.querySelectorAll('[role=tabpanel]').forEach(panel=>panel.hidden=panel.id!=='view-'+name);
  history.replaceState(null,'','#'+name);
  if(name==='geschlossen')zeichneProfit();
}
document.querySelectorAll('.trade-tabs [role=tab]').forEach(button=>{
  button.addEventListener('click',()=>selectTradeView(button.dataset.view));
  button.addEventListener('keydown',event=>{
    const tabs=[...document.querySelectorAll('.trade-tabs [role=tab]')], index=tabs.indexOf(button);
    const next=event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:event.key==='Home'?0:event.key==='End'?tabs.length-1:null;
    if(next!==null){event.preventDefault();selectTradeView(tabs[next].dataset.view,true);}
  });
});
selectTradeView(location.hash.slice(1));

document.querySelector('#trade-refresh').addEventListener('click',ladeTrades);
document.querySelector('#trade-broker').addEventListener('change',()=>{ladeTrades();if(document.querySelector('#execution-history-panel')?.open)loadExecutionHistory(1);});
document.querySelector('#trade-tage').addEventListener('change',ladeTrades);
let resizeTimer;
window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{zeichneProfit();zeichneKerzen();},150)});
const initialBroker=new URLSearchParams(location.search).get('broker');
if(['okx','etoro'].includes(initialBroker))document.querySelector('#trade-broker').value=initialBroker;
document.querySelector('#execution-history-panel')?.addEventListener('toggle',event=>{if(event.target.open)loadExecutionHistory(1);});
// A read-only detail stays stable while the surrounding active status refreshes.
nexusPoll(async()=>{
  if(document.querySelector('.trade-more[open], #manual-order-dialog[open], #execution-detail-dialog[open], #etoro-settlement-dialog[open]')){
    await Promise.all([ladeExecutions(),ladeManualStatus()]);
  }else{await ladeTrades();}
},20000);


async function zeigeEtoroAbrechnung(id){
  let dialog=document.querySelector('#etoro-settlement-dialog');
  if(!dialog){dialog=document.createElement('dialog');dialog.id='etoro-settlement-dialog';dialog.className='order-dialog';dialog.setAttribute('aria-label','eToro Abschlussabrechnung');dialog.style.cssText='max-width:760px;width:90%;max-height:90vh;overflow:auto';document.body.appendChild(dialog);}
  dialog.innerHTML='<h2>Abschlusskosten prüfen</h2><p role="status">Belege werden gelesen …</p><button class="secondary" data-dismiss>Schließen</button>';
  dialog.querySelector('[data-dismiss]').onclick=()=>dialog.close();dialog.showModal();
  try{
    const p=await api(`/api/etoro/settlement/${id}`);
    dialog.innerHTML=`<h2>${esc(p.symbol)} · Abschlussabrechnung</h2><p>Position ${esc(p.position_id)} · ${p.paper?'DEMO':'LIVE'} · Konto ${esc(p.account.slice(0,8))}</p>
      <table><tbody><tr><td>Barbestand vorher (${esc(zeit(p.before.observed_at))})</td><td>${esc(p.before.cash_usd)} USD</td></tr>
      <tr><td>Barbestand nachher (${esc(zeit(p.after.observed_at))})</td><td>${esc(p.after.cash_usd)} USD</td></tr>
      <tr><td>Bruttoverkaufserlös</td><td>${esc(p.gross_proceeds)} USD</td></tr><tr><td>Zuwachs des Barbestands</td><td>${esc(p.cash_delta)} USD</td></tr>
      <tr><td>Daraus abgeleitete Abschlusskosten</td><td>${esc(p.proposed_exit_cost)} USD</td></tr>
      <tr><td>Bereits belegte Einstiegskosten</td><td>${esc(p.entry_cost)} USD</td></tr><tr><td>Ergebnis nach diesen Kosten</td><td>${esc(p.proposed_net)} USD</td></tr></tbody></table>
      <p>Die Differenz ist nur dann dem Verkauf zuzuordnen, wenn im Zeitraum keine weiteren Geldbewegungen lagen. Das ist eine bestätigte Ableitung aus Barbeständen und kein nativer Gebührenbeleg.</p>
      <p class="small">${p.automatic_release?'Automatische Verbuchung: Belege eindeutig (übriger Positionsbestand unverändert, keine offenen Orders, Intervall '+esc(String(p.interval_seconds))+' s, Kosten innerhalb '+esc(p.automatic_cost_limit)+' USD). NEXUS protokolliert diese Abrechnung selbst beim nächsten Abgleich.':'Keine automatische Verbuchung: '+esc(p.automatic_block_reason||'Belege nicht eindeutig')+'. Deine Bestätigung bleibt erforderlich.'}</p>
      ${p.completed?'<p><strong>Diese Abrechnung wurde bereits protokolliert'+(p.source==='BROKER_CASH_DELTA_AUTOMATIC'?' (automatisch aus Barbestandsbelegen)':'')+'.</strong></p>':'<label><input type="checkbox" id="etoro-cash-confirm"> Ich habe geprüft: Im angezeigten Zeitraum gab es keine anderen Ein-/Auszahlungen, Dividenden, Zinsen, Finanzierungen oder sonstigen Geldbewegungen. Die Differenz gehört vollständig zu diesem Abschluss.</label><p><button id="etoro-settlement-save" disabled>Abrechnung bestätigen</button></p>'}
      <p role="status" id="etoro-settlement-message"></p><button class="secondary" data-dismiss>Schließen</button>`;
    dialog.querySelector('[data-dismiss]').onclick=()=>dialog.close();
    const check=dialog.querySelector('#etoro-cash-confirm'),button=dialog.querySelector('#etoro-settlement-save');
    if(check){check.onchange=()=>{button.disabled=!check.checked;};button.onclick=async()=>{
      button.disabled=true;check.disabled=true;
      try{const result=await api(`/api/etoro/settlement/${id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:p.token,no_other_cashflows:check.checked})});dialog.querySelector('#etoro-settlement-message').textContent=result.detail;await ladeTrades();}
      catch(err){dialog.querySelector('#etoro-settlement-message').textContent=err.message;check.disabled=false;button.disabled=!check.checked;}
    };}
  }catch(err){dialog.querySelector('[role="status"]').textContent=err.message;}
}

async function ladeEtoroStornos(){
  let panel=document.querySelector('#etoro-cancellation-panel');
  if(!panel){const anchor=document.querySelector('#reconcile-trades');if(!anchor)return;panel=document.createElement('section');panel.id='etoro-cancellation-panel';anchor.before(panel);}
  try{
    const data=await api('/api/etoro/cancellations');
    panel.innerHTML=`<h3>eToro · wartende Kaufaufträge und Stornierungen</h3><p>Ausgeführte Teilmengen bleiben erhalten. Eine Stornoanfrage ist erst nach dem Brokerabgleich abgeschlossen.</p>${data.available.map((r,i)=>`<p>${esc(r.symbol)} · ${r.paper?'DEMO':'LIVE'} · Order ${esc(r.order_id)} · ${esc(r.label)} <button class="secondary" data-etoro-cancel="${i}">Restauftrag stornieren</button></p>`).join('')}${data.requests.map(r=>`<p>Order ${esc(r.order_id)} · ${r.paper?'DEMO':'LIVE'} · <strong>${esc(r.label)}</strong></p>`).join('')}${!data.available.length&&!data.requests.length?'<p>Keine vorgemerkte Stornierung oder zugeordnete wartende Kauforder.</p>':''}`;
    panel.querySelectorAll('[data-etoro-cancel]').forEach(button=>button.onclick=async()=>{
      const r=data.available[Number(button.dataset.etoroCancel)];
      if(!confirm(`Verbleibenden Kaufauftrag ${r.order_id} bei eToro stornieren? Bereits ausgeführte Mengen bleiben bestehen.`))return;
      button.disabled=true;
      try{await api('/api/etoro/cancellations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(r)});await ladeEtoroStornos();}
      catch(err){button.disabled=false;alert(err.message);}
    });
  }catch(err){panel.textContent='Stornoabgleich derzeit nicht lesbar: '+err.message;}
}
if(typeof document.createElement==='function' && typeof api==='function'){
  nexusPoll(ladeEtoroStornos,30000);
}
