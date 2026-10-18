"""Install public frontend assets; never expose controller settings or credentials."""
import hashlib
import json
import os
import math
from pathlib import Path

from heating_ui_assets import DIGEST, SOURCE, VERSION
from heating_model import BOUNDS, DEFAULT_MODEL

ENTITY_KEYS = (
    'room', 'return', 'outside', 'fan', 'weather', 'energy', 'target', 'lower', 'upper',
    'night_target', 'night_lower', 'night_upper', 'sleep_start', 'sleep_end', 'start', 'end',
    'monthly_budget', 'session_limit', 'native_setpoint', 'mode', 'strategy', 'armed',
    'restore_once', 'fan_control', 'fan_mode', 'fan_speed', 'fan_night_limit', 'fan_intense_speed',
    'ai_auto_apply', 'rollback', 'fan_backup',
)


# Public allowlist; no paths, credentials, entity remapping or actuation permissions.
_SYSTEM_ROWS = (
    ('outage_max_age','Графік і прогноз','Свіжість даних Svitlo',10800,300,21600,60,'с','Максимальний вік updated. 10800 с = 3 години. Старіші дані дають unknown; фізичний стан мережі перевіряється окремо.'),
    ('outage_advance_limit','Графік і прогноз','Ліміт завчасного нагріву',180,0,360,5,'хв','Максимальне перенесення нагріву перед відключенням поза дозволеним вікном. Застосовується лише за чинними перевірками комфорту й відомої мережі.'),
    ('morning_min_minutes','Графік і прогноз','Мінімальний ранковий сеанс',30,5,30,5,'хв','Мінімальна тривалість ранкового сеансу за наявності потреби в теплі; не змушує котел гріти без потреби.'),
    ('weather_max_age','Графік і прогноз','Свіжість погодинної погоди',10800,300,10800,60,'с','Вік останнього отриманого прогнозу. Після цієї межі використовується чинний резервний розрахунок без погодинних даних.'),
    ('room_max_age','Датчики','Свіжість кімнатного датчика',14400,300,14400,60,'с','Граничний вік heartbeat основного датчика. За перевищення застосовується наявний резерв температури термостата.'),
    ('return_max_age','Датчики','Свіжість датчика обратки',1200,60,1200,60,'с','Після цього віку датчик не вважається придатним для автоматичного керування. Верхня межа залишається 20 хвилин.'),
    ('fireplace_max_age','Датчики','Свіжість датчика каміна',14400,300,14400,60,'с','Застарілий датчик не може створювати автоматичний запит циркуляції.'),
    ('fire_ignite_delta','Камін','Різниця для розпалювання',2,0,5,.1,'°C','Температура каміна мінус температура кімнати. Разом із додатним трендом визначає розпалювання; сама фаза не запускає вентилятор.'),
    ('fire_start_delta','Камін','Запуск корисного тепла',5,2,15,.1,'°C','Від цієї різниці формується запит циркуляції. Має бути вище порогів вимкнення та швидкого згасання.'),
    ('fire_intense_delta','Камін','Інтенсивне горіння',10,5,30,.1,'°C','Від цієї різниці вдень обирається рівень 3, якщо тренд не вказує на згасання.'),
    ('fire_stop_delta','Камін','Різниця для вимкнення',3,1,10,.1,'°C','Нижче цього порога починається безперервна витримка вимкнення. Повернення до порога скидає таймер.'),
    ('fire_fast_stop_delta','Камін','Різниця швидкого вимкнення',4,1,15,.1,'°C','Нижче цього перепаду та за сильного негативного тренду запит знімається без витримки.'),
    ('fire_ignite_slope','Камін','Тренд розпалювання',.8,0,5,.1,'°C/год','Тренд має бути більшим за це значення, щоб позначити розпалювання за достатньої різниці температур.'),
    ('fire_fade_slope','Камін','Тренд згасання',-.8,-5,0,.1,'°C/год','Тренд нижче цього значення позначає згасання й повертає денну інтенсивну швидкість до стандартної.'),
    ('fire_fast_stop_slope','Камін','Сильне охолодження',-2,-10,-.1,.1,'°C/год','Тренд не вище цієї межі разом із низькою різницею температур знімає запит одразу.'),
    ('fire_cooldown_seconds','Камін','Витримка вимкнення',300,60,600,15,'с','Безперервний час нижче порога вимкнення; 300 с = 5 хвилин. Прогалини вимірювань не зараховуються.'),
    ('fire_trend_seconds','Камін','Вікно оцінки тренду',1800,300,3600,60,'с','Глибина історії різниці температур для оцінки тренду. При короткій історії зберігається чинне згладжування.'),
    ('fire_continuity_seconds','Камін','Допустима перерва вимірів',90,30,90,15,'с','Більша перерва скидає відлік безперервного низького перепаду. Не змінює період controller tick.'),
)
SYSTEM_FIELDS = [dict(key=k,group=g,label=label,default=default,min=lo,max=hi,step=step,
                      unit=unit,help=help_,target='config',kind='number')
                 for k,g,label,default,lo,hi,step,unit,help_ in _SYSTEM_ROWS]
