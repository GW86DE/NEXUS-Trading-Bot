/* Read-only order evidence. Identity always includes broker, account and mode. */
function executionIdentity(row) {
  const broker=String(row.broker||'').toLowerCase(), environment=String(row.environment||'').toUpperCase();
  if(!['okx','etoro'].includes(broker)||!['DEMO','LIVE'].includes(environment)||!row.account||!row.client_id)return null;
  return new URLSearchParams({broker,account:String(row.account),environment,client_id:String(row.client_id)});
}

function executionFacts(order={}) {
  const closedUnproven=order.state==='POSITION_CLOSED_ORDER_UNPROVEN';
  const fact=(value,yes,no)=>value===true||value===1?yes:value===false||value===0?no:'nicht belegt';
  return `${closedUnproven?'<p class="alert warn">Die Position ist geschlossen. Der Abschluss beweist keine Ausführung dieses alten Auftrags. Dieser Klärungsstand ist kein offener Bestand und verursacht keine Kaufsperre. Kostenbelege werden beim tatsächlich ausgeführten Verkauf geprüft.</p>':''}<dl class="evidence-grid"><dt>Auftragsabschluss</dt><dd>${esc(closedUnproven?'Ausgang dieses Auftrags ungeklärt':fact(order.terminal,'gemeldet','noch offen'))}</dd><dt>Ausführungsbelege</dt><dd>${esc(fact(order.evidence_complete,'vollständig','noch unvollständig'))}</dd><dt>Mengenbuchung</dt><dd>${esc(fact(order.accounted,'abgeschlossen','noch offen'))}</dd><dt>Gebührenbelege</dt><dd>${esc(({KNOWN:'Belegt',INCOMPLETE:'Noch unvollständig',UNKNOWN:'Nicht belegt'})[order.fee_evidence?.status]||'Nicht separat geprüft')}<p class="small">${esc(order.fee_evidence?.detail||'Mengenbuchung und Gebührenvollständigkeit sind getrennte Prüfungen.')}</p></dd></dl>`;
}

function executionCard(row,index) {
  const action=({BUY:'Kauf',SELL:'Verkauf'})[String(row.side).toUpperCase()]||'Auftragsseite unbekannt';
  return `<article class="panel"><div class="section-head"><strong>${esc(row.instrument||'Instrument unbekannt')} · ${action}</strong>${badge(executionLabel(row.state),row.accounted&&row.evidence_complete?'ok':'warn')}</div>${contextBadge({broker:row.broker,account_fingerprint:row.account,broker_environment:row.environment})}<p>Netto ausgeführt ${esc(knownNumber(row.net_filled)?row.net_filled:'unbekannt')} · beauftragt ${esc(knownNumber(row.requested)?row.requested:'unbekannt')}</p>${executionFacts(row)}<p class="small">${esc(row.check_detail||'Abgleich erfolgt durch den Handelskern.')}<br>Letzte Prüfung: ${esc(observationTime(row.last_checked_at))}</p><button class="secondary execution-open" data-execution-index="${index}" ${executionIdentity(row)?'':'disabled'}>Auftragsdetails & Belege</button></article>`;
}

function bindExecutionDetails(target,rows) {
  target?.querySelectorAll('.execution-open').forEach(button=>button.addEventListener('click',()=>openExecutionDetail(rows[Number(button.dataset.executionIndex)])));
}

