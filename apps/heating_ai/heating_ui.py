"""Install public frontend assets; never expose controller settings or credentials."""
import hashlib
import json
import os
import math
from pathlib import Path

from heating_ui_assets import DIGEST, SOURCE, VERSION

ENTITY_KEYS = (
    'room', 'return', 'outside', 'fan', 'weather', 'energy', 'target', 'lower', 'upper',
    'night_target', 'night_lower', 'night_upper', 'sleep_start', 'sleep_end', 'start', 'end',
    'monthly_budget', 'session_limit', 'native_setpoint', 'mode', 'strategy', 'armed',
    'restore_once', 'fan_control', 'fan_mode', 'fan_speed', 'fan_night_limit', 'fan_intense_speed',
    'ai_auto_apply', 'rollback', 'fan_backup',
)


def project_fireplace(stamp,delta,active):
    """Display the planner's existing exp(-elapsed/7200) heat signal, not future burns."""
    if not active or delta is None or not math.isfinite(delta) or delta<=2:
        return []
    return [dict(start=stamp,end=stamp+min(86400,7200*math.log(delta/2)),estimated=True)]


def verify_installation(directory=None):
    """Reject incomplete HACS file replacement before any state or device changes."""
    root=Path(directory) if directory is not None else Path(__file__).resolve().parent
    manifest=json.loads((root/'release.json').read_text())
    modules={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('heating_*.py')}
    if manifest.get('version')!=VERSION or len(modules)!=9 or modules!=manifest.get('module_hashes'):
        raise ValueError('Heating AI release is incomplete or has mixed module versions')
    return modules


def _write_changed(path, content):
    if path.exists() and path.read_text() == content:
        return
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(content)
    os.replace(temporary, path)


def install_panel(cfg):
    """Return runtime proof and a small entity map; errors must not stop heating."""
    if not cfg.get('ui_enabled', True):
        return None
    modules=verify_installation()
    url = cfg.get('ui_url', '/heating-ai')
    if not isinstance(url, str) or not url.startswith('/') or url.startswith('//'):
        raise ValueError('ui_url must be a local Home Assistant path')
    directory = Path(cfg.get('ui_www_dir', '/homeassistant/www/heating-ai'))
    # Do not create /homeassistant when a container does not have this mount.
    if not directory.exists() and not directory.parent.exists():
        if directory.parent.name != 'www' or not directory.parent.parent.is_dir():
            raise FileNotFoundError('Home Assistant www mount missing; configure ui_www_dir')
        directory.parent.mkdir()
    directory.mkdir(exist_ok=True)
    name = f'heating-panel-{DIGEST}.js'
    _write_changed(directory/name, SOURCE)
    _write_changed(directory/'version.json', json.dumps({'module':name,'version':VERSION}))
    # This small stable loader revalidates the version after each page reload.
    _write_changed(directory/'loader.js', "const root=new URL('.',import.meta.url);\n"
                   "const response=await fetch(new URL('version.json',root),{cache:'no-store'});\n"
                   "if(!response.ok)throw new Error('Heating AI version unavailable');\n"
                   "const {module}=await response.json();\n"
                   "if(!/^heating-panel-[a-f0-9]{16}\\.js$/.test(module))throw new Error('Invalid Heating AI module');\n"
                   "await import(new URL(module,root));\n")
    entities={key:cfg[key] for key in ENTITY_KEYS if isinstance(cfg.get(key),str) and '.' in cfg[key]}
    entities.setdefault('fan_backup','input_boolean.heating_fan_backup_verified')
    entities.setdefault('fan_intense_speed','input_number.heating_fan_intense_speed')
    return {'version': VERSION, 'ui_url': url, 'asset_digest': DIGEST,
            'source_directory': str(Path(__file__).resolve().parent),
            'module_hashes': modules, 'release_verified': True,
            'entities': entities}
