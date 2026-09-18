/* Execution facts, not model reasoning or inferred provider availability. */
function aiExecutionView(answer={}, label='GPT', compact=false) {
  const execution=answer.execution;
  if(!execution)return `<div class="request-phase"><strong>${esc(label)}</strong> ${badge('Ausführungsbeleg fehlt','warn')}<p class="small">${answer.ok===false?'Ein Fehler ist gespeichert; ob ein Request gestartet wurde, ist in diesem Altbestand nicht belegt.':'Ein gespeichertes Ergebnis allein belegt keinen neuen GPT-Aufruf.'}</p></div>`;
  const phase=String(execution.phase||'UNKNOWN').toUpperCase();
  const labels={NOT_STARTED:'Nicht gestartet',CACHE_HIT:'Cache verwendet',REQUEST_STARTED:'Request gestartet',SUCCEEDED:'Antwort empfangen',TIMED_OUT:'Zeitlimit erreicht',FAILED:'Fehlgeschlagen',DISCARDED:'Ergebnis verworfen',UNKNOWN:'Phase unbekannt'};
  const dispatched=execution.request_dispatched===true?'Request tatsächlich gestartet':execution.request_dispatched===false?'Kein neuer Request gestartet':'Requeststart nicht belegt';
  const model=answer.model||answer.modell||execution.model||'nicht belegt';
  const kind=phase==='SUCCEEDED'?'ok':['FAILED','TIMED_OUT'].includes(phase)?'bad':'warn';
  return `<div class="request-phase"><strong>${esc(label)}</strong> ${badge(labels[phase]||phase,kind)}<p class="small">${esc(dispatched)} · Modell ${esc(model)}${execution.cache_used===true?' · gespeichertes Ergebnis':''}${execution.error_code?' · '+esc(execution.error_code):''}</p>${compact?'':`<p class="small">Start: ${esc(observationTime(execution.started_at))} · Ende: ${esc(observationTime(execution.finished_at))}<br>Dauer: ${knownNumber(execution.duration_seconds)?esc(Number(execution.duration_seconds).toFixed(2))+' s':'nicht belegt'}<br>Lokale Request-ID: ${esc(execution.local_request_id||'nicht belegt')}${execution.provider_request_id?'<br>Provider-ID: '+esc(execution.provider_request_id):''}</p>${aiInputSourcesView(answer)}`}</div>`;
}

function renderAIDiagnostics(result) {
  if(result.complete===false||result.error)return `<p class="alert danger">GPT-Diagnose nicht vollständig lesbar: ${esc(result.error||'Belege fehlen')}</p>`;
  const records=Array.isArray(result.records)?result.records:[];
  const summary=result.summary||{};
  return `<p class="snapshot-note">Gespeicherte Request-Metadaten · ${esc(observationTime(result.as_of))}. Diese Anzeige führt keinen GPT-Aufruf aus. Mehrere Kandidaten mit derselben Request-ID gehören zu einem gemeinsamen Aufruf.</p>${summary.detail?'<p>'+esc(summary.detail)+'</p>':''}${records.length?records.slice(0,30).map(row=>aiExecutionView({model:row.model,execution:row.execution},[row.symbol,row.task].filter(Boolean).join(' · ')||'GPT-Aufgabe')).join(''):'<p>Keine Requestbelege in diesem Ausschnitt. Provider-Erreichbarkeit ist dadurch nicht bestätigt.</p>'}`;
}

let aiDiagnosticsInFlight=false;
async function loadAIDiagnostics() {
  const target=document.querySelector('#ai-request-diagnostics');if(!target||aiDiagnosticsInFlight)return;
  aiDiagnosticsInFlight=true;
  try {target.innerHTML=renderAIDiagnostics(await api('/api/ai-diagnostics'));}
  catch(error) {target.innerHTML=`<p class="alert danger">GPT-Diagnose nicht aktuell: ${esc(error.message)}</p>`;}
  finally {aiDiagnosticsInFlight=false;}
}

const aiDiagnosticsPanel=document.querySelector('#ai-diagnostics-panel');
aiDiagnosticsPanel?.addEventListener('toggle',()=>{if(aiDiagnosticsPanel.open)loadAIDiagnostics();});
document.querySelector('#ai-diagnostics-refresh')?.addEventListener('click',loadAIDiagnostics);
nexusPoll(loadAIDiagnostics,60000,{immediate:false,when:()=>Boolean(aiDiagnosticsPanel?.open)});

function aiInputSourcesView(answer={}) {
  const packets=Object.entries(answer.input_sources||{}).slice(0,5);
  if(!packets.length)return '<p class="small">Für diesen älteren Aufruf fehlt ein separater Beleg der tatsächlich übergebenen Datenfelder.</p>';
  return '<details class="ai-input-sources"><summary>Tatsächlich übergebene Daten</summary><p class="small">Diese Belege zeigen den Inhalt der GPT-Eingabe einer validierten Antwort; sie beweisen keine bestimmte Gewichtung durch das Modell.</p>'+packets.map(([symbol,packet])=>'<h4>'+esc(symbol)+'</h4><p class="small">Paketversion '+esc(packet.view_revision||'unbekannt')+' · '+esc(packet.status||'nicht belegt')+'</p>'+((packet.sources||[]).slice(0,30).map(source=>'<p class="small"><strong>'+esc(source.source_id||'Beleg-ID fehlt')+'</strong> · '+esc(source.provider||'Quelle unbekannt')+' · '+esc(source.kind||'Datenart unbekannt')+'<br>'+(source.included===true?'Übergeben':source.included===false?'Nicht übergeben':'Übergabe unbekannt')+(source.truncated===true?' · gekürzt':'')+' · Daten bis '+esc(source.as_of||'nicht belegt')+(knownNumber(source.included_rows)?' · '+esc(source.included_rows)+' Datenzeilen':'')+'</p>').join(''))).join('')+'</details>';
}
