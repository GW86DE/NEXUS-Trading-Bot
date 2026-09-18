/* Native SVG, no remote chart dependency and no broker/API writes. */
(() => {
  let data=null, requestNumber=0;
  const el=id=>document.getElementById('performance-'+id);
  const known=v=>typeof v==='number'&&Number.isFinite(v);
  const num=v=>known(v)?v.toLocaleString('de-DE',{minimumFractionDigits:2,maximumFractionDigits:2}):'nicht bekannt';
  const date=s=>{const parts=s.split('-');return parts[2]+'.'+parts[1]+'.'+parts[0];};
  function metric(title,m,currency) {
    const label=m.complete?'': ' · bestätigter Teil';
    return `<article class="card"><h3>${esc(title+label)}</h3><strong class="performance-value ${known(m.net)?m.net<0?'pnl-loss':'pnl-gain':''}">${esc(num(m.net))} ${esc(currency)}</strong><p>${esc(num(m.percent))}${known(m.percent)?' %':''} auf eingesetztes Kapital</p><p class="small">${m.known} bewertet · ${m.unknown} Ergebnis offen · ${m.wins} Gewinn / ${m.losses} Verlust<br>Bezugsgröße: ${esc(num(m.capital))} ${esc(currency)}</p></article>`;
  }
  function render() {
    const g=data?.groups.find(x=>x.group_key===el('group').value);
    el('chart').replaceChildren();el('metrics').replaceChildren();el('table').replaceChildren();
    el('method').textContent=data?.method||'';
    if(!g){el('status').textContent='Noch keine kontogebundenen Handelsdaten vorhanden.';return;}
    el('status').textContent=`Stand ${new Date(data.as_of).toLocaleString('de-DE')} · Tag: ${data.timezone} · ${g.open_count} offene Positionen nicht eingerechnet${data.excluded_rows?' · '+data.excluded_rows+' Zeilen ohne sichere Zuordnung/Zeit ausgeschlossen':''}`;
    el('metrics').innerHTML=metric('Heute',g.today,g.currency)+metric('Gesamt · seit Aufzeichnungsbeginn',g.total,g.currency);
    const mode=el('mode').value,unit=el('unit').value,suffix=unit==='percent'?'%':g.currency;
    const points=g.series.map(r=>({day:r.day,value:r[mode][unit],metric:r[mode]}));
    const values=points.filter(p=>known(p.value)).map(p=>p.value);
    if(!values.length){el('chart').innerHTML='<p class="alert">Für diese Darstellung fehlen bestätigte Ergebnis- oder Kapitalbelege.</p>';}
    else {
      const width=Math.max(320,Math.min(900,el('chart').clientWidth||840)),height=280,left=85,right=20,top=22,bottom=44;
      let lo=Math.min(0,...values),hi=Math.max(0,...values);if(lo===hi){lo=-1;hi=1;}
      const spread=hi-lo;lo-=spread*.08;hi+=spread*.08;
      const x=i=>left+i*(width-left-right)/Math.max(1,points.length-1);
      const y=v=>top+(hi-v)*(height-top-bottom)/(hi-lo);
      let paths='',ticks='';
      for(let n=0;n<=4;n++){const value=lo+(hi-lo)*n/4;const yy=y(value);ticks+=`<line x1="${left}" x2="${width-right}" y1="${yy}" y2="${yy}" class="performance-grid"/><text x="${left-10}" y="${yy+4}" text-anchor="end">${esc(num(value))}</text>`;}
      let segment=[];
      const flush=()=>{if(segment.length){paths+=`<polyline points="${segment.join(' ')}" class="performance-line"/>`;segment=[];}};
      points.forEach((p,i)=>{if(!known(p.value)){flush();return;}const px=x(i),py=y(p.value),partial=!p.metric.complete,pointLabel=date(p.day)+': '+num(p.value)+' '+suffix+(partial?' · bestätigter Teil; '+p.metric.unknown+' Ergebnis offen':'');
        if(mode==='daily'){paths+=`<line x1="${px}" x2="${px}" y1="${y(0)}" y2="${py}" class="performance-bar ${p.value<0?'loss':'gain'} ${partial?'performance-partial':''}" data-complete="${!partial}"><title>${esc(pointLabel)}</title></line>`;}
        else {segment.push(px+','+py);paths+=`<circle cx="${px}" cy="${py}" r="${partial?4:2}" class="${partial?'performance-partial-point':''}" fill="#78afff"><title>${esc(pointLabel)}</title></circle>`;}
      });flush();
      el('chart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" class="performance-svg" role="img" aria-labelledby="performance-svg-title performance-svg-desc"><title id="performance-svg-title">${esc((mode==='daily'?'Tagesergebnis':'Gesamtergebnis')+' in '+suffix)}</title><desc id="performance-svg-desc">Ergebnisse für ${esc(g.label)}. Bestätigte Teilsummen werden markiert. Unbekannte Ergebnisse sind nicht als null gerechnet. Exakte Werte in der aufklappbaren Tabelle.</desc>${ticks}<line x1="${left}" x2="${width-right}" y1="${y(0)}" y2="${y(0)}" class="performance-zero"/>${paths}<text x="${left}" y="${height-10}">${esc(date(points[0].day))}</text><text x="${width-right}" y="${height-10}" text-anchor="end">${esc(date(points[points.length-1].day))}</text><text x="${left}" y="15">${esc(suffix)}</text></svg>${points.some(p=>!p.metric.complete)?'<p class="small">Markierte Werte zeigen nur den bestätigten Teil. Offene Ergebnisse sind nicht eingerechnet.</p>':''}`;
    }
    el('table').innerHTML=`<table><thead><tr><th>Tag</th><th>Netto ${esc(g.currency)}</th><th>Rendite %</th><th>Unbekannte Ergebnisse</th></tr></thead><tbody>${g.series.slice().reverse().map(r=>`<tr><td>${esc(date(r.day))}</td><td>${esc(num(r.daily.net))}${r.daily.complete?'':' (Teil)'}</td><td>${esc(num(r.daily.percent))}</td><td>${r.daily.unknown}</td></tr>`).join('')}</tbody></table>`;
  }
  async function load(){const seq=++requestNumber;try{
    const next=await api('/api/performance?days='+encodeURIComponent(el('days').value));if(seq!==requestNumber)return;
    const selected=el('group').value;data=next;
    el('group').innerHTML=data.groups.map(g=>`<option value="${esc(g.group_key)}">${esc(g.label+' · '+g.currency)}</option>`).join('');
    if(data.groups.some(g=>g.group_key===selected))el('group').value=selected;render();
  }catch(e){if(seq!==requestNumber)return;data=null;el('chart').replaceChildren();el('metrics').replaceChildren();el('table').replaceChildren();el('status').textContent='Ergebnisdaten nicht aktuell: '+e.message;}}
  ['group','mode','unit'].forEach(id=>el(id).addEventListener('change',render));el('days').addEventListener('change',load);
  window.addEventListener('resize',()=>{if(data)render();});
  nexusPoll(load,60000);
})();
