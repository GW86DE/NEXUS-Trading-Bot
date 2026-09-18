from __future__ import annotations
import sys, threading
from credential_store import load_credentials, save_credentials
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
ROOT=Path(__file__).resolve().parent; CREDS=ROOT/'etoro_credentials.json'
def _load(): return load_credentials(CREDS,{})
def _save(d): save_credentials(CREDS,d)
def _test(paper,api_key,user_key):
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from broker.etoro import EtoroBroker
    b=EtoroBroker(paper=paper,api_key=api_key,user_key=user_key)
    try:return b.diagnose_credentials()
    finally:b.disconnect()
def main():
    d=_load(); root=tk.Tk(); root.title('eToro Public API einrichten · TradingBot 8.1.1 NEXUS'); root.geometry('790x660'); root.configure(bg='#0b1220')
    fg='#e5e7eb'; muted='#94a3b8'; panel='#111827'; green='#22c55e'
    tk.Label(root,text='eToro Public API',bg='#0b1220',fg=fg,font=('Segoe UI',20,'bold')).pack(anchor='w',padx=24,pady=(22,2))
    tk.Label(root,text='Der Test ist read-only und prueft getrennt: Identitaet → Demo/Live-Konto → P&L-Lebensbit → Portfolio. Es wird KEINE Order gesendet.',bg='#0b1220',fg=muted,wraplength=720,justify='left').pack(anchor='w',padx=24,pady=(0,16))
    entries={}; box=tk.Frame(root,bg=panel); box.pack(fill='x',padx=24,pady=4)
    for key,label in [('demo_api_key','DEMO · x-api-key'),('demo_user_key','DEMO · x-user-key'),('live_api_key','LIVE · x-api-key'),('live_user_key','LIVE · x-user-key')]:
        row=tk.Frame(box,bg=panel); row.pack(fill='x',padx=16,pady=7); tk.Label(row,text=label,bg=panel,fg=fg,width=22,anchor='w',font=('Segoe UI',10,'bold')).pack(side='left')
        e=tk.Entry(row,show='•',font=('Consolas',10)); e.pack(side='left',fill='x',expand=True); e.insert(0,str(d.get(key,''))); entries[key]=e
    tk.Label(box,text='Wichtig: x-api-key = Public API Key der Anwendung, x-user-key = dein benutzerspezifischer Key. Demo und Real benoetigen passende Scopes.',bg=panel,fg=muted,wraplength=700,justify='left').pack(anchor='w',padx=16,pady=(4,12))
    status=tk.StringVar(value='Noch nicht getestet'); tk.Label(root,textvariable=status,bg='#0b1220',fg=fg,font=('Segoe UI',10,'bold'),wraplength=730,justify='left').pack(anchor='w',padx=24,pady=(14,8))
    def values():return {k:e.get().strip() for k,e in entries.items()}
    def save():_save(values());status.set('Gespeichert.');messagebox.showinfo('eToro','Zugangsdaten gespeichert. Vor echten Orders zuerst DEMO testen.')
    def test(paper):
        v=values(); a=v['demo_api_key' if paper else 'live_api_key']; u=v['demo_user_key' if paper else 'live_user_key']
        if not a or not u:return messagebox.showwarning('eToro','Fuer diesen Modus fehlen x-api-key oder x-user-key.')
        status.set('Verbindung wird getestet …');
        def worker():
            try:
                x=_test(paper,a,u); scopes=', '.join(x.get('scopes') or []) or 'nicht gemeldet'
                warn=("\nHinweis: "+x.get('scope_warning','')) if x.get('scope_warning') else ''
                step_txt=" · ".join(("✅ " if st.get("ok") else "⚠️ ")+str(st.get("step")) for st in x.get("steps",[]))
                msg=(f"✅ {x['environment']} vollstaendig lesbar · Benutzer {x.get('username') or '?'} · "
                     f"CID {x.get('selectedCid') or '?'} · Equity {x['equity']:,.2f} {x['currency']} · Cash {x['cash']:,.2f}\n"
                     f"Pruefschritte: {step_txt}\nScopes: {scopes}{warn}")
                root.after(0,lambda:status.set(msg))
            except Exception as exc:
                msg=str(exc); root.after(0,lambda:status.set('❌ '+msg)); root.after(0,lambda:messagebox.showerror('eToro Verbindungstest',msg))
        threading.Thread(target=worker,daemon=True).start()
    buttons=tk.Frame(root,bg='#0b1220'); buttons.pack(fill='x',padx=24,pady=10)
    for text,cmd,color in [('Speichern',save,'#2563eb'),('DEMO testen',lambda:test(True),green),('LIVE nur lesen/testen',lambda:test(False),'#b45309')]:
        tk.Button(buttons,text=text,command=cmd,bg=color,fg='white',relief='flat',padx=16,pady=10,font=('Segoe UI',10,'bold')).pack(side='left',padx=(0,10))
    info='''FEHLERHILFE\n401/403 bedeutet meist falsches Key-Paar, falsche Demo/Real-Umgebung oder fehlenden Read-Scope. Der Test sendet KEINE Order.\n\nKOSTEN\nVor qualifizierten eToro-Kaeufen nutzt der Bot weiterhin den offiziellen What-if-Kostenendpunkt und blockiert den Kauf, wenn keine belastbare Kostenquote verfuegbar ist.'''
    tk.Label(root,text=info,bg=panel,fg=muted,font=('Segoe UI',9),wraplength=700,justify='left',padx=16,pady=14).pack(fill='x',padx=24,pady=12)
    root.mainloop()
if __name__=='__main__':main()
