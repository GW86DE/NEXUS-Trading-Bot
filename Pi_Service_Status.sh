#!/usr/bin/env bash
set -euo pipefail
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
echo "=== systemd ==="
sudo systemctl --no-pager --full status tradingbot-pi5.service || true
sudo systemctl --no-pager --full status tradingbot-webui.service || true
echo
echo "=== Dienstpfad dieser Version ==="
if [[ -x "$ROOT/Pi_Service_Unit_Pruefen.sh" ]]; then
  "$ROOT/Pi_Service_Unit_Pruefen.sh" all || true
fi
echo
echo "=== letzte 80 Journal-Zeilen ==="
sudo journalctl -u tradingbot-pi5.service -n 80 --no-pager || true
echo
echo "=== WebUI Journal ==="
sudo journalctl -u tradingbot-webui.service -n 40 --no-pager || true
echo
echo "=== Runtime ==="
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  "$ROOT/.venv/bin/python" - <<'PY'
import json
from pathlib import Path
p=Path('runtime_status.json')
print(json.dumps(json.loads(p.read_text(encoding='utf-8')),indent=2,ensure_ascii=False) if p.exists() else 'runtime_status.json noch nicht vorhanden - eToro-Start/Status noch nicht bestaetigt.')
q=Path('runtime_status_okx.json')
print('\n--- OKX ---')
print(json.dumps(json.loads(q.read_text(encoding='utf-8')),indent=2,ensure_ascii=False) if q.exists() else 'runtime_status_okx.json noch nicht vorhanden - OKX-Start/Status noch nicht bestaetigt.')
PY
else
  echo ".venv fehlt – zuerst ./Pi_Installieren.sh ausfuehren."
fi
