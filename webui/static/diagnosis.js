const diagnosisMessage=document.querySelector('#diagnosis-message');
const diagnosisPhases={START_EXPORT:'Startsammlung',OBSERVATION:'Beobachtung',END_EXPORT:'Endsammlung',ARCHIVING:'ZIP wird erstellt',ARCHIVE_READY:'ZIP fertig'};
const diagnosisTelegram={NOT_REQUESTED:'Nicht angefordert',PENDING:'Nach Fertigstellung',SENDING:'Versand läuft',SENT:'Versendet',FAILED:'Nicht gestartet',UNKNOWN:'Versand nicht bestätigt',TOO_LARGE:'ZIP zu groß für Telegram'};
async function startDiagnosis(mode){
 try{await api('/api/diagnosis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,telegram:document.querySelector('#diagnosis-telegram').checked})});diagnosisMessage.textContent='Diagnose gestartet.';await loadDiagnosis()}
 catch(e){diagnosisMessage.textContent=e.message}
}
async function loadDiagnosis(){
 try{
  const state=await api('/api/diagnosis');
  document.querySelector('#diagnosis-instant').disabled=state.busy;
  document.querySelector('#diagnosis-30min').disabled=state.busy;
  const toggle=document.querySelector('#diagnosis-telegram');toggle.disabled=!state.telegram_available||state.busy;if(!state.telegram_available)toggle.checked=false;
  document.querySelector('#diagnosis-telegram-info').textContent=state.telegram_available?'Versand an den in NEXUS eingerichteten Telegram-Chat. Die ZIP bleibt lokal gespeichert.':'Telegram ist ausgeschaltet oder noch nicht eingerichtet. Die lokale Diagnose ist verfügbar.';
  document.querySelector('#diagnosis-directory').textContent='Ablage: '+state.output_directory;
  document.querySelector('#diagnosis-jobs').innerHTML=(state.jobs||[]).map(row=>{
   const active=['STARTING','RUNNING','SENDING'].includes(row.status);
   let elapsed='';if(active&&row.phase==='OBSERVATION'&&row.requested_seconds){const n=Math.max(0,Math.min(row.requested_seconds,Date.now()/1000-row.observation_started_at));elapsed=` · ${Math.floor(n/60)} / 30 Minuten`;}
   const title=active?(row.status==='SENDING'?'Telegram-Versand':diagnosisPhases[row.phase]||row.status):({COMPLETED:'Abgeschlossen',FAILED:'Fehlgeschlagen',INTERRUPTED:'Unterbrochen'}[row.status]||row.status);
   const sendable=row.download_url&&state.telegram_available&&!state.busy&&['NOT_REQUESTED','FAILED'].includes(row.telegram_status);
   // 10.5.0: abgeschlossene Diagnose als aufbereiteter Bericht in neuem Fenster (aus der verifizierten ZIP).
   const reportLink=row.download_url&&/^[0-9a-f]{32}$/.test(String(row.id||''))?'<a class="button" href="/diagnosis/'+esc(row.id)+'/bericht" target="_blank" rel="noopener noreferrer">Bericht öffnen</a>':'';
   return `<article class="section"><h3>${row.mode==='instant'?'Sofortdiagnose':'30-Minuten-Diagnose'} · ${esc(title+elapsed)}</h3><p class="small">${esc(observationTime(row.created_at))}${row.completion?' · '+esc(row.completion==='COMPLETED'?'Vollständige Sammlung':'Teilbericht: '+row.completion):''}</p><p>${esc(row.detail||'')}</p><p class="small">Telegram: ${esc(diagnosisTelegram[row.telegram_status]||row.telegram_status)}</p><div class="toolbar">${reportLink}${row.download_url?'<a class="button secondary" href="'+esc(row.download_url)+'">ZIP herunterladen</a>':''}${sendable?'<button class="secondary" data-diagnosis-send="'+esc(row.id)+'">ZIP über Telegram senden</button>':''}</div></article>`;
  }).join('')||'<p>Noch keine Diagnose über diese WebUI gestartet.</p>';
  document.querySelectorAll('[data-diagnosis-send]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('/api/diagnosis/'+b.dataset.diagnosisSend+'/telegram',{method:'POST'});await loadDiagnosis()}catch(e){diagnosisMessage.textContent=e.message}});
 }catch(e){diagnosisMessage.textContent=e.message}
}
document.querySelector('#diagnosis-instant').onclick=()=>startDiagnosis('instant');
document.querySelector('#diagnosis-30min').onclick=()=>startDiagnosis('30min');
nexusPoll(loadDiagnosis,5000);
