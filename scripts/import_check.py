import sys
import traceback
from pathlib import Path
import importlib.util

root = Path('.').resolve()
py_files = [p for p in root.rglob('*.py') if '__pycache__' not in p.parts and p.name != 'import_check.py']
failures = []

print(f"Found {len(py_files)} python files to check")
for p in sorted(py_files):
    rel = p.relative_to(root)
    mod_name = '.'.join(rel.with_suffix('').parts)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, p)
        mod = importlib.util.module_from_spec(spec)
        # execute module (may run top-level code)
        spec.loader.exec_module(mod)
    except Exception:
        failures.append((str(p), traceback.format_exc()))

print(f"Checked {len(py_files)} files. Failures: {len(failures)}")
if failures:
    for path, tb in failures:
        print('\n' + '='*60)
        print(path)
        print(tb)
    sys.exit(2)
else:
    print('All imports OK')
    sys.exit(0)
