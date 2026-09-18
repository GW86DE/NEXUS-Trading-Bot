const backtestMessage=document.querySelector('#backtest-message');
const backtestStates={STARTING:'Wird vorbereitet',RUNNING:'Läuft',COMPLETED:'Abgeschlossen',FAILED:'Fehlgeschlagen',INTERRUPTED:'Unterbrochen'};
const backtestLogsOpen=new Set();
async function startBacktest(nurPlan){
 const options={umfang:document.querySelector('#backtest-scope').value,
  aktien_limit:Number(document.querySelector('#backtest-stocks').value)||15,
  krypto_limit:Number(document.querySelector('#backtest-crypto').value)||5,
  nur_plan:!!nurPlan};
 try{await api('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(options)});backtestMessage.textContent=nurPlan?'Planvorschau gestartet.':'Backtest gestartet.';await loadBacktest()}
 catch(e){backtestMessage.textContent=e.message}
}
function backtestDuration(row){
 const from=row.started_at||row.created_at;if(!from)return'';
 const to=row.finished_at||Date.now()/1000;const n=Math.max(0,to-from);
 return n>=5400?` · ${(n/3600).toFixed(1)} Std.`:n>=90?` · ${Math.round(n/60)} Min.`:` · ${Math.round(n)} s`;
}
async function toggleBacktestLog(id,box){
 if(backtestLogsOpen.has(id)){backtestLogsOpen.delete(id);box.hidden=true;return}
 backtestLogsOpen.add(id);box.hidden=false;box.textContent='Protokoll wird geladen …';
 try{const data=await api('/api/backtest/'+id+'/log');box.textContent=(data.lines||[]).join('\n')||'Noch keine Protokollzeilen.'; box.scrollTop=box.scrollHeight}
 catch(e){box.textContent=e.message}
}
async function loadBacktest(){
 try{
  const state=await api('/api/backtest');
  document.querySelector('#backtest-start').disabled=state.busy||!state.script_available;
  document.querySelector('#backtest-plan').disabled=state.busy||!state.script_available;
  document.querySelector('#backtest-directory').textContent=state.script_available?('Ablage: '+state.output_directory):'Backtest-Werkzeug fehlt im Quellordner.';
  document.querySelector('#backtest-jobs').innerHTML=(state.jobs||[]).map(row=>{
   const active=['STARTING','RUNNING'].includes(row.status);
   const opts=row.options||{};
   const title=`${esc(row.mode||'Backtest')} · ${esc(backtestStates[row.status]||row.status)}${backtestDuration(row)}`;
   const scope=opts.nur_plan?'Planvorschau ohne Netzabruf':`${opts.umfang==='aktiv'?'Aktives Universum':'Fokus'} · max. ${opts.aktien_limit} Aktien / ${opts.krypto_limit} Coins`;
   return `<article class="section"><h3>${title}</h3><p class="small">${esc(observationTime(row.created_at))} · ${esc(scope)}</p><p>${esc(row.detail||'')}</p><div class="toolbar">${row.report_url?'<a class="button" href="'+esc(row.report_url)+'" target="_blank" rel="noopener">Bericht öffnen</a>':''}${row.download_url?'<a class="button secondary" href="'+esc(row.download_url)+'">ZIP herunterladen</a>':''}<button class="secondary" data-backtest-log="${esc(row.id)}">${active?'Live-Protokoll':'Protokoll'}</button></div><pre class="backtest-log" data-backtest-log-box="${esc(row.id)}" ${backtestLogsOpen.has(row.id)?'':'hidden'}></pre></article>`;
  }).join('')||'<p>Noch kein Backtest über diese WebUI gestartet.</p>';
  document.querySelectorAll('[data-backtest-log]').forEach(b=>{b.onclick=()=>toggleBacktestLog(b.dataset.backtestLog,document.querySelector('[data-backtest-log-box="'+b.dataset.backtestLog+'"]'))});
  for(const id of backtestLogsOpen){const box=document.querySelector('[data-backtest-log-box="'+id+'"]');if(!box)continue;try{const data=await api('/api/backtest/'+id+'/log');box.hidden=false;box.textContent=(data.lines||[]).join('\n')||'Noch keine Protokollzeilen.';box.scrollTop=box.scrollHeight}catch(e){/* Anzeige behält den letzten Stand */}}
 }catch(e){backtestMessage.textContent=e.message}
}
document.querySelector('#backtest-start').onclick=()=>startBacktest(false);
document.querySelector('#backtest-plan').onclick=()=>startBacktest(true);
nexusPoll(loadBacktest,5000);
