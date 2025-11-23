import sys
import os
import traceback
from pathlib import Path
import importlib.util
import importlib.machinery
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

root = Path('.').resolve()
sys.path.insert(0, str(root))

# Load .env from project root if present so real env vars are available.
try:
    from dotenv import load_dotenv
    load_dotenv(root / '.env')
except Exception:
    pass

# If real BOT_TOKEN and DATABASE_URL are set, prefer using the project's
# `config.py`. Otherwise inject a safe stub to avoid runtime side-effects.
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')
if not (BOT_TOKEN and DATABASE_URL):
    # Inject a safe `config` module so imports of `config` don't execute runtime setup.
    import types
    safe_cfg = types.ModuleType('config')
    safe_cfg.BOT_TOKEN = os.environ.get('BOT_TOKEN')
    safe_cfg.bot = None
    safe_cfg.ADMIN_IDS = [1877127405, 1059714785, 1078562089, 6446544048]
    safe_cfg.DOC_CONFIG = {
        "writeoff": {"stores": {"Бар": ["Списание бар порча"], "Кухня": ["Списание кухня порча"]}},
        "internal_transfer": {"stores": ["Бар", "Кухня"]},
    }
    safe_cfg.PARENT_FILTERS = [
        '4d2a8e1d-7c24-4df1-a8bd-58a6e2e82a12',
        '6c5f1595-ce55-459d-b368-94bab2f20ee3'
    ]
    safe_cfg.STORE_NAME_MAP = {"Бар": ["Бар Пиццерия"], "Кухня": ["Кухня Пиццерия"]}

    def _get_bot_placeholder(token=None):
        raise RuntimeError('get_bot() unavailable during static import-check')

    safe_cfg.get_bot = _get_bot_placeholder
    sys.modules['config'] = safe_cfg

SKIP_FILES = {'import_check.py', 'import_check_run.py', 'main.py', 'webhook.py', 'bot.py'}
py_files = [p for p in root.rglob('*.py') if '__pycache__' not in p.parts and p.name not in SKIP_FILES]
failures = []

logger.info("Found %d python files to check", len(py_files))
for p in sorted(py_files):
    rel = p.relative_to(root)
    mod_name = '.'.join(rel.with_suffix('').parts)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, p)
        mod = importlib.util.module_from_spec(spec)
        # ensure parent packages exist in sys.modules (helps package imports inside modules)
        parts = mod_name.split('.')
        for i in range(1, len(parts)):
            pkg = '.'.join(parts[:i])
            if pkg not in sys.modules:
                pkg_spec = importlib.machinery.ModuleSpec(pkg, None)
                pkg_mod = importlib.util.module_from_spec(pkg_spec)
                pkg_mod.__path__ = [str(root.joinpath(*parts[:i]))]
                sys.modules[pkg] = pkg_mod

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