SYSTEM_FIELDS += [dict(key=k,group='Циркуляція',label=label,default=default,target='config',kind='time',
                       help='Час місцевої зони Home Assistant. Межі мають відрізнятися; в нічному вікні рівень 1 має пріоритет над інтенсивним горінням.')
                  for k,label,default in [('fan_night_start','Початок нічної циркуляції','23:00'),
                                          ('fan_night_end','Кінець нічної циркуляції','07:00')]]
_MODEL_HELP = {
    'floor_air':('Передавання тепла в кімнату','Вплив різниці між еквівалентною температурою накопиченого тепла та кімнатою. Більше значення — швидша передача тепла.'),
    'room_loss':('Тепловтрати кімнати','Вплив різниці між кімнатою й вулицею. Більше значення — швидше прогнозоване охолодження.'),
    'floor_decay':('Охолодження накопиченого тепла','Темп зменшення запасу тепла. Обратка є індикатором запасу, а не температурою поверхні підлоги.'),
    'heat_gain':('Накопичення тепла від котла','Вплив виміряної потужності котла на еквівалентний тепловий запас.'),
    'fire_gain':('Вплив тепла каміна','Внесок різниці температур каміна та кімнати в прогноз температури. Не змінює пороги циркуляції.'),
}
SYSTEM_FIELDS += [dict(key=k,group='Теплова модель',label=_MODEL_HELP[k][0],
                      help=_MODEL_HELP[k][1]+' Зміна проходить чинну перевірку моделі на історії; без достатніх даних може бути відхилена. Попередню модель можна відновити.',
                      default=DEFAULT_MODEL[k],min=lo,max=hi,step=.0001,unit='',target='model',kind='number')
                  for k,(lo,hi) in BOUNDS.items()]
SYSTEM_DEFAULTS={f['key']:f['default'] for f in SYSTEM_FIELDS if f['target']=='config'}
SYSTEM_FIXED = (
    ('forecast_hours','Горизонт прогнозу',24,'год','Фіксований горизонт чинного планувальника. Ця панель не змінює алгоритм прогнозування.'),
    ('weather_tolerance','Допуск покриття погоди',3600,'с','Один погодинний інтервал. Більший дефіцит показує попередження; ця діагностична константа не редагується.'),
    ('boiler_min_on','Мінімальна робота котла',600,'с','Чинний захист від частих перемикань; аварійна зупинка має пріоритет. Не редагується з панелі.'),
    ('boiler_min_off','Пауза між запусками котла',900,'с','Чинний захист повторного запуску. Не редагується з панелі.'),
    ('fan_retry','Повтор команди вентилятора',30,'с','Обмеження повторів команд, включно з повтором після помилки. Збережено як системний захист.'),
    ('inverter_fresh','Свіжість інвертора',90,'с','Незмінний захист фактичної доступності живлення. Графік Svitlo не замінює цю перевірку.'),
    ('grid_input_fresh','Свіжість вхідної мережі',300,'с','Незмінний захист фактичного grid monitoring. Не налаштовується через панель.'),
    ('return_limit','Межа теплового запасу в прогнозі',50,'°C','Чинна верхня межа еквівалентної температури, не температура поверхні підлоги. Не редагується з панелі.'),
)


def system_values(cfg):
    return {k:cfg.get(k,v) for k,v in SYSTEM_DEFAULTS.items()}


def system_fields(cfg,model):
    fields=[dict(f,value=(model if f['target']=='model' else cfg).get(f['key'],f['default'])) for f in SYSTEM_FIELDS]
    fields.extend(dict(key=k,group='Системні захисти',label=label,value=value,unit=unit,
                       help=help_,kind='readonly',target='fixed') for k,label,value,unit,help_ in SYSTEM_FIXED)
    return fields


def validate_system_settings(values,cfg,model):
    """Validate the entire draft before any mutation; keep all other configuration private."""
    import re
    if not isinstance(values,dict) or not values:
        raise ValueError('Немає змін для збереження')
    schema={f['key']:f for f in SYSTEM_FIELDS}
    clean={}
    for key,value in values.items():
        if key not in schema:
            raise ValueError('Параметр не доступний для редагування')
        field=schema[key]
        if field['kind']=='time':
            if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
                raise ValueError('Час має бути у форматі ГГ:ХХ')
        elif (isinstance(value,bool) or not isinstance(value,(int,float)) or
              not math.isfinite(value) or not field['min']<=value<=field['max']):
            raise ValueError(field['label']+': значення поза дозволеними межами')
        clean[key]=value
    p={**system_values(cfg),**{k:v for k,v in clean.items() if schema[k]['target']=='config'}}
    if not (p['fire_ignite_delta']<p['fire_start_delta']<=p['fire_intense_delta'] and
            p['fire_stop_delta']<p['fire_fast_stop_delta']<p['fire_start_delta'] and
            p['fire_fast_stop_slope']<p['fire_fade_slope']<=0<=p['fire_ignite_slope'] and
            p['fan_night_start']!=p['fan_night_end']):
        raise ValueError('Пороги каміна або межі нічного часу суперечать одне одному')
    return ({k:v for k,v in clean.items() if schema[k]['target']=='config'},
            {k:v for k,v in clean.items() if schema[k]['target']=='model'})


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
