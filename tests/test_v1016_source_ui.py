import json
from pathlib import Path
import subprocess


def test_candidate_research_renderer_explains_unknown_and_escapes_input():
    root=Path(__file__).resolve().parents[1]
    code="""const fs=require('fs'),vm=require('vm');
    const c={document:{querySelector:()=>null},nexusPoll:()=>{},
      esc:v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])),
      knownNumber:v=>typeof v==='number'&&Number.isFinite(v),observationTime:v=>v??'nicht belegt',badge:v=>String(v)};
    vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
    const html=vm.runInContext(`renderXCandidateResearch({state:'OK',candidate_research:{candidates:[
        {symbol:'PEP',company_name:'<script>attack</script>',state:'NO_MATCHING_SAMPLE',sentiment:'UNKNOWN',
        usable_posts:0,query:'($PEP) -is:retweet',evidence:[]}]}})`,c);
    if(html.includes('<script>')||!html.includes('&lt;script&gt;')||!html.includes('Unbekannt')||!html.includes('Keine auswertbaren Treffer')||!html.includes('($PEP) -is:retweet')||!html.includes('keine Kauf-'))process.exit(1);
    """
    p=subprocess.run(['node','-e',code,str(root/'webui/static/sources.js')],text=True,capture_output=True,timeout=10)
    assert p.returncode==0,p.stderr
    for name in ['pulsar','diagnosis','sources']:
        html=(root/'webui/templates'/f'{name}.html').read_text()
        assert html.count('id="x-candidate-research"')==1
        assert '/static/sources.js' in html
