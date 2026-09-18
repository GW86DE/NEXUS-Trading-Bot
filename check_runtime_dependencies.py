"""Check actual release-pinned runtime packages without importing broker code."""
from importlib import import_module, metadata
from pathlib import Path
import sys


def check():
    mapping={'scikit-learn':'sklearn','python-multipart':'python_multipart','websocket-client':'websocket'}
    errors=[]
    for line in (Path(__file__).resolve().parent/'requirements-lock.txt').read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#'):
            continue
        name,wanted=line.split('==',1)
        try:
            actual=metadata.version(name)
            if actual!=wanted:
                errors.append(f'{name}: installiert {actual}, erwartet {wanted}')
            import_module(mapping.get(name,name.replace('-','_')))
        except Exception as exc:
            errors.append(f'{name}: Import nicht erfolgreich ({type(exc).__name__})')
    if errors:
        print('\n'.join(errors)); return 1
    print('LAUFZEITABHAENGIGKEITEN OK (echte Imports, Release-Pins)')
    return 0


if __name__=='__main__':
    sys.exit(check())
