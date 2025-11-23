import sys
import traceback
from pathlib import Path
import importlib.util
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

root = Path('.').resolve()
py_files = [p for p in root.rglob('*.py') if '__pycache__' not in p.parts and p.name != 'import_check.py']
failures = []

logger.info("Found %d python files to check", len(py_files))
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
logger.info("Checked %d files. Failures: %d", len(py_files), len(failures))
if failures:
    for path, tb in failures:
        logger.error('\n' + '='*60)
        logger.error(path)
        logger.error(tb)
    sys.exit(2)
else:
    logger.info('All imports OK')
    sys.exit(0)
