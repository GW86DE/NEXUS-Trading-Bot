/* Behaviour tests run the production renderers and scheduler without a browser. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {test}=require('node:test');
const root=path.resolve(__dirname,'..');

test('FMP source opens authenticated stored receipt without exposing provider URL',()=>{
  const {context}=environment(['pulsar.js']);
  const html=context.pulsarSourceLink({provider:'FMP',id:'a'.repeat(64),url:'https://financialmodelingprep.com/stable/profile?apikey=secret'},'AGI');
  assert.match(html,/href="\/api\/pulsar\/source\/AGI\//);
  assert.match(html,/Gespeicherten Beleg öffnen/);
  assert.doesNotMatch(html,/apikey|secret|financialmodelingprep/);
  assert.doesNotMatch(context.pulsarSourceLink({provider:'FMP',id:'bad',url:'https://example.com'},'AGI'),/<a /);
});

test('diagnosis shows real observation progress, locks starts, escapes text',async()=>{
  const {context,nodes}=environment(['diagnosis.js']);
  context.api=async()=>({busy:true,telegram_available:true,output_directory:'/reports',jobs:[{
    id:'a'.repeat(32),status:'RUNNING',phase:'OBSERVATION',mode:'30min',created_at:1,
    observation_started_at:Date.now()/1000-1200,requested_seconds:1800,
    telegram_status:'NOT_REQUESTED',detail:'<script>unsafe</script>'}]});
  await context.loadDiagnosis();
  assert.equal(nodes.get('#diagnosis-instant').disabled,true);
  assert.equal(nodes.get('#diagnosis-30min').disabled,true);
  assert.match(nodes.get('#diagnosis-jobs').innerHTML,/20 \/ 30 Minuten/);
  assert.doesNotMatch(nodes.get('#diagnosis-jobs').innerHTML,/<script>|data-diagnosis-send/);
});

test('diagnosis never starts or sends during polling and preserves explicit switch',async()=>{
  const {context,nodes}=environment(['diagnosis.js']);
  const calls=[];
  context.api=async(url,options)=>{calls.push({url,options});return {busy:false,telegram_available:true,jobs:[]}};
  await context.loadDiagnosis();
  assert.equal(calls.length,1);assert.equal(calls[0].options,undefined);
  nodes.get('#diagnosis-telegram').checked=true;
  await nodes.get('#diagnosis-instant').onclick();
  const post=calls.find(x=>x.options?.method==='POST');
  assert.deepEqual(JSON.parse(post.options.body),{mode:'instant',telegram:true});
});

test('unconfirmed Telegram send keeps local download but does not offer duplicate',async()=>{
  const {context,nodes}=environment(['diagnosis.js']);
  context.api=async()=>({busy:false,telegram_available:true,output_directory:'/reports',jobs:[{
    id:'a'.repeat(32),status:'COMPLETED',mode:'instant',created_at:1,telegram_status:'UNKNOWN',
    download_url:'/api/diagnosis/'+ 'a'.repeat(32)+'/download',completion:'COMPLETED_WITH_EXPORT_ERRORS'}]});
  await context.loadDiagnosis();
  const html=nodes.get('#diagnosis-jobs').innerHTML;
  assert.match(html,/ZIP herunterladen/);assert.match(html,/Teilbericht/);
  assert.doesNotMatch(html,/data-diagnosis-send/);
});

function environment(files=[]){
  const nodes=new Map(),timers=[];
  const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',textContent:'',dataset:{},addEventListener(){},querySelectorAll(){return[];},querySelector(){return null;},classList:{toggle(){}},setAttribute(){},replaceChildren(){}});return nodes.get(id);};
  const context={console,URL,URLSearchParams,Date,Number,Set,Map,Promise,JSON,String,Boolean,Math,
    document:{readyState:'loading',hidden:false,addEventListener(){},querySelector:s=>s==='#ai-diagnostics-panel'?null:node(s),querySelectorAll(){return[];},getElementById:node},
    location:{pathname:'/trades',search:'',hash:''},history:{replaceState(){}},
    window:{addEventListener(){}},setTimeout:(callback,delay)=>{timers.push({callback,delay});return timers.length;},clearTimeout(){},
    matchMedia:()=>({matches:true})};
  vm.createContext(context);
  const load=name=>vm.runInContext(fs.readFileSync(path.join(root,'webui/static',name),'utf8'),context,{filename:name});
  load('common.js');
  const originalPoll=context.nexusPoll;
  context.nexusPoll=()=>()=>{};
  context.api=async()=>({rows:[],entries:[],orders:[],pages:1});
  for(const f of files)load(f);
  return {context,nodes,timers,originalPoll,run:script=>vm.runInContext(script,context)};
}

test('poller waits for completion, creates no backlog and skips hidden tabs',async()=>{
  const {context,timers,originalPoll}=environment();
  let calls=0,resolve;
  const gate=new Promise(r=>resolve=r);
  const stop=originalPoll(async()=>{calls++;await gate;},15000);
  assert.equal(calls,1);assert.equal(timers.length,0);
  resolve();await new Promise(setImmediate);
  assert.equal(timers.length,1);assert.equal(timers[0].delay,15000);
  context.document.hidden=true;await timers.shift().callback();
  assert.equal(calls,1);assert.equal(timers.length,1);
  context.document.hidden=false;await timers.shift().callback();
  assert.equal(calls,2);stop();
  await timers.shift().callback();assert.equal(calls,2);
});

test('unknown channels never become green or online',()=>{
  const {context}=environment();
  assert.match(context.operationBadge({}),/Unbekannt/);
  assert.doesNotMatch(context.operationBadge({}),/status ok/);
  assert.match(context.operationBadge({state:'OFFLINE'}),/Offline/);
});

test('broker card shows REST online and websocket offline separately',()=>{
  const {context}=environment(['dashboard.js']);
  const result=context.brokerOverview('okx',{okx:{online:true,worker_alive:true},operations:{brokers:{okx:{
    buy:{state:'BLOCKED',detail:'Abgleich offen'},sell:{state:'PER_POSITION',detail:'Identität je Position'},
    rest:{state:'ONLINE',detail:'Bestätigter Abruf'},websocket:{state:'OFFLINE',detail:'Verbindung getrennt'}}}}});
  assert.match(result,/Neue Käufe/);assert.match(result,/Gesperrt/);assert.match(result,/Verkäufe/);
  assert.match(result,/REST/);assert.match(result,/Erreichbar/);assert.match(result,/WebSocket/);assert.match(result,/Offline/);
  assert.doesNotMatch(result,/>Verbunden</);assert.doesNotMatch(result,/VERKAUF AKTIV/);
});

test('buy blocks (10.7.0) list reason, scope, expiry and resolution per broker and never invent a block',()=>{
  const {context}=environment(['dashboard.js']);
  const sperren={domaene_gesperrt:false,gesperrte_symbole:['XRP'],anzahl:2,sperren:[
    {broker:'okx',grund:'BALANCE_REDUCTION',reichweite:'SYMBOL',ablauf:'BELEG',aufloesung:'Verkaufsbeleg (auch mit Lot-Rest) oder Bestand wieder da',detail:'Trade 81',symbol:'XRP',quelle:'buchhaltung'},
    {broker:'okx',grund:'TAGESVERLUSTGRENZE',reichweite:'DOMAIN',ablauf:'TAGESRESET',aufloesung:'naechster Handelstag',detail:'okx: Tagesverlustgrenze erreicht',symbol:'',quelle:'risikotopf'}]};
  const html=context.brokerOverview('okx',{okx:{online:true,worker_alive:true,sperren}});
  assert.match(html,/<h4>Kaufsperren<\/h4>/);assert.match(html,/Das Konto ist frei; gesperrt sind nur: XRP\./);
  assert.match(html,/<th>Grund<\/th><th>Reichweite<\/th><th>Endet durch<\/th><th>Auflösung<\/th>/);
  assert.match(html,/BALANCE_REDUCTION · XRP/);assert.match(html,/nur dieser Wert/);assert.match(html,/Beleg<\/td>/);
  assert.match(html,/TAGESVERLUSTGRENZE/);assert.match(html,/ganzes Konto/);assert.match(html,/Tageswechsel/);
  assert.match(html,/Verkaufsbeleg \(auch mit Lot-Rest\)/);
  // Ohne Liste (aelterer Kern) wird nichts behauptet; leere Liste heisst: keine Sperre.
  assert.doesNotMatch(context.brokerOverview('okx',{okx:{online:true,worker_alive:true}}),/Kaufsperren/);
  assert.match(context.brokerOverview('etoro',{etoro:{online:true,worker_alive:true,sperren:{domaene_gesperrt:false,gesperrte_symbole:[],anzahl:0,sperren:[]}}}),/Keine aktive Kaufsperre\./);
  assert.match(context.brokerOverview('etoro',{etoro:{sperren:{fehler:'OSError'}}}),/Sperrliste nicht lesbar: OSError/);
});

test('unknown Pi uptime and timestamps are not fabricated zero readings',()=>{
  const {context}=environment(['dashboard.js']);
  assert.equal(context.fmtDuration(undefined),'unbekannt');
  assert.equal(context.observationTime(null),'nicht belegt');
  assert.equal(context.knownNumber(null),false);assert.equal(context.knownNumber(''),false);
  assert.equal(context.knownNumber(false),false);assert.equal(context.knownNumber(' '),false);
  assert.equal(context.knownNumber(0),true);
  assert.equal(context.pct(null,100),'unbekannt');assert.equal(context.pct(0,100),'0.0 %');
  assert.doesNotMatch(context.brokerOverview('etoro',{}),/<strong>0<\/strong><span>Depotpositionen/);
});

test('order identity rejects absent scope and encodes exact client identity',()=>{
  const {context}=environment(['execution_details.js']);
  assert.equal(context.executionIdentity({broker:'okx',client_id:'same'}),null);
  const identity={broker:'okx',account:'A',environment:'DEMO',client_id:'buy+1/&'};
  const first=context.executionIdentity(identity);
  assert.equal(first.get('client_id'),'buy+1/&');
  assert.notEqual(first.toString(),context.executionIdentity({...identity,account:'B'}).toString());
  assert.notEqual(first.toString(),context.executionIdentity({...identity,environment:'LIVE'}).toString());
});

test('order detail keeps terminal, native evidence and accounting independent',()=>{
  const {context}=environment(['execution_details.js']);
  const html=context.executionFacts({terminal:1,evidence_complete:0,accounted:0});
  assert.match(html,/Auftragsabschluss<\/dt><dd>gemeldet/);
  assert.match(html,/Ausführungsbelege<\/dt><dd>noch unvollständig/);
  assert.match(html,/Mengenbuchung<\/dt><dd>noch offen/);
  assert.doesNotMatch(html,/abgeschlossen/);
});

test('closed position never claims old order is broker open or filled',()=>{
  const {context}=environment(['trades.js','execution_details.js']);
  const row={state:'POSITION_CLOSED_ORDER_UNPROVEN',terminal:false,evidence_complete:false,accounted:false,filled:0,net_filled:0};
  assert.match(context.executionLabel(row.state),/Position geschlossen.*Auftragsausgang ungeklärt/);
  const html=context.executionFacts(row);
  assert.match(html,/Position ist geschlossen/);
  assert.match(html,/Ausgang dieses Auftrags ungeklärt/);
  assert.doesNotMatch(html,/Beim Broker offen/);
});

test('order fills preserve unknown fee, native currency and escape external text',()=>{
  const {context}=environment(['execution_details.js']);context.executionLabel=s=>s||'unbekannt';
  const html=context.executionDetailHTML({order:{state:'FILLED'},fills:[{fill_id:'<img onerror=bad>',quantity:'0.01',price:'12.3',fee:null,fee_currency:'BTC'}],events:[{kind:'RECONCILE',detail:'<script>bad()</script>'}]});
  assert.match(html,/BTC/);assert.match(html,/unbekannt/);assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<script>/);assert.doesNotMatch(html,/<img onerror/);
});

test('unknown order side is not described as a sale',()=>{
  const {context}=environment(['execution_details.js']);context.executionLabel=s=>s||'unbekannt';
  const html=context.executionCard({instrument:'SUI-USDC'},0);
  assert.match(html,/Auftragsseite unbekannt/);assert.doesNotMatch(html,/ · Verkauf/);
});

test('detail truncation notice requires a real bounded-result flag',()=>{
  const {context}=environment(['execution_details.js']);context.executionLabel=s=>s||'unbekannt';
  assert.doesNotMatch(context.executionDetailHTML({truncated:{fills:false,events:false}}),/Weitere Belege sind gespeichert/);
  assert.match(context.executionDetailHTML({truncated:{fills:true,events:false}}),/Weitere Belege sind gespeichert/);
});

test('partial history database error is displayed and never becomes no open orders',async()=>{
  const {context,nodes}=environment(['execution_details.js']);context.api=async()=>({orders:[],complete:false,error:'DB_LOCKED'});
  await context.loadExecutionHistory();
  const html=nodes.get('#execution-history').innerHTML;
  assert.match(html,/DB_LOCKED/);assert.match(html,/Verlauf nicht abrufbar/);
  assert.doesNotMatch(html,/Keine gespeicherten Aufträge/);
});

test('GPT precheck blocked and actual timeout have different execution claims',()=>{
  const {context}=environment(['ai_diagnostics.js']);
  const blocked=context.aiExecutionView({execution:{phase:'NOT_STARTED',request_dispatched:false,error_code:'PULSAR_PRECHECK_FAILED'}},'Vertiefung');
  const timeout=context.aiExecutionView({model:'gpt-test',execution:{phase:'TIMED_OUT',request_dispatched:true,duration_seconds:30,local_request_id:'batch-1'}},'Vorprüfung');
  assert.match(blocked,/Nicht gestartet/);assert.match(blocked,/Kein neuer Request gestartet/);assert.match(blocked,/PULSAR_PRECHECK_FAILED/);
  assert.match(timeout,/Zeitlimit erreicht/);assert.match(timeout,/Request tatsächlich gestartet/);assert.match(timeout,/30.00 s/);assert.match(timeout,/batch-1/);
});

test('cached result is not a fresh request and legacy failures stay unproven',()=>{
  const {context}=environment(['ai_diagnostics.js']);
  const cached=context.aiExecutionView({execution:{phase:'CACHE_HIT',request_dispatched:false,cache_used:true}},'GPT');
  assert.match(cached,/Cache verwendet/);assert.match(cached,/Kein neuer Request gestartet/);
  const legacy=context.aiExecutionView({ok:false},'GPT');
  assert.match(legacy,/Ausführungsbeleg fehlt/);assert.match(legacy,/nicht belegt/);assert.doesNotMatch(legacy,/Request tatsächlich gestartet/);
});

test('GPT diagnostic list is bounded and does not invent provider health',()=>{
  const {context}=environment(['ai_diagnostics.js']);
  assert.match(context.renderAIDiagnostics({records:[]}),/Provider-Erreichbarkeit ist dadurch nicht bestätigt/);
  const html=context.renderAIDiagnostics({records:Array.from({length:50},(_,i)=>({symbol:'S'+i,execution:{phase:'SUCCEEDED',request_dispatched:true}}))});
  assert.equal((html.match(/class="request-phase"/g)||[]).length,30);
});

test('PULSAR card uses phase evidence and missing source numbers stay unknown',()=>{
  // 10.3.0 Hype-Spur: keine Community-Breite/Terra-Ansichten mehr; die Karte
  // zeigt offene Hype-Kriterien und erfindet weiterhin keine Zahlen.
  const {context}=environment(['ai_diagnostics.js','pulsar.js']);
  const html=context.renderPulsarCard({symbol:'SUI',name:'Sui',sources:[],precheck:{ok:false,execution:{phase:'TIMED_OUT',request_dispatched:true}},analysis:{execution:{phase:'NOT_STARTED',request_dispatched:false,error_code:'PULSAR_PRECHECK_FAILED'}},countercheck:{}},0);
  assert.match(html,/Hype-Spur: Kriterien offen/);assert.match(html,/Zeitlimit erreicht/);
  assert.doesNotMatch(html,/Community-Breite/);assert.doesNotMatch(html,/Belegscore/);
  assert.match(html,/unbekannt Erwähnungen/);assert.doesNotMatch(html,/Autoren: 0/);
});

test('PULSAR old trades with missing paper flag never claim live mode',()=>{
  const {context}=environment(['ai_diagnostics.js','pulsar.js']);
  const html=context.tradeRows([{symbol:'ABC',menge:1,einstieg_preis:1,waehrung:'USD'}]);
  assert.match(html,/Umgebung unbekannt/);assert.doesNotMatch(html,/>LIVE</);
});

test('PULSAR card (10.5.0) shows trigger, two-of-three confirmations, squeeze and existence chips; legacy community checks are gone',()=>{
  const {context}=environment(['ai_diagnostics.js','pulsar.js']);
  const card={symbol:'A',name:'A Corp',sources:[],state:'AUSLOESER',eligible:false,hype:{trigger:{kind:'VOLUMEN+REDDIT',detail:'VOLUMEN: 3,4x; REDDIT: 150 Erwaehnungen'},confirmed_count:1,
    confirmations:{volumen:{ok:true,detail:'3,4x'},zweite_social_familie:{ok:false,detail:'Nur eine Social-Familie belegt (REDDIT)'},kurs:{ok:false,detail:'Quote nicht bestaetigt: 1.0 %'}},
    squeeze:{flag:true,detail:'Short-Anteil 22,0 %'},existence_risk:{blocked:false,findings:[]},finance_note:'Bilanz (nur Information): Gewinn negativ'},
    stocktwits:{messages_1h:12,authors_1h:7,bullish_1h:9,bearish_1h:1,truncated_1h:false},community_checks:[{name:'unique_authors',value:null}]};
  const html=context.renderPulsarCard(card,0);
  assert.match(html,/Auslöser:<\/strong> VOLUMEN\+REDDIT/);
  assert.match(html,/Volumen · belegt/);assert.match(html,/Zweite Social-Familie · offen/);assert.match(html,/Kurs · offen/);assert.match(html,/1 von 3/);
  assert.match(html,/Squeeze-Merkmal · ja/);assert.match(html,/Existenzrisiko · kein Befund/);assert.match(html,/Bilanz · nur Information/);
  assert.match(html,/StockTwits: 12 Nachrichten\/h von 7 Konten/);assert.match(html,/AUSLÖSER · wird gemessen/);
  assert.doesNotMatch(html,/Autorenkonten|Community-Messkriterien|Belegscore/);
  const blocked=context.renderPulsarCard({symbol:'B',name:'B',sources:[],hype:{existence_risk:{blocked:true,findings:[]},squeeze:{flag:null}}},0);
  assert.match(blocked,/Existenzrisiko · BLOCKIERT/);assert.match(blocked,/Squeeze-Merkmal · unbekannt/);assert.match(blocked,/Kein Auslöser/);
});

test('PULSAR measurement panel renders verdict, horizons and groups without inventing numbers',()=>{
  const {context,nodes}=environment(['ai_diagnostics.js','pulsar.js']);
  const m={total:3,status_counts:{COMPLETE:1,OPEN:2},primary_horizon_days:5,verdict:{status:'ZU_WENIG_DATEN',reason:'1 von 30 vollstaendigen 5-Tage-Messungen',rule:'Urteil erst ab 30'},
    horizons:{'1':{n:1,hit_rate:1,median:0.02,median_after_costs:0.013,worst:0.02,best:0.02},'5':{n:0,hit_rate:null,median:null,median_after_costs:null,worst:null,best:null}},
    by_trigger:{'VOLUMEN':{total:2,'5':{n:0,hit_rate:null,median_after_costs:null}}},by_squeeze:{},by_eligible:{},detail:'Papiermessung'};
  const html=context.renderMeasurement(m,{closed:2,wins:1,losses:1,open:0,net_unknown:0,net_by_currency:{USD:12.5},detail:'Demo'});
  assert.match(html,/<strong>3 Auslöser gemessen<\/strong> · vollständig 1 · laufend 2 · ohne Kurs 0/);assert.match(html,/1 Handelstag<\/th><td>1<\/td><td>100 %<\/td><td>2,0 %<\/td><td>1,3 %/);
  assert.match(html,/5 Handelstage<\/th><td>0<\/td><td>unbekannt<\/td><td>unbekannt/);
  assert.match(html,/VOLUMEN<\/th><td>2 \(0 vollständig\)/);assert.match(html,/Demo-Trades:<\/strong> 2 abgeschlossen \(1 Gewinn \/ 1 Verlust\)/);assert.match(html,/12,5 USD/);
  assert.equal(nodes.get('#p-verdict').className,'verdict verdict-none');assert.equal(nodes.get('#p-verdict').textContent,'ZU WENIG DATEN');
  context.renderMeasurement({total:40,verdict:{status:'ERFOLGREICH',reason:'x'},horizons:{},status_counts:{}},null);
  assert.equal(nodes.get('#p-verdict').className,'verdict verdict-ok');
  assert.match(context.measurementRows([]),/Noch keine Messungen/);
  assert.match(context.measurementRows([{symbol:'GME',trigger_day:'2026-09-18',trigger_kind:'REDDIT',price_at:23.5,eligible:1,squeeze:null,r1:null,r5:0.031,status:'PARTIAL'}]),/GME<\/td><td>2026-09-18<\/td><td>REDDIT<\/td><td>23,5<\/td><td>ja<\/td><td>unbekannt<\/td><td>unbekannt<\/td><td>unbekannt<\/td><td>3,1 %/);
});

test('diagnosis list offers the report page only for finished ZIPs and the report page renders findings, runtime and measurement',async()=>{
  const {context,nodes}=environment(['diagnosis.js']);
  context.api=async()=>({busy:false,telegram_available:false,output_directory:'/reports',jobs:[
    {id:'b'.repeat(32),status:'COMPLETED',mode:'30min',created_at:1,telegram_status:'NOT_REQUESTED',download_url:'/api/diagnosis/'+'b'.repeat(32)+'/download',completion:'COMPLETED'},
    {id:'c'.repeat(32),status:'RUNNING',phase:'OBSERVATION',mode:'30min',created_at:1,observation_started_at:Date.now()/1000-60,requested_seconds:1800,telegram_status:'NOT_REQUESTED'}]});
  await context.loadDiagnosis();
  const html=nodes.get('#diagnosis-jobs').innerHTML;
  assert.match(html,new RegExp('href="/diagnosis/'+'b'.repeat(32)+'/bericht" target="_blank"'));
  assert.doesNotMatch(html,new RegExp('/diagnosis/'+'c'.repeat(32)+'/bericht'));
  const report=environment(['diagnosis_report.js']);
  report.context.document.querySelector=s=>s==='meta[name="diagnosis-id"]'?{content:'b'.repeat(32)}:report.nodes.get?.(s)||(report.nodes.set(s,{value:'',innerHTML:'',textContent:'',className:'',href:''}),report.nodes.get(s));
  report.context.api=async url=>{assert.equal(url,'/api/diagnosis/'+'b'.repeat(32)+'/summary');return {job:{mode:'30min',created_at:1},archive:{name:'NEXUS_10_Diagnose_x.zip',size:1048576,members:12},report_markdown:'# Bericht',
    summary:{completion:'COMPLETED_WITH_EXPORT_ERRORS',observation_mode:'TIMED_OBSERVATION',observed_seconds:1800,sample_count:30,started_utc:'a',ended_utc:'b',tool_version:'1.9.0',
      runtime:{okx:{freshness:'FRESH',state:'RUNNING',buys_allowed:false,buy_reason:'Reserve',tradable_capital:85000,capital_currency:'EUR'},etoro:{freshness:'UNKNOWN'}},
      decisions:{rows_in_window:12,block_reasons:{RISK_GATE:9,'<b>x</b>':1},export_status:'OK'},gpt:{local_dispatches_in_window:2,successful_results_in_window:1},
      pulsar:{cards:[{symbol:'GME',state:'AUSLOESER',eligible:false,blocks:[]}],measurement:{total:5,status_counts:{OPEN:5},horizons:{'5':{n:0}},verdict:{status:'ZU_WENIG_DATEN',reason:'0 von 30'},meaning:'Papier'}},
      sources:[{provider:'FMP',state:'ok'}],database_exports:{'decision_history.sqlite':'OK'},
      findings:{counts:{WARN:1,INFO:1},items:[{component:'okx',level:'WARN',code:'RISK_MANAGER_BUY_BLOCK',meaning:'gesperrt'},{component:'PULSAR',level:'INFO',code:'PULSAR_MEASUREMENT_ZU_WENIG_DATEN',meaning:'<i>m</i>'}]},errors:[{type:'OSError',message:'x'}],limits:['L1']}};};
  await report.context.loadReport();
  const n=report.nodes;
  assert.match(n.get('#r-kpis').innerHTML,/Abgeschlossen mit Exportfehlern/);assert.match(n.get('#r-kpis').innerHTML,/30 min/);
  assert.match(n.get('#r-runtime').innerHTML,/OKX<\/th><td>FRESH<\/td><td>RUNNING<\/td><td><span class="status warn">GESPERRT/);assert.match(n.get('#r-runtime').innerHTML,/ETORO<\/th><td>UNKNOWN<\/td><td>unbekannt<\/td><td><span class="status">UNBEKANNT/);
  assert.match(n.get('#r-findings').innerHTML,/Warnung \(1\)/);assert.match(n.get('#r-findings').innerHTML,/RISK_MANAGER_BUY_BLOCK/);assert.match(n.get('#r-findings').innerHTML,/&lt;i&gt;m&lt;\/i&gt;/);assert.match(n.get('#r-findings').innerHTML,/OSError/);
  assert.match(n.get('#r-decisions').innerHTML,/RISK_GATE<\/th><td>9/);assert.match(n.get('#r-decisions').innerHTML,/&lt;b&gt;x&lt;\/b&gt;/);
  assert.match(n.get('#r-measurement').innerHTML,/<strong>5 Auslöser gemessen<\/strong>/);assert.equal(n.get('#r-verdict').textContent,'ZU WENIG DATEN');
  assert.match(n.get('#r-sources').innerHTML,/FMP · ok/);assert.match(n.get('#r-sources').innerHTML,/GME<\/td><td>AUSLOESER<\/td><td>nein/);
  assert.equal(n.get('#r-report').textContent,'# Bericht');assert.equal(n.get('#r-download').href,'/api/diagnosis/'+'b'.repeat(32)+'/download');
});

test('decision history missing paper flag does not claim LIVE',()=>{
  const {context}=environment(['logbook.js']);
  assert.match(context.execution({}),/Umgebung unbekannt/);
  assert.match(context.execution({paper:false}),/LIVE/);
  assert.match(context.execution({paper:true}),/DEMO/);
});

test('universe favourite filter keeps only observed favourites and exposes check time',()=>{
  const {context,nodes}=environment(['universe.js']);
  context.document.querySelector('#f-art').value='favorit';
  const rows=context.filtern([{symbol:'A',favorit:true},{symbol:'B',favorit:false}]);
  assert.deepEqual(Array.from(rows,x=>x.symbol),['A']);
  const html=context.tabelle([{symbol:'A',zustand:'AKTIV',score:null,letzte_pruefung:'2026-09-13T10:00:00Z'}]);
  assert.match(html,/Letzte Prüfung/);assert.match(html,/unbekannt/);
});

test('uncertain analysis process blocks new runs and explains bounded termination',()=>{
  const {context,nodes,run}=environment(['analysis.js']);
  run("analyseDaten={blocked:true,running:false,recent:[],tasks:[{id:'backtest',group:'Tests',label:'Backtest',description:'offline',status:'UNKNOWN'}]}");
  context.zeichneZusammenfassung();context.zeichneGruppen();
  assert.match(nodes.get('#analysis-summary').innerHTML,/PRÜFUNG ERFORDERLICH/);
  assert.match(nodes.get('#analysis-groups').innerHTML,/data-task="backtest" disabled/);
  assert.equal(run("statusText('TIMED_OUT')"),'Zeitlimit erreicht');
  assert.equal(run("statusText('OUTPUT_LIMIT')"),'Ausgabelimit erreicht');
});

test('eToro cash preview never books until explicit checkbox confirmation',async()=>{
  const {context,nodes}=environment(['trades.js']);
  const dialog=nodes.get('#etoro-settlement-dialog')||{innerHTML:''};
  const fields=new Map();
  const child=key=>{if(!fields.has(key))fields.set(key,{checked:false,disabled:true,textContent:''});return fields.get(key);};
  dialog.querySelector=child;dialog.showModal=()=>{};dialog.close=()=>{};
  const original=context.document.querySelector;
  context.document.querySelector=key=>key==='#etoro-settlement-dialog'?dialog:original(key);
  const preview={symbol:'<PEP>',position_id:'888',account:'account-one',paper:true,token:'receipt',
    before:{observed_at:'2026-09-14T13:30:00Z',cash_usd:'1000'},after:{observed_at:'2026-09-14T13:33:00Z',cash_usd:'1419'},
    proposed_exit_cost:'1',entry_cost:'1',proposed_net:'18',gross_proceeds:'420',cash_delta:'419',completed:false};
  const calls=[];
  context.api=async(url,options)=>{calls.push({url,options});return options?{detail:'Protokolliert'}:preview;};
  context.ladeTrades=async()=>{};
  await context.zeigeEtoroAbrechnung(54);
  assert.equal(calls.length,1);assert.equal(calls[0].options,undefined);
  assert.match(dialog.innerHTML,/&lt;PEP&gt;/);assert.match(dialog.innerHTML,/keine weiteren Geldbewegungen/);
  const check=child('#etoro-cash-confirm'),button=child('#etoro-settlement-save');
  assert.equal(button.disabled,true);
  check.checked=true;check.onchange();assert.equal(button.disabled,false);
  await button.onclick();
  assert.deepEqual(JSON.parse(calls[1].options.body),{token:'receipt',no_other_cashflows:true});
  assert.equal(calls[1].url,'/api/etoro/settlement/54');
  assert.equal(child('#etoro-settlement-message').textContent,'Protokolliert');
});

test('candle chart (10.4.0) requests the chosen timeframe, renders bar buttons, markers and axis note without ECharts',async()=>{
  const {context,nodes,run}=environment(['trades.js']);
  const calls=[];
  const candles=[];
  for(let i=0;i<30;i++){const t=new Date(Date.UTC(2026,8,17,10+i,0,0)).toISOString();candles.push({zeit:t,open:180+i*0.1,high:180.6+i*0.1,low:179.6+i*0.1,close:180.3+i*0.1,volume:1000+i});}
  const chart={ok:true,trade_id:7,instrument:'GOOGL',bar:'1d',bar_seconds:86400,available_bars:['15m','1h','1d'],
    axis:{min:179.5,max:183.5,clipped:2},candles,kauf:{zeit:candles[10].zeit,preis:181.3},verkauf:{zeit:candles[20].zeit,preis:182.4},
    stop:176.0,take_profit:null,active_roi_price:null,active_roi_pct:null,current_price:null,current_source:'',
    letzter_schluss:183.2,waehrung:'USD',entry_strategy_mode:'NEXUS_STANDARD',display_context:{broker:'etoro',environment:'DEMO'},
    data_source:'eToro-Historie (Kern-Snapshot)',data_age_seconds:420};
  context.api=async url=>{calls.push(url);return chart;};
  // let-Bindungen des Skripts (tradeDaten, ausgewaehlt) sind keine Kontext-Eigenschaften: nur per run() erreichbar.
  run("tradeDaten={offene_trades:[{trade_id:7,broker:'etoro',symbol:'GOOGL'}],geschlossene_trades:[],klaerungs_trades:[]}");
  await context.ladeChart(7,'1d');
  assert.equal(calls[0],'/api/trades/7/candles?bar=1d');
  assert.equal(run('ausgewaehlt'),7);
  const svg=nodes.get('#trade-chart').innerHTML;
  assert.match(svg,/<svg/);assert.match(svg,/KAUF 181,3/);assert.match(svg,/VERKAUF 182,4/);assert.match(svg,/Stop-Loss/);
  assert.match(nodes.get('#trade-chart-bars').innerHTML,/data-bar="15m"/);
  assert.match(nodes.get('#trade-chart-bars').innerHTML,/data-bar="1d" aria-pressed="true"/);
  assert.match(nodes.get('#trade-chart-bars').innerHTML,/Stand vor 7 min/);
  assert.match(nodes.get('#trade-chart-axisnote').textContent,/2 Docht-Ausreißer/);
  assert.match(nodes.get('#trade-chart-axisnote').textContent,/30 Kerzen geladen/);
  assert.match(nodes.get('#trade-chart-title').textContent,/eToro · Aktien · DEMO · GOOGL · Trade 7/);
  assert.match(nodes.get('#trade-chart-info').textContent,/1d-Kerzen · nur abgeschlossen · letzter Schluss 183,20 USD/);
  // Stille Aktualisierung desselben Trades fragt ohne neuen Zeitrahmen weiter mit dem gewaehlten ab.
  await context.ladeChart(7);
  assert.equal(calls[1],'/api/trades/7/candles?bar=1d');
  // Ein anderer Trade beginnt wieder mit der Automatik des Servers.
  await context.ladeChart(9);
  assert.equal(calls[2],'/api/trades/9/candles');
  // Fehlerantwort: kein Zeitrahmen, klare Meldung, keine Ausnahme.
  context.api=async()=>({ok:false,fehler:'Noch keine eToro-Kerzen'});
  await context.ladeChart(11);
  assert.match(nodes.get('#trade-chart').innerHTML,/Noch keine eToro-Kerzen/);
  assert.equal(nodes.get('#trade-chart-bars').innerHTML,'');
  assert.equal(nodes.get('#trade-chart-mode').textContent,'KEINE KERZEN');
});

test('risk levels (10.6.0) show the effective numbers per broker and never invent a chosen level',()=>{
  const {context,nodes}=environment(['settings.js']);
  const okxOptionen=[
    {level:'vorsichtig',label:'Vorsichtig',beschreibung:'Der bisherige Wert.',risiko_pro_trade_pct:0.003,max_position_pct:0.05,aktiv:false},
    {level:'mittel',label:'Mittel',beschreibung:'Doppelter Einsatz.',risiko_pro_trade_pct:0.006,max_position_pct:0.10,aktiv:true},
    {level:'erhoeht',label:'Erhoeht',beschreibung:'Vierfacher Einsatz.',risiko_pro_trade_pct:0.012,max_position_pct:0.20,aktiv:false}];
  context.renderRiskLevels({
    okx:{broker:'okx',level:'mittel',chosen:true,error:'',updated_at_utc:'2026-09-18T12:30:00+00:00',
      bezugsgroesse:'freies Guthaben der Waehrung, in der gekauft wird',
      wirksam:{label:'Mittel',risiko_pro_trade_pct:0.006,max_position_pct:0.10,quelle:'stufe'},options:okxOptionen},
    etoro:{broker:'etoro',level:'',chosen:false,error:'',updated_at_utc:'',
      bezugsgroesse:'Kontowert des eToro-Kontos',
      wirksam:{label:'Vorgabe (keine Stufe gewaehlt)',risiko_pro_trade_pct:0.02,max_position_pct:0.15,quelle:'vorgabe'},
      options:[{level:'vorsichtig',label:'Vorsichtig',beschreibung:'Klein.',risiko_pro_trade_pct:0.005,max_position_pct:0.03,aktiv:false}]}});
  const html=nodes.get('#risk-levels').innerHTML;
  // Gewaehlte Stufe: wirksame Zahlen, Bezugsgroesse und Zeitpunkt der Wahl.
  assert.match(html,/OKX · Krypto/);assert.match(html,/Wirksam: <strong>Mittel<\/strong> · 0,60 % Risiko je Trade · höchstens 10 % je Position/);
  assert.match(html,/freies Guthaben der Waehrung/);assert.match(html,/Gewählt am 2026-09-18 12:30 UTC/);
  // Ohne Wahl wird keine Stufe behauptet, aber der wirksame Wert genannt.
  assert.match(html,/Noch keine Stufe gewählt · es gilt die bisherige Vorgabe/);
  assert.match(html,/Wirksam: <strong>Vorgabe \(keine Stufe gewaehlt\)<\/strong> · 2,00 % Risiko je Trade/);
  // Karten: aktive Stufe markiert, hoechste Stufe als Gefahr, Beschreibung vorhanden.
  assert.match(html,/class="profile selected"><h3>Mittel<\/h3>/);
  assert.match(html,/<button class="danger" onclick="setRiskLevel\('okx','erhoeht'\)">Diese Stufe wählen<\/button>/);
  assert.match(html,/<button class="secondary" onclick="setRiskLevel\('okx','mittel'\)">Aktiv<\/button>/);
  assert.match(html,/Doppelter Einsatz\./);
  assert.doesNotMatch(html,/NaN|undefined/);
  // Fail-safe wird sichtbar gemacht, nicht verschwiegen.
  context.renderRiskLevels({okx:{broker:'okx',level:'vorsichtig',chosen:false,error:'Stufendatei unlesbar: JSONDecodeError',
    bezugsgroesse:'x',wirksam:{label:'Vorsichtig',risiko_pro_trade_pct:0.003,max_position_pct:0.05,quelle:'fail-safe'},options:[]}});
  assert.match(nodes.get('#risk-levels').innerHTML,/Stufendatei nicht verwendbar: Stufendatei unlesbar: JSONDecodeError · Es gilt vorsichtshalber die kleinste Stufe\./);
  // Ein Modulfehler ersetzt die Liste durch eine Meldung statt leerer Flaeche.
  context.renderRiskLevels({error:'Einsatzstufen nicht lesbar: OSError'});
  assert.match(nodes.get('#risk-levels').innerHTML,/Einsatzstufen nicht lesbar: OSError/);
});