function analysisScope(task){return String(task||'').startsWith('crypto_')?'OKX · öffentliche Spotdaten · Simulation':(['backtest','walkforward','profiles','sweep','ml'].includes(task)?'Aktienstrategie · Yahoo-Historie · Simulation':'System / Aktien-Research · keine Orders');}
let analyseDaten = {tasks: [], recent: [], running: false};
let aktiverJob = null;
let pollTimer = null;

const statusText = value => ({NEVER:'Noch nie',STARTING:'Startet',RUNNING:'Läuft',
  SUCCEEDED:'Erfolgreich',FAILED:'Fehlgeschlagen',INTERRUPTED:'Unterbrochen',TIMED_OUT:'Zeitlimit erreicht',OUTPUT_LIMIT:'Ausgabelimit erreicht',UNKNOWN:'Prozessstatus unklar'})[value] || value || 'Unbekannt';
const statusKlasse = value => value === 'SUCCEEDED' ? 'ok' : ['FAILED','INTERRUPTED','TIMED_OUT','OUTPUT_LIMIT','UNKNOWN'].includes(value) ? 'bad' : ['RUNNING','STARTING'].includes(value) ? 'warn' : '';
const datum = value => value ? new Date(value).toLocaleString('de-DE', {dateStyle:'short',timeStyle:'short',timeZone:'Europe/Berlin'}) : '–';

function zeichneZusammenfassung() {
  const fertig = (analyseDaten.recent || []).filter(x => x.status === 'SUCCEEDED').length;
  const zuletzt = (analyseDaten.recent || [])[0];
  document.querySelector('#analysis-summary').innerHTML = `
    <article class="card"><h3>Werkzeuge</h3><div class="metric">${analyseDaten.tasks.length}</div><div class="small">aus der Desktop-GUI übertragen</div></article>
    <article class="card"><h3>Systemstatus</h3><div class="metric ${analyseDaten.running||analyseDaten.blocked?'warn':'pos'}">${analyseDaten.blocked?'PRÜFUNG ERFORDERLICH':analyseDaten.running?'ANALYSE LÄUFT':'BEREIT'}</div><div class="small">maximal ein lokaler Lauf</div></article>
    <article class="card"><h3>Erfolgreiche Läufe</h3><div class="metric">${fertig}</div><div class="small">in der aktuellen Historie</div></article>
    <article class="card"><h3>Letzter Lauf</h3><div class="metric small-metric">${esc(zuletzt?.label || 'Noch keiner')}</div><div class="small">${esc(datum(zuletzt?.started_at))}</div></article>`;
}

function zeichneGruppen() {
  const opened=new Set([...document.querySelectorAll('.analysis-group[open]')].map(n=>n.dataset.group));
  const groups = {};
  (analyseDaten.tasks || []).forEach(task => (groups[task.group] ||= []).push(task));
  document.querySelector('#analysis-groups').innerHTML = Object.entries(groups).map(([group,tasks]) => `
    <details class="panel analysis-group" data-group="${esc(group)}" ${opened.has(group)?'open':''}><summary>${esc(group)} · ${tasks.length} Werkzeuge</summary><div class="analysis-tool-grid">${tasks.map(task => `
      <div class="analysis-tool"><div><h3>${esc(task.label)}</h3><div class="scope-note">${esc(analysisScope(task.id))}</div><p>${esc(task.description)}</p></div>
      <div class="analysis-tool-foot"><span class="status ${statusKlasse(task.status)}">${esc(statusText(task.status))}</span>
      <button class="analysis-start" data-task="${esc(task.id)}" ${analyseDaten.running||analyseDaten.blocked?'disabled':''}>Starten</button></div></div>`).join('')}</div></details>`).join('');
  document.querySelectorAll('.analysis-start').forEach(button => button.addEventListener('click', () => starteAnalyse(button.dataset.task)));
}

function zeichneHistorie() {
  const rows = analyseDaten.recent || [];
  document.querySelector('#analysis-history').innerHTML = rows.length ? `<table><thead><tr><th>Start</th><th>Analyse</th><th>Status</th><th>Ende</th><th>Ausgabe</th></tr></thead><tbody>${rows.map(row => `<tr><td>${esc(datum(row.started_at))}</td><td><strong>${esc(row.label || row.task)}</strong><div class="small">${esc(analysisScope(row.task))}</div></td><td>${badge(statusText(row.status),statusKlasse(row.status))}</td><td>${esc(datum(row.finished_at))}</td><td><button class="secondary analysis-open" data-job="${esc(row.job_id)}">Öffnen</button></td></tr>`).join('')}</tbody></table>` : '<p class="small">Noch keine Analyseläufe gespeichert.</p>';
  document.querySelectorAll('.analysis-open').forEach(button => button.addEventListener('click', () => oeffneJob(button.dataset.job)));
}

async function ladeAnalyse() {
  try {
    analyseDaten = await api('/api/analysis');
    zeichneZusammenfassung(); zeichneGruppen(); zeichneHistorie();
    const running = (analyseDaten.recent || []).find(x => x.status === 'RUNNING');
    if (running && (!aktiverJob || aktiverJob === running.job_id)) oeffneJob(running.job_id, true);
  } catch (error) {
    document.querySelector('#analysis-groups').innerHTML = `<article class="alert danger">${esc(error.message)}</article>`;
  }
}

async function starteAnalyse(task) {
  if (!confirm('Analyse jetzt lokal starten? Sie kann je nach Datenmenge mehrere Minuten dauern.')) return;
  try {
    const result = await api(`/api/analysis/${encodeURIComponent(task)}`, {method:'POST'});
    aktiverJob = result.job.job_id;
    await ladeAnalyse();
    await oeffneJob(aktiverJob, true);
  } catch (error) { alert(error.message); }
}

async function oeffneJob(jobId, polling = false) {
  if(polling&&document.hidden){clearTimeout(pollTimer);pollTimer=setTimeout(()=>oeffneJob(jobId,true),10000);return;}
  aktiverJob = jobId;
  clearTimeout(pollTimer);
  try {
    const result = await api(`/api/analysis/jobs/${encodeURIComponent(jobId)}`);
    const state = result.state || {};
    const badgeNode = document.querySelector('#analysis-result-status');
    badgeNode.textContent = statusText(state.status);
    badgeNode.className = `status ${statusKlasse(state.status)}`;
    document.querySelector('#analysis-result-info').textContent = `${state.label || state.task} · ${analysisScope(state.task)} · gestartet ${datum(state.started_at)}`;
    const output = document.querySelector('#analysis-output');
    output.textContent = result.output || 'Noch keine Ausgabe.';
    output.scrollTop = output.scrollHeight;
    if (['RUNNING','STARTING'].includes(state.status)) pollTimer = setTimeout(() => oeffneJob(jobId, true), 2000);
    else if (polling) await ladeAnalyse();
  } catch (error) {
    document.querySelector('#analysis-output').textContent = error.message;
  }
}

document.querySelector('#analysis-refresh').addEventListener('click', ladeAnalyse);
ladeAnalyse();
