let daten = {};
const underdogsPage = location.pathname.replace(/\/$/, '') === '/underdogs';
if (underdogsPage) {
  document.title = document.title.replace('Universum','Underdogs');
  document.querySelector('#universe-title').textContent = 'Underdogs';
  document.querySelector('#universe-description').textContent = 'Kuratierte Aktienkandidaten mit ihrem tatsächlichen Universumsstatus. Die Kennzeichnung erteilt keine Kaufzulassung.';
  document.querySelector('#f-art').value = 'underdog';
  document.querySelector('#f-art').disabled = true;
  document.querySelector('#crypto-universum').hidden = true;
  document.querySelector('#okx-section').hidden = true;
}

function stundenText(h) {
  if (h === null || h === undefined) return '–';
  if (h < 24) return `${h.toFixed(0)} h`;
  return `${(h / 24).toFixed(1)} Tage`;
}

function zustandsFarbe(z) {
  if (z === 'AKTIV') return 'ok';
  if (z === 'ABGANG') return 'warn';
  return '';
}

function merkmale(w) {
  const teile = [];
  if (w.underdog) teile.push('<span class="status">UNDERDOG</span>');
  if (w.kern) teile.push('<span class="status ok">KERN</span>');
  if (w.gepinnt) teile.push('<span class="status warn">POSITION</span>');
  if (w.favorit) teile.push('<span class="status">FAVORIT</span>');
  if (w.focus) teile.push(`<span class="status">FOCUS ${w.focus_platz || ''}</span>`);
  return teile.join(' ') || '–';
}

function filtern(werte) {
  const suche = (document.querySelector('#f-suche').value || '').trim().toUpperCase();
  const zustand = document.querySelector('#f-zustand').value;
  const art = document.querySelector('#f-art').value;
  let out = werte.filter(w => {
    if (suche && !String(w.symbol || '').toUpperCase().includes(suche)) return false;
    if (zustand && w.zustand !== zustand) return false;
    if (art === 'kern' && !w.kern) return false;
    if (art === 'focus' && !w.focus) return false;
    if (art === 'favorit' && !w.favorit) return false;
    if (art === 'gepinnt' && !w.gepinnt) return false;
    if (art === 'ki' && !w.ki) return false;
    if ((underdogsPage || art === 'underdog') && !w.underdog) return false;
    return true;
  });
  const sortierung = document.querySelector('#f-sortierung').value;
  const nachRang = (a, b) => (a.rang || 9999) - (b.rang || 9999);
  if (sortierung === 'score') out.sort((a, b) => (b.score || 0) - (a.score || 0));
  else if (sortierung === 'symbol') out.sort((a, b) => String(a.symbol).localeCompare(String(b.symbol)));
  else if (sortierung === 'neu') out.sort((a, b) => (a.alter_stunden ?? 1e9) - (b.alter_stunden ?? 1e9));
  else if (sortierung === 'alt') out.sort((a, b) => (b.alter_stunden ?? -1) - (a.alter_stunden ?? -1));
  else out.sort(nachRang);
  return out;
}

function tabelle(werte) {
  if (!werte.length) return '<p class="small">Keine Werte, die zum Filter passen.</p>';
  const zeilen = werte.map(w => `<tr>
    <td><strong>${esc(w.symbol)}</strong>${w.fmp_context&&Object.keys(w.fmp_context).length?fmpContextView(w.fmp_context):''}</td>
    <td>${w.kaufblock_grund ? badge('Käufe gesperrt', 'danger') : badge(w.zustand || '?', zustandsFarbe(w.zustand))}</td>
    <td>${merkmale(w)}</td>
    <td>${w.rang || '–'}${w.bester_rang ? ` <span class="small">(best ${w.bester_rang})</span>` : ''}</td>
    <td>${w.score==null?'unbekannt':Number(w.score).toFixed(3)}</td>
    <td>${stundenText(w.alter_stunden)}</td>
    <td>${stundenText(w.stunden_im_zustand)}</td>
    <td>${esc(w.ki || '–')}${w.ki_modell ? `<br><span class="small">${esc(w.ki_modell)}</span>` : ''}</td>
    <td class="small">${esc(w.kaufblock_grund || w.aufnahmegrund || w.abganggrund || '–')}<br><span class="small">Letzte Prüfung: ${esc(observationTime(w.letzte_pruefung))}</span></td>
  </tr>`).join('');
  return `<table><thead><tr>
    <th>Symbol</th><th>Zustand</th><th>Merkmale</th><th>Rang</th><th>Score</th>
    <th>Aufgenommen vor</th><th>Im Zustand seit</th><th>KI-Urteil</th><th>Grund</th>
  </tr></thead><tbody>${zeilen}</tbody></table>`;
}

