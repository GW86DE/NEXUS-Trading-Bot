"""Only called by an installer with the user's explicit Starter selection."""
from pathlib import Path
from credential_store import load_credentials, save_credentials


def starter(root):
    path=Path(root)/'news_sources_credentials.json'
    data=load_credentials(path,{})
    if not isinstance(data,dict):raise ValueError('FMP-Einstellungen unlesbar')
    data['fmp_plan']='AUTO'
    data['fmp_subscription']='STARTER'
    # The user requested full use of their existing FMP subscription.
    enabled=dict(data.get('enabled') or {})
    enabled['fmp']=True
    data['enabled']=enabled
    save_credentials(path,data)
