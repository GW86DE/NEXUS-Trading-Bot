/* Provider receipts are read from NEXUS. Refresh never calls X or GPT. */
let sourceSettingsLoaded=false;
const sourceCount=value=>knownNumber(value)?esc(value):'unbekannt';
const sourceMoney=value=>knownNumber(value)?Number(value).toFixed(3)+' EUR':'unbekannt';
function sourceState(value) {
  const names={OK:'Letzter Abruf erfolgreich',READY:'Bereit für nächsten Abruf',DISABLED:'Deaktiviert',NOT_CONFIGURED:'Zugang fehlt',NOT_OBSERVED:'Noch kein Abrufbeleg',UNKNOWN:'Nicht belegt',STALE:'Stand veraltet',ERROR:'Abruf fehlgeschlagen',PAUSED:'Abrufpause',BUDGET_EXHAUSTED:'Monatsbudget erreicht',BUDGET_BLOCKED:'Budgetgrenze',WAITING:'Wartet auf Zeitplan',WAITING_FIRST_REQUEST:'Wartet auf ersten Abruf',PRICING_EXPIRED:'Preisprüfung erneuern',RATE_LIMITED:'Anbieterlimit',PRICING_UNACKNOWLEDGED:'Preisgrundlage bestätigen',SHADOW_RESEARCH:'Recherchebetrieb'};
  return badge(names[String(value).toUpperCase()]||value||'Nicht belegt',['OK','READY'].includes(String(value).toUpperCase())?'ok':['ERROR','BUDGET_EXHAUSTED'].includes(String(value).toUpperCase())?'bad':'warn');
}
function renderXStatus(x={}) {
  const c=x.counts||{},b=x.budget||{};
  return `${sourceState(x.state)}<p>${esc(x.detail||'Noch kein gespeicherter X-Sammlerbeleg.')}</p><p class="small">Letzter Versuch: ${esc(observationTime(x.last_attempt))} · Letzter API-Erfolg: ${esc(observationTime(x.last_success))}<br>Sammler-Rückmeldung: ${esc(observationTime(x.worker_heartbeat))} · Gespeicherter Zustand: ${esc(observationTime(x.observed_at))}</p>
    <div class="table-wrap"><table><thead><tr><th>API-Aufrufe</th><th>Erfolgreich</th><th>Fehlgeschlagen</th><th>Posts empfangen</th><th>Eindeutig gespeichert</th><th>Duplikate</th><th>Verarbeitet</th><th>Ereignisse</th><th>Recherche-Lesevorgänge</th></tr></thead><tbody><tr>${['requests','successful_requests','failed_requests','posts_received','unique_posts','duplicates','processed_posts','events','consumer_reads'].map(key=>'<td>'+sourceCount(c[key])+'</td>').join('')}</tr></tbody></table></div>
    <p><strong>Lokales Monatsbudget ${esc(b.month||'nicht belegt')}</strong><br>Kostenobergrenze verbraucht: ${sourceMoney(b.charged_upper_eur)} · Noch reserviert: ${sourceMoney(b.reserved_eur)} · Verfügbar: ${sourceMoney(b.remaining_eur)} · Limit: ${sourceMoney(b.limit_eur)}</p><p class="small">API-Erfolg, verarbeiteter Post, Rechercheübergabe und bestätigtes Ereignis sind verschiedene Nachweise. Ein Lesevorgang beweist keine Handelswirkung. API-, Empfangs- und Verarbeitungszähler gelten für den angegebenen UTC-Monat; eindeutig gespeicherte Posts nur für die Aufbewahrungsfrist von sieben Tagen. Diese Zähler sind lokale Belege; die Anbieterrechnung kann abweichen.</p>`;
}
function renderSourcePipeline(s={}) {
  const rows=Array.isArray(s.sources)?s.sources:[];
  return `<p class="small">${esc(s.meaning||'Noch keine getrennten Belege verfügbar.')}</p><div class="table-wrap"><table><thead><tr><th>Quelle</th><th>Letzter Abruf</th><th>Letzter Erfolg</th><th>Empfangene Elemente</th><th>Karten mit Quelle</th><th>Fehlerstand</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.provider)}${row.platform==='REDDIT_AGGREGATOR'?'<br><span class="small">Reddit-Aggregator</span>':''}</td><td>${sourceState(row.state)}</td><td>${esc(observationTime(row.last_success))}</td><td>${sourceCount(row.returned_items_last_request)}${knownNumber(row.aggregate_rows_in_cache)?'<br><span class="small">'+sourceCount(row.aggregate_rows_in_cache)+' aggregierte Cache-Zeilen</span>':''}</td><td>${sourceCount(row.cards_with_source)}</td><td>${esc(({HISTORICAL:'Historisch',CURRENT:'Aktuell',NONE:'Keiner belegt',UNVERIFIED:'Aktualität ungeprüft'})[row.error_scope]||'Unbekannt')}${row.failure_kind?'<br>'+esc(row.failure_kind):''}</td></tr>`).join('')}</tbody></table></div><p class="small">Reddit: ApeWisdom und Tradestie liefern Toplisten mit überlappenden Foren. Keine direkte Reddit-API und keine vollständigen Posts oder unabhängigen Autoren belegt. Unterschiedliche Instrumente im letzten erfassten Toplistenstand: ${sourceCount(s.reddit?.symbols_in_last_coverage)}. Ein nicht gelistetes Symbol bedeutet nicht null Erwähnungen.</p><p class="small">Kartenstand: ${esc(observationTime(s.card_cache_saved))} · Belegabfrage: ${esc(observationTime(s.as_of))}</p>`;
}
function renderNewsRiskEvidence(r={}) {
  if(!r.status)return '<p>Noch kein gespeicherter Beleg für die Nachrichten-Risikoprüfung.</p>';
  const state={MULTISOURCE_RISK_HINT:'Risikohinweis aus mehreren Ursprüngen',UNCONFIRMED_RISK_HINT:'Einzelhinweis · Gegenprüfung fehlt',NO_RISK_HINT:'Kein Risikohinweis in diesem Ausschnitt'};
  return '<p><strong>'+esc(state[r.status]||r.status)+'</strong></p><p class="small">Stand: '+esc(observationTime(r.observed_at||r.assessed_at))+' · Bestätigtes Ereignis: '+(r.verified_event===true?'ja':'nein')+'<br>Ungeprüfter Krisenwert: '+sourceCount(r.raw_crisis_score)+' · Durch mehrere Ursprünge gestützter Krisenwert: '+sourceCount(r.actionable_crisis_score)+' · Beteiligte Ursprünge: '+sourceCount(r.origin_count)+'</p><p>'+esc(r.detail||'Gleiche Themen sind noch kein Beweis für dasselbe Ereignis.')+'</p>'+((r.categories||[]).length?'<p class="small">'+r.categories.slice(0,20).map(c=>esc(c.category)+': '+esc((c.origins||[]).join(', '))+(c.multiple_origins?' · mehrere Ursprünge':' · Gegenprüfung fehlt')).join('<br>')+'</p>':'')+'<p class="small">Ein Risikohinweis ist keine Verkaufsanweisung. X kann diese Prüfung allein nicht erfüllen. Veraltete oder kopierte Meldungen und reine Anbieternamen zählen nicht als weitere Bestätigung.</p>';
}
function renderXAttention(rows=[]) {
  if(!Array.isArray(rows)||!rows.length)return '<p>Noch keine X-Aufmerksamkeitsmessung vorhanden.</p>';
  return '<div class="table-wrap"><table><thead><tr><th>Wert</th><th>Messzustand</th><th>Gemessene Posts</th><th>Vergleichsbasis</th><th>Normierung</th></tr></thead><tbody>'+rows.slice(0,24).map(row=>'<tr><td>'+esc(row.symbol||'unbekannt')+'</td><td>'+esc(row.coverage_status||row.state||'nicht belegt')+(row.coverage_detail?'<br><span class="small">'+esc(row.coverage_detail)+'</span>':'')+'</td><td>'+sourceCount(row.count??row.post_count??row.total_tweet_count)+'<br><span class="small">'+esc(row.day||'Messdatum fehlt')+'</span></td><td>'+esc(row.baseline_status||((row.complete_days??0)+' beobachtete Tage · '+(row.baseline_ready?'28-Tage-Basis vorhanden':'Vergleichsbasis wird aufgebaut')))+'</td><td>'+esc(row.normalization_status||'Noch kein Normierungsbeleg')+(knownNumber(row.normalized_ratio)?'<br>'+sourceCount(row.normalized_ratio)+'×':'')+'<br><span class="small">'+sourceCount(row.normalized_comparison_days)+' passende Vergleichstage</span></td></tr>').join('')+'</tbody></table></div>';
}
function renderXDiscovery(x={}, pipeline={}) {
  const d=x.discovery||{},rows=Array.isArray(d.candidates)?d.candidates:[],p=pipeline.source_pipeline||pipeline||{};
  
  return '<p>'+esc(d.detail||'Aktienhinweise werden automatisch aus den begrenzten X-Suchläufen gesammelt. Noch kein gespeicherter Suchbeleg.')+'</p><p class="small">Gefundene Kandidaten: '+sourceCount(d.candidate_count)+' · Weiterverarbeitung siehe PULSAR. Ein Fund ist keine Prognose für einen Kursanstieg.</p>'+(rows.length?'<div class="table-wrap"><table><thead><tr><th>Gefundener Ticker</th><th>Posts in der Stichprobe</th><th>Hinweisart</th><th>Beobachtet</th><th>Prüfstatus</th></tr></thead><tbody>'+rows.slice(0,24).map(r=>'<tr><td>'+esc(r.symbol)+'</td><td>'+sourceCount(r.sampled_post_count)+'</td><td>'+esc((r.topics||[]).join(', ')||'Aktienhinweis')+'</td><td>'+esc(observationTime(r.observed_at))+'</td><td>Unternehmen und Anlass zu prüfen</td></tr>').join('')+'</tbody></table></div>':'<p>Noch keine frischen Kandidaten gespeichert. Das beweist keine Abwesenheit interessanter Aktien.</p>')+(p.observed_at?'<p class="small">Letzte PULSAR-Verarbeitung: '+esc(observationTime(p.research_at||p.observed_at))+' · '+esc(pipeline.selection_state==='STALE'?'Älterer Stand':'Gespeicherter Prüfstand')+'<br>X: '+sourceCount(p.x_received)+' übergeben · '+sourceCount(p.x_profile_validated)+' Profile bestätigt · '+sourceCount(p.x_new_selected)+' neu ausgewählt · '+sourceCount(p.x_researched)+' recherchiert.<br>'+esc(p.detail||'Prüfergebnisse stehen in PULSAR.')+'</p>':'');
}
function renderXSourcePlan(x={}) {
  const d=x.discovery||{},accounts=Array.isArray(d.source_accounts)?d.source_accounts:[];
  const link=u=>{try{const p=new URL(u);return p.protocol==='https:'&&!p.username&&!p.password?p.href:''}catch{return ''}};
  return '<p class="small">Öffentliche Konten dienen als Hinweisgeber. Ihre Auswahl bestätigt weder die Identität jedes empfangenen Posts noch dessen Inhalt. Politische Aussagen und Unternehmensmeldungen werden getrennt eingeordnet.</p>'+accounts.map(a=>{
    const row=typeof a==='string'?{handle:a}:a,url=link(row.reference||row.source_url||row.reference_url||row.evidence_url);
    return '<p><strong>@'+esc(row.handle||row.username||'unbekannt')+'</strong> · '+esc(({POLITICAL:'Politische Aussagen',GOVERNMENT:'Regierung',CENTRAL_BANK:'Zentralbank',REGULATOR:'Finanzaufsicht'})[row.role]||row.role||row.category||'Recherchequelle')+(url?' · <a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">Kontenbeleg</a>':'')+'</p>';
  }).join('')+'<p class="small">'+esc(typeof d.plan==='string'?d.plan:(d.plan?.slots_utc||[]).join(' · ')||'Bis zu drei kleine Suchläufe pro Tag; öffentliche Konten und Aktienhinweise werden nach festem Plan abgefragt.')+' (UTC). Jeder Bereich wird höchstens einmal täglich abgefragt. Die offene Unternehmenssuche findet auch Werte außerhalb des bisherigen Universums.</p><p class="small">Automatische Vergleichsgruppe: '+esc((d.monitoring_symbols||[]).join(', ')||'Noch im Aufbau')+'</p>'+renderXAccountRegistry(d.account_registry);
}
const registryStates={PROPOSED:'Vorgeschlagen',ACTIVE:'Aktiv im Abrufplan',QUARANTINED:'Quarantäne',EXPIRED:'Abgelaufen',REMOVED:'Entfernt'};
function renderXAccountRegistry(reg){
  reg=reg||{};const rows=Array.isArray(reg.rows)?reg.rows:[];
  const limits=reg.limits||{};
  let html='<h3>Dynamische Quellen-Registry</h3><p class="small">'+esc(reg.detail||'Konten, die in den bezahlten Stichproben wiederholt relevante Themen mit Primärquellen-Links veröffentlichen, werden hier automatisch vorgeschlagen. Aktiv werden sie erst nach deiner eigenen Identitätsprüfung.')+'</p>';
  if(!rows.length)return html+'<p>Noch keine Vorschläge — es braucht mindestens '+sourceCount(limits.proposal_thresholds?.relevant_posts??3)+' relevante Posts an '+sourceCount(limits.proposal_thresholds?.distinct_days??2)+' Tagen innerhalb der 7-Tage-Stichprobe.</p>';
  html+='<div class="table-wrap"><table><thead><tr><th>Konto</th><th>Zustand</th><th>Belege</th><th>Themen / Primärquellen</th><th>Aktion</th></tr></thead><tbody>'+rows.map(r=>{
    const actions=[];
    if(r.state==='PROPOSED')actions.push('<button class="secondary" data-registry-action="activate" data-registry-id="'+esc(r.account_id)+'">Aktivieren</button>');
    if(r.state==='ACTIVE')actions.push('<button class="secondary" data-registry-action="quarantine" data-registry-id="'+esc(r.account_id)+'">Quarantäne</button>');
    if(r.state!=='REMOVED')actions.push('<button class="secondary" data-registry-action="remove" data-registry-id="'+esc(r.account_id)+'">Entfernen</button>');
    return '<tr><td><a href="https://x.com/i/user/'+esc(r.account_id)+'" target="_blank" rel="noopener noreferrer">Konto '+esc(r.account_id)+'</a>'+(r.identity_note?'<br><span class="small">'+esc(r.identity_note)+'</span>':'')+'</td><td>'+esc(registryStates[r.state]||r.state)+(r.expires_at?'<br><span class="small">läuft ab '+esc(observationTime(r.expires_at))+'</span>':'')+'</td><td>'+sourceCount(r.relevant_post_count)+' relevante Posts an '+sourceCount(r.distinct_day_count)+' Tagen<br><span class="small">zuletzt '+esc(observationTime(r.last_relevant_at))+'</span></td><td>'+esc((r.topics||[]).join(', ')||'—')+(Array.isArray(r.primary_domains)&&r.primary_domains.length?'<br><span class="small">'+esc(r.primary_domains.join(', '))+'</span>':'')+'</td><td>'+actions.join(' ')+'</td></tr>';
  }).join('')+'</tbody></table></div><p class="small">Höchstens '+sourceCount(limits.active??5)+' aktive dynamische Konten; Vorschläge verfallen nach '+sourceCount(limits.proposal_ttl_days??14)+' Tagen ohne neue Relevanz, aktive nach '+sourceCount(limits.active_ttl_days??30)+' Tagen. Auch ein aktives Konto bestätigt allein weder Krise noch Kauf oder Verkauf.</p>';
  return html;
}
async function registryAction(id,action){
  let note='';
  if(action==='activate'){
    note=window.prompt('Identitätsvermerk: Wer steht nach deiner eigenen Prüfung hinter diesem Konto? (mind. 10 Zeichen)')||'';
    if(note.trim().length<10)return;
  }else if(action==='quarantine'){
    note=window.prompt('Grund der Quarantäne (optional)')||'';
  }else if(action==='remove'&&!window.confirm('Konto dauerhaft aus der Registry entfernen? Es wird nicht erneut vorgeschlagen.')){
    return;
  }
  try{await api('/api/market-intelligence/registry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_id:id,action,note})});await loadSources()}
  catch(e){const status=document.querySelector('#x-status');if(status)status.insertAdjacentHTML('afterbegin','<p class="alert warn">'+esc(e.message)+'</p>')}
}
function renderXNormalization(x={}) {
  const n=x.normalization||{},coverage=x.counts_coverage||{};
  const problems=Array.isArray(coverage)?coverage.filter(r=>r.state!=='COMPLETE'):[];
  return '<p class="small">Kandidaten können bereits während des Aufbaus gefunden werden. Belastbare Langzeitvergleiche benötigen vollständige Messdaten. Stille, vom Anbieter ausgelassene Tage werden nicht als null Posts ergänzt.</p>'+(n.changed_at||n.control_since?'<p class="small">Aktuelle Vergleichsgruppe seit '+esc(observationTime(n.changed_at||n.control_since))+'. '+esc(n.detail||'Änderungen starten nur die gemeinsame Normierung neu.')+'</p>':'')+problems.slice(-24).map(r=>'<p class="alert warn"><strong>'+esc(r.symbol)+' · unvollständige Tagesabdeckung</strong><br>'+sourceCount(r.observed_days)+' von '+sourceCount(r.expected_days)+' angeforderten Tagen erhalten · an '+sourceCount(r.consecutive_incomplete_days)+' aufeinanderfolgenden Abruftagen unvollständig.<br>'+esc(r.detail)+'<br>Fehlende Tage: '+esc((r.missing_days||[]).join(', ')||'Antwortformat oder Seitengrenze prüfen')+'</p>').join('');
}
function renderXReceipts(x={}) {
  const requests=Array.isArray(x.requests)?x.requests:[],consumers=Array.isArray(x.consumers)?x.consumers:[];
  return '<p class="small">Begrenzter Ausschnitt mit Prüfkennungen. Keine X-Rohtexte oder Zugangsdaten.</p><div class="table-wrap"><table><thead><tr><th>Abfrage</th><th>Zeit</th><th>Ausgang</th><th>Such-Prüfsumme</th><th>Antwort-Prüfsumme</th></tr></thead><tbody>'+requests.slice(-30).reverse().map(row=>'<tr><td>'+esc(row.kind)+' · '+esc(row.context)+'</td><td>'+esc(observationTime(row.started))+'</td><td>'+esc(row.status)+' · HTTP '+esc(row.http??'unbekannt')+'</td><td>'+esc(row.query_hash||'nicht belegt')+'</td><td>'+esc(row.body_hash||'nicht belegt')+'</td></tr>').join('')+'</tbody></table></div><h3>Übergabe an die Recherche</h3>'+consumers.slice(0,30).map(row=>'<p class="small"><strong>'+esc(row.consumer)+' · '+esc(row.context)+'</strong> · '+esc(observationTime(row.consumed))+'<br>Paket-Prüfsumme '+esc(row.payload_hash||'nicht belegt')+'</p>').join('');
}
function renderXEvents(rows=[]) {
  if(!Array.isArray(rows)||!rows.length)return '<p>Noch keine gespeicherten Ereignishinweise. Das ist keine Bestätigung einer nachrichtenfreien Marktlage.</p>';
  return rows.slice(0,30).map(row=>'<article class="card"><p><strong>'+esc(row.title||row.category||row.event_type||'Recherchehinweis')+'</strong> · '+esc(row.status||row.state||'UNVERIFIED')+'</p><p>'+esc(row.summary||row.detail||'Einordnung noch offen')+'</p><p class="small">Werte: '+esc((Array.isArray(row.affected_symbols)?row.affected_symbols:Array.isArray(row.symbols)?row.symbols:[]).join(', ')||row.symbol||'nicht belegt')+'<br>Beobachtet: '+esc(observationTime(row.detected_at||row.observed_at||row.created_at))+'<br>Beleg: '+esc(row.id||row.event_id||row.evidence_hash||'nicht belegt')+'</p></article>').join('');
}
async function loadSources() {
  try {
    const s=await api('/api/market-intelligence'),x=s.x||{},settings=x.settings||{};
    const status=document.querySelector('#x-status');if(status)status.innerHTML=renderXStatus(x);
    const pipeline=document.querySelector('#source-pipeline');if(pipeline)pipeline.innerHTML=renderSourcePipeline(s);
    const newsRisk=document.querySelector('#news-risk-evidence');if(newsRisk)newsRisk.innerHTML=renderNewsRiskEvidence(s.news_risk_evidence);
    const traces=document.querySelector('#x-receipts');if(traces)traces.innerHTML=renderXReceipts(x);
    const events=document.querySelector('#x-events');if(events)events.innerHTML=renderXEvents(x.events);
    const attention=document.querySelector('#x-attention');if(attention)attention.innerHTML=renderXAttention(x.attention);
    const discovery=document.querySelector('#x-discovery');if(discovery)discovery.innerHTML=renderXDiscovery(x,s.pulsar_pipeline);
    const research=document.querySelector('#x-candidate-research');if(research)research.innerHTML=renderXCandidateResearch(x);
    const plan=document.querySelector('#x-source-plan');if(plan){plan.innerHTML=renderXSourcePlan(x);plan.querySelectorAll('[data-registry-action]').forEach(b=>b.onclick=()=>registryAction(b.dataset.registryId,b.dataset.registryAction));}
    const normalization=document.querySelector('#x-normalization');if(normalization)normalization.innerHTML=renderXNormalization(x);
    if(document.querySelector('#x-settings')&&!sourceSettingsLoaded){
      document.querySelector('#x-enabled').checked=settings.enabled===true;
      document.querySelector('#x-budget').value=settings.monthly_budget_eur??15;
      document.querySelector('#x-symbols').value=(settings.symbols||[]).join(', ');
      document.querySelector('#x-accounts').value=(settings.priority_accounts||[]).join(', ');
      document.querySelector('#x-pricing').checked=settings.pricing_acknowledged===true;
      document.querySelector('#x-token-status').textContent=settings.token_configured?'Token gespeichert':'Noch kein Token eingerichtet';
      document.querySelector('#x-pricing-detail').textContent=`Preisgrundlage des Sammlers: ${settings.post_usd??'unbekannt'} USD je geliefertem Post und ${settings.counts_request_usd??'unbekannt'} USD je Counts-Anfrage, EUR-Pufferfaktor ${settings.eur_reserve_multiplier??'unbekannt'} (Stand ${settings.price_basis_date??'unbekannt'}). Counts höchstens einmal pro ${settings.counts_interval_hours??24} Stunden; bis zu ${settings.searches_per_day??3} allgemeine plus ${settings.candidate_searches_per_day??3} gezielte PULSAR-Suchabrufe am Tag mit je ${settings.posts_per_request??10} Posts. Geplante Monatsobergrenze bei voller Nutzung: ${sourceMoney(settings.estimated_month_eur)}. Bei knapperem Budget werden weitere Abrufe ausgelassen.`;
      sourceSettingsLoaded=true;
    }
  } catch(error) {
    const status=document.querySelector('#x-status');if(status)status.innerHTML='<p class="alert warn">Quellenstand nicht abrufbar: '+esc(error.message)+'</p>';
  }
}
async function saveXSettings(event) {
  event.preventDefault();const message=document.querySelector('#x-message');
  const list=id=>document.querySelector(id).value.split(/[,\s]+/).map(v=>v.trim()).filter(Boolean);
  const payload={enabled:document.querySelector('#x-enabled').checked,monthly_budget_eur:Number(document.querySelector('#x-budget').value),symbols:list('#x-symbols').map(v=>v.toUpperCase()),priority_accounts:list('#x-accounts').map(v=>v.replace(/^@/,'')),pricing_acknowledged:document.querySelector('#x-pricing').checked};
  const token=document.querySelector('#x-token');if(token.value.trim())payload.bearer_token=token.value.trim();
  try {
    const result=await api('/api/market-intelligence/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    token.value='';sourceSettingsLoaded=false;message.textContent=result.detail;await loadSources();
  } catch(error){message.textContent=error.message;}
}
document.querySelector('#x-settings')?.addEventListener('submit',saveXSettings);
document.querySelector('#sources-refresh')?.addEventListener('click',loadSources);
nexusPoll(loadSources,60000);

function renderXCandidateResearch(x={}) {
  const r=x.candidate_research||{},rows=r.candidates||[];
  const states={QUEUED:'Wartet auf Abruf',COLLECTING:'Abruf reserviert / läuft',PROCESSED:'Stichprobe verarbeitet',NO_MATCHING_SAMPLE:'Keine auswertbaren Treffer',STALE:'Stichprobe veraltet',STALE_CANDIDATE:'Kandidat veraltet',NETWORK_ERROR:'Netzwerkfehler',AUTH_ERROR:'Zugang prüfen',RATE_LIMITED:'Anbieterpause',INVALID_RESPONSE:'Antwort nicht auswertbar'};
  const sentiment={POSITIVE_HINT:'Positive Schlagworthinweise',NEGATIVE_HINT:'Negative Schlagworthinweise',MIXED:'Gemischte Schlagworthinweise',UNCLASSIFIED:'Nicht eindeutig einzuordnen',UNKNOWN:'Unbekannt'};
  return '<p>'+esc(r.detail||'Noch kein Rechercheplan vorhanden.')+'</p><p class="small">X-Status: '+esc(x.state||'unbekannt')+' · Nächstes Zeitfenster: '+esc(observationTime(r.next_slot_at))+' · Budgetrest: '+(knownNumber(x.budget?.remaining_eur)?Number(x.budget.remaining_eur).toFixed(2)+' EUR':'unbekannt')+'</p>'+(rows.length?rows.map(c=>'<article class="card"><div class="section-head"><h3>'+esc(c.symbol)+' · '+esc(c.company_name||'')+'</h3>'+badge(states[c.state]||c.state,c.state==='PROCESSED'?'ok':'warn')+'</div><p class="small">PULSAR-Ursprung: '+esc(c.origin)+' · Auswahl: '+esc(observationTime(c.selected_at))+'<br>Letzter Abruf: '+esc(observationTime(c.last_attempt))+' · Verarbeitung: '+esc(observationTime(c.processed_at))+'</p><p><strong>'+esc(sentiment[c.sentiment]||'Unbekannt')+'</strong><br>'+esc(c.assessment||'')+'</p><p class="small">Im gemeinsamen Abruf empfangen: '+sourceCount(c.request_posts_received)+' · Dem Kandidaten zugeordnet: '+sourceCount(c.matched_posts)+' · Aktuell auswertbar: '+sourceCount(c.usable_posts)+' · Unterschiedliche Konten in der Stichprobe: '+sourceCount(c.distinct_accounts_in_sample)+'</p><details><summary>Suchauftrag und Belege</summary><p><code>'+esc(c.query||'Suchauftrag wird im nächsten freien Zeitfenster gebildet')+'</code></p><p class="small">'+esc(c.method||'')+'</p>'+(c.evidence||[]).map(e=>'<p class="small">'+esc(e.topic||'Thema unklassifiziert')+' · '+esc(e.category||'')+' · '+esc(observationTime(e.created_at))+' · <a target="_blank" rel="noopener noreferrer" href="https://x.com/i/web/status/'+encodeURIComponent(String(e.post_id||''))+'">X-Beitrag ansehen</a></p>').join('')+'</details></article>').join(''):'<p>Noch keine aktuellen PULSAR-Kandidaten zur gezielten X-Recherche vorgemerkt. Die Auswahl erfolgt automatisch im PULSAR-Recherchelauf.</p>')+'<p class="small">Diese Anzeige zeigt Recherchehinweise. X erteilt keine Kauf-, Verkaufs- oder Krisenfreigabe. Mehrere X-Beiträge sind weiterhin dieselbe Quellenfamilie; unabhängige Belege und die regulären Handelsprüfungen bleiben erforderlich. Der nächste reguläre PULSAR-Lauf übernimmt verfügbare Ergebnisse in die Bewertung.</p>';
}