function executionDetailHTML(data) {
  const order=data.order||{}, fills=Array.isArray(data.fills)?data.fills:[], events=Array.isArray(data.events)?data.events:[];
  const truncated=data.truncated===true||Object.values(data.truncated||{}).some(value=>value===true);
  const fields=[['Instrument',order.instrument],['Auftragsseite',order.side],['Broker Order-ID',order.order_id],['Client Order-ID',order.client_id],['Positions-ID',order.position_id],['NEXUS Trade-ID',order.trade_id],['Beauftragt',order.requested],['Brutto ausgeführt',order.filled],['Netto ausgeführt',order.net_filled],['Erstellt',observationTime(order.created_at)],['Letzte Änderung',observationTime(order.updated_at)]];
  return `${contextBadge({broker:order.broker,account_fingerprint:order.account,broker_environment:order.environment})}<p>${badge(executionLabel(order.state))}</p>${executionFacts(order)}<dl class="evidence-grid">${fields.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value===null||value===undefined||value===''?'nicht belegt':value)}</dd>`).join('')}</dl><h3>Native Ausführungsbelege</h3>${fills.length?`<div class="table-wrap evidence-table" tabindex="0" role="region" aria-label="Ausführungsbelege, seitlich scrollbar"><table><thead><tr><th>Fill-ID</th><th>Menge</th><th>Preis</th><th>Gebühr</th><th>Währung</th></tr></thead><tbody>${fills.map(fill=>`<tr><td>${esc(fill.fill_id||'nicht belegt')}</td><td>${esc(knownNumber(fill.quantity)?fill.quantity:'unbekannt')}</td><td>${esc(knownNumber(fill.price)?fill.price:'unbekannt')}</td><td>${esc(knownNumber(fill.fee)?fill.fee:'unbekannt')}</td><td>${esc(fill.fee_currency||'unbekannt')}</td></tr>`).join('')}</tbody></table></div>`:'<p>Keine nativen Ausführungsbelege in diesem gespeicherten Ausschnitt. Das beweist keine Nichtausführung.</p>'}<h3>Gespeicherter Verlauf</h3>${events.length?`<ol class="evidence-timeline">${events.map(event=>`<li><strong>${esc(event.kind||event.event_type||'Ereignis')}</strong> · ${esc(observationTime(event.created_at))}${event.detail?'<p class="small">'+esc(typeof event.detail==='string'?event.detail:JSON.stringify(event.detail))+'</p>':''}</li>`).join('')}</ol>`:'<p>Kein Ereignisverlauf in diesem Ausschnitt vorhanden.</p>'}<p class="snapshot-note">Begrenzter, gespeicherter Ausschnitt${data.as_of?' · Abruf '+esc(observationTime(data.as_of)):''}. Die Ansicht sendet keine Brokerabfrage und verändert keinen Auftrag.${truncated?' Weitere Belege sind gespeichert; die Anzeige ist begrenzt.':''}</p>`;
}

async function openExecutionDetail(row) {
  const query=executionIdentity(row);if(!query)return;
  document.querySelector('#execution-detail-dialog')?.remove();
  const dialog=document.createElement('dialog');dialog.id='execution-detail-dialog';dialog.className='order-dialog execution-detail-dialog';
  dialog.setAttribute('aria-labelledby','execution-detail-title');
  dialog.innerHTML='<div class="section-head"><h2 id="execution-detail-title">Auftragsdetails</h2><button class="secondary execution-close" aria-label="Auftragsdetails schließen">Schließen</button></div><div class="execution-detail-content" aria-live="polite">Belege werden geladen …</div>';
  dialog.querySelector('.execution-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
  try {
    const result=await api('/api/executions/detail?'+query);
    if(!dialog.isConnected)return;
    // Do not silently show an answer for another account/order after a mismatch.
    if(result.complete===false||result.error)throw new Error(result.error||'Die Belegquelle konnte nicht vollständig gelesen werden.');
    if(!result.order||executionIdentity(result.order)?.toString()!==query.toString())throw new Error('Der Beleg passt nicht zum ausgewählten Kontokontext.');
    dialog.querySelector('.execution-detail-content').innerHTML=executionDetailHTML(result);
  } catch(error) {if(dialog.isConnected)dialog.querySelector('.execution-detail-content').innerHTML=`<p class="alert danger">Belegansicht nicht verfügbar: ${esc(error.message)}</p>`;}
}

let executionHistoryRequest=0;
async function loadExecutionHistory(page=1) {
  const target=document.querySelector('#execution-history');if(!target)return;
  const request=++executionHistoryRequest;
  target.textContent='Auftragsverlauf wird geladen …';
  try {
    const query=new URLSearchParams({broker:document.querySelector('#trade-broker')?.value||'',page:String(page),limit:'20'});
    const result=await api('/api/executions/history?'+query);if(request!==executionHistoryRequest)return;
    if(result.complete===false||result.error)throw new Error(result.error||'Die Auftragsquelle konnte nicht vollständig gelesen werden.');
    if(!Array.isArray(result.orders))throw new Error('Auftragsliste fehlt in der Antwort.');
    const rows=result.orders;
    target.innerHTML=`<p class="snapshot-note">${esc(result.total??rows.length)} gespeicherte Aufträge · Seite ${esc(result.page||page)} / ${esc(result.pages||1)} · höchstens 20 Aufträge pro Seite. Kein vollständiger Brokerkontoauszug.</p>${rows.length?'<div class="execution-list">'+rows.map(executionCard).join('')+'</div>':'<p>Keine gespeicherten Aufträge in dieser Auswahl. Das ist kein positiver Brokerabgleich.</p>'}<div class="pager"><button class="secondary execution-history-prev" ${page<=1?'disabled':''}>Zurück</button><button class="secondary execution-history-next" ${page>=(result.pages||1)?'disabled':''}>Weiter</button></div>`;
    bindExecutionDetails(target,rows);
    target.querySelector('.execution-history-prev').onclick=()=>loadExecutionHistory(page-1);
    target.querySelector('.execution-history-next').onclick=()=>loadExecutionHistory(page+1);
  } catch(error) {if(request===executionHistoryRequest)target.innerHTML=`<p class="alert danger">Verlauf nicht abrufbar: ${esc(error.message)}</p>`;}
}
