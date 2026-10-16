"""Install public frontend assets; never expose controller settings or credentials."""
import hashlib
import json
import os
from pathlib import Path

from heating_ui_assets import DIGEST, SOURCE, VERSION

ENTITY_KEYS = (
    'room', 'return', 'outside', 'fan', 'weather', 'energy', 'target', 'lower', 'upper',
    'night_target', 'night_lower', 'night_upper', 'sleep_start', 'sleep_end', 'start', 'end',
    'monthly_budget', 'session_limit', 'native_setpoint', 'mode', 'strategy', 'armed',
    'restore_once', 'fan_control', 'fan_mode', 'fan_speed', 'fan_night_limit',
    'ai_auto_apply', 'rollback',
)


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
    modules = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
               for p in Path(__file__).parent.glob('heating_*.py')}
    return {'version': VERSION, 'ui_url': url, 'asset_digest': DIGEST,
            'source_directory': str(Path(__file__).resolve().parent),
            'module_hashes': modules,
            'entities': {key:cfg[key] for key in ENTITY_KEYS
                         if isinstance(cfg.get(key), str) and '.' in cfg[key]}}