function zeichne() {
  const broker = daten.broker || {};
  for (const [key, prefix] of [['okx', 'okx'], ['etoro', 'etoro']]) {
    const d = broker[key] || {};
    const alle = [...(d.werte || []), ...(key === 'etoro' ? d.katalog_kandidaten || [] : [])];
    const gefiltert = filtern(alle);
    document.querySelector(`#${prefix}-tabelle`).innerHTML = d.fehler
      ? `<div class="alert danger">${esc(d.fehler)}</div>`
      : tabelle(gefiltert);
    const u = d.uebersicht || {};
    const regel = d.autonome_aufnahme
      ? 'Aufnahmen und Entfernungen erfolgen einmal täglich. Bewertungen und Sicherheitsprüfungen laufen weiter.'
      : 'Aufnahmen brauchen deine Bestätigung per Telegram. Sicherheitsabgänge passieren sofort.';
    document.querySelector(`#${prefix}-info`).innerHTML =
      `${gefiltert.length} von ${alle.length} angezeigt · Limit ${u.aktiv_limit || '?'} · `
      + `Focus ${u.focus_limit || '?'} · handelbar ${u.handelbar || 0} · `
      + `Beobachtung ${u.beobachtung || 0} · gepinnt ${u.gepinnt || 0}<br>${esc(regel)}`;
  }

  // v9.2: Eine Kachel je Handelsseite, alle Zahlen aus der API.
  // Vorher stand hier eine gemeinsame Kachel "Fester Kern" mit 75 Aktien und
  // 20 Kryptowerten zu einer Zahl addiert -- zwei getrennte Universen in
  // einem Wert. Dazu kam bei eToro der Hinweis "Aufnahme nur mit
  // Telegram-Freigabe", der seit 8.1.5 nicht mehr stimmt: Aktien werden
  // autonom aufgenommen, mit vier Stunden Bewaehrung.
  const okxB = broker.okx || {}, etoroB = broker.etoro || {};
  const okx = okxB.uebersicht || {}, etoro = etoroB.uebersicht || {};
  const kernKrypto = daten.kernwerte_krypto || daten.kernwerte || [];
  const kernAktien = daten.kernwerte_aktien || [];
  const dynamic = daten.dynamic_30 || {};
  const dynamicItems = dynamic.items || [];

  const zahl = (v, ersatz = '–') => (v === undefined || v === null ? ersatz : v);

  function aufnahmetext(seite) {
    if (!seite.autonome_aufnahme) return 'Aufnahme nur nach Freigabe';
    const h = Number(seite.beobachtung_stunden || 0);
    const w = Number(seite.max_aenderungen_pro_lauf || 0);
    return `Autonome Aufnahme · ${h % 1 ? h.toFixed(1) : h} h Bewährung`
         + (w ? ` · max. ${w} Wechsel je Lauf` : '');
  }

  function seitenKachel(titel, u, seite, kernListe) {
    const kern = Number(u.kern_limit || kernListe.length || 0);
    const dyn = Number(u.dynamisch_limit || 0);
    const aktivLimit = Number(u.aktiv_limit || (kern + dyn) || 0);
    return `<article class="card"><h3>${esc(titel)}</h3>
      <div class="metric">${zahl(u.handelbar, 0)}</div>
      <p>handelbar von max. ${aktivLimit}</p>
      <div class="small">Kern ${zahl(u.kern, kernListe.length)}/${kern}
        · dynamisch ${zahl(u.dynamisch, 0)}/${dyn}
        · in Bewährung ${zahl(u.beobachtung, 0)}${underdogZeile(u)}<br>${esc(aufnahmetext(seite))}</div></article>`;
  }

  // v9.3: Underdogs getrennt ausweisen. Bis 9.2 stand der Schalter auf
  // "aktiv", 25 Kandidaten lagen im Katalog -- und keiner war handelbar.
  // Sichtbar war das nirgends.
  function underdogZeile(u) {
    if (u.underdogs_im_katalog === undefined) return '';
    if (!u.underdogs_eingeschaltet) return '<br>Underdogs: ausgeschaltet';
    const blockiert = Number(u.underdogs_blockiert || 0);
    return `<br>Underdogs ${zahl(u.underdogs_aktiv, 0)} aktiv`
      + ` · ${zahl(u.underdogs_im_kern, 0)}/${zahl(u.underdogs_kernplaetze, 0)} Kernplätze`
      + ` · ${zahl(u.underdogs_im_katalog, 0)} im Katalog`
      + (blockiert ? ` · ${blockiert} aktuell gesperrt` : '');
  }

  document.querySelector('#kennzahlen').innerHTML =
    (underdogsPage ? '' : seitenKachel('OKX Krypto', okx, okxB, kernKrypto))
    + seitenKachel('eToro Aktien', etoro, etoroB, kernAktien)
    + (underdogsPage ? '' : `<article class="card"><h3>OKX · dynamische Kryptos</h3>
        <div class="metric">${dynamicItems.length}</div>
        <p>von ${Number(okx.dynamisch_limit || 0)} monatlich bewerteten Werten</p>
        <div class="small">${esc(dynamic.status || 'EMPTY')} · nächste Bewertung ${esc(dynamic.next_due_utc || '–')}</div></article>
      <article class="card"><h3>Beide Broker · gepinnte Werte</h3>
        <div class="metric">eToro ${etoro.gepinnt || 0} · OKX ${okx.gepinnt || 0}</div>
        <p>bleiben immer im Monitoring</p>
        <div class="small">Auch wenn sie aus dem Entry-Universe fallen</div></article>`);

  const coverage = dynamic.coverage || {}, changes = dynamic.changes || {};
  const items = dynamicItems;
  const statusClass = dynamic.status === 'CURRENT' ? 'ok' : 'warn';
  const rows = items.map(x => `<tr><td>${x.rank || '–'}</td><td><strong>${esc(x.base || '?')}</strong></td>`
    + `<td>${esc(x.pair || '–')}</td><td>${esc(x.quote || '–')}</td>`
    + `<td>${x.volume_30d_median_eur !== undefined ? Number(x.volume_30d_median_eur).toLocaleString('de-DE', {maximumFractionDigits:0}) : Number(x.volume_24h_normalized_eur || 0).toLocaleString('de-DE', {maximumFractionDigits:0})}</td>`
    + `<td class="small">${esc(x.measured_at_utc || '–')}</td></tr>`).join('');
  const kernListe = kernKrypto.join(', ') || 'noch nicht geladen';
  document.querySelector('#crypto-universum').innerHTML = `<h2>OKX · Krypto-Universum ${Number(okx.kern_limit || 0)} + ${Number(okx.dynamisch_limit || 0)} ${badge(dynamic.status || 'EMPTY', statusClass)}</h2>`
    + `<p class="small"><strong>Fester Kern (${kernKrypto.length}/${Number(okx.kern_limit || 0)}):</strong> ${esc(kernListe)}. Die Mitgliedschaft bleibt; aktuelle Sicherheitsfilter können einzelne Werte sperren.</p>`
    + `<p class="small"><strong>Dynamisch (${items.length}/${Number(okx.dynamisch_limit || 0)}):</strong> Quelle: ${esc(dynamic.source || 'OKX official public SPOT tickers')} · `
    + `Auswahl: ${esc(dynamic.selection_method || 'aktuelles normalisiertes 24h-Quote-Notional, danach 30-Tage-Median')} · `
    + `Abdeckung: ${coverage.days || 0}/${coverage.required_days || 20} Tage · Letzter Erfolg: ${esc(dynamic.last_success_utc || '–')} · `
    + `Nächste Fälligkeit: ${esc(dynamic.next_due_utc || '–')}</p>`
    + `<p class="small">Hinzu: ${esc((changes.added || []).join(', ') || 'keine')} · Entfernt: ${esc((changes.removed || []).join(', ') || 'keine')} · `
    + `Unverändert: ${(changes.unchanged || []).length}${dynamic.error ? ` · Hinweis: ${esc(dynamic.error)}` : ''}</p>`
    + (rows ? `<div class="table-wrap"><table><thead><tr><th>Rang</th><th>Basis</th><th>Paar</th><th>Quote</th><th>Volumen EUR</th><th>Messzeit</th></tr></thead><tbody>${rows}</tbody></table></div>`
            : `<p class="small">Noch keine verifizierte dynamische Startliste. Die ${kernKrypto.length} Kernwerte bleiben sichtbar; jeder Kauf braucht trotzdem vollständige und aktuelle Marktdaten.</p>`);
}

async function ladeUniversum() {
  try {
    daten = await api('/api/universe');
    if (daten.fehler) {
      document.querySelector('#kennzahlen').innerHTML = `<div class="alert danger">${esc(daten.fehler)}</div>`;
      return;
    }
    zeichne();
  } catch (e) {
    document.querySelector('#kennzahlen').innerHTML = `<div class="alert danger">${esc(e.message)}</div>`;
  }
}

ladeUniversum();
