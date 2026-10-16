"""Migrate one stopped AppDaemon app to HACS; never modify YAML or learned data.

Run from an environment with both the AppDaemon config and HA config mounted.
Paths in --hacs-dir must also resolve inside the AppDaemon container.
The default operation is a read-only plan. Backups must be outside app_dir.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

LEGACY = ('heating_climate.py', 'heating_environment.py', 'heating_fallback.py',
          'heating_model.py', 'heating_outages.py', 'heating_recovery.py', 'heating_stability.py')
EXPECTED = (*LEGACY, 'heating_ui.py', 'heating_ui_assets.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(app_dir, hacs_dir, data_dir, backup_dir):
    app_dir, hacs_dir, data_dir, backup_dir = (Path(p).resolve() for p in (app_dir,hacs_dir,data_dir,backup_dir))
    for path in (app_dir,hacs_dir,data_dir):
        if not path.is_dir():
            raise ValueError(f'Directory missing: {path}')
    if hacs_dir.is_relative_to(app_dir) or app_dir.is_relative_to(hacs_dir):
        raise ValueError('Migration is for separate legacy and HACS roots; inspect existing deployment')
    if backup_dir.is_relative_to(app_dir) or backup_dir.is_relative_to(hacs_dir):
        raise ValueError('Backup must be outside all scanned/managed app directories')
    if data_dir.is_relative_to(hacs_dir) or data_dir.is_relative_to(app_dir):
        raise ValueError('Learned data must remain outside managed/scanned app directories')
    missing = [name for name in EXPECTED if not (hacs_dir/name).is_file()]
    if missing:
        raise ValueError(f'Incomplete HACS download: {missing}')
    sources = {}
    for name in LEGACY:
        matches = list(app_dir.rglob(name))
        if len(matches) != 1 or matches[0].is_symlink():
            raise ValueError(f'Expected exactly one legacy {name}; found {len(matches)}')
        sources[str(matches[0])] = digest(matches[0])
    if any(app_dir.rglob('heating_ui.py')) or any(app_dir.rglob('heating_ui_assets.py')):
        raise ValueError('UI modules already exist in legacy tree; inspect duplicate imports')
    link = app_dir/'heating_ai_hacs'
    if link.exists() or link.is_symlink():
        raise ValueError('Migration link already exists; verify runtime before proceeding')
    return {'app_dir':str(app_dir),'hacs_dir':str(hacs_dir),'data_dir':str(data_dir),
            'backup_dir':str(backup_dir),'link':str(link),'sources':sources,
            'target_hashes':{name:digest(hacs_dir/name) for name in EXPECTED}}


def apply(migration, *, appdaemon_stopped=False):
    if not appdaemon_stopped:
        raise ValueError('Stop AppDaemon after recording mode/armed; --appdaemon-stopped is required')
    # Recheck every precondition immediately before changing files.
    fresh = plan(*(migration[key] for key in ('app_dir','hacs_dir','data_dir','backup_dir')))
    if fresh != migration:
        raise ValueError('Files changed after planning; inspect a fresh plan')
    backup = Path(migration['backup_dir'])/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup.mkdir(parents=True)
    manifest = backup/'migration.json'
    manifest.write_text(json.dumps(migration,indent=2))
    # Copy first, verify every backup, then remove the old scanned modules.
    for source, expected in migration['sources'].items():
        target = backup/Path(source).name
        shutil.copy2(source,target)
        if digest(target) != expected:
            raise ValueError('Backup verification failed; legacy files have not been removed')
    removed = []
    try:
        for source in migration['sources']:
            Path(source).unlink()
            removed.append(source)
        Path(migration['link']).symlink_to(migration['hacs_dir'],target_is_directory=True)
    except BaseException:
        for source in removed:
            shutil.copy2(backup/Path(source).name,source)
        raise
    return manifest


def rollback(manifest, *, appdaemon_stopped=False):
    if not appdaemon_stopped:
        raise ValueError('Stop AppDaemon before rollback')
    manifest = Path(manifest)
    migration = json.loads(manifest.read_text())
    link = Path(migration['link'])
    if not link.is_symlink() or link.resolve() != Path(migration['hacs_dir']).resolve():
        raise ValueError('Migration link changed; automatic rollback refused')
    for source, expected in migration['sources'].items():
        if Path(source).exists() or digest(manifest.parent/Path(source).name) != expected:
            raise ValueError('Legacy path occupied or backup checksum changed')
    # Prepare every file under an ignored suffix before exposing it to AppDaemon.
    staged = []
    try:
        for source in migration['sources']:
            temporary = Path(source).with_suffix('.py.restore-tmp')
            if temporary.exists():
                raise ValueError('Rollback temporary file already exists')
            shutil.copy2(manifest.parent/Path(source).name,temporary)
            staged.append((temporary,Path(source)))
        link.unlink()
        for temporary,target in staged:
            temporary.rename(target)
    finally:
        for temporary,_ in staged:
            temporary.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('app-dir','hacs-dir','data-dir','backup-dir'):
        parser.add_argument('--'+name)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--rollback',metavar='MANIFEST')
    parser.add_argument('--appdaemon-stopped',action='store_true')
    args=parser.parse_args()
    if args.rollback:
        rollback(args.rollback,appdaemon_stopped=args.appdaemon_stopped)
        print('Legacy code restored; settings and learned data were not changed.')
        return
    if not all((args.app_dir,args.hacs_dir,args.data_dir,args.backup_dir)):
        parser.error('All four directory paths are required for a plan or migration')
    migration=plan(args.app_dir,args.hacs_dir,args.data_dir,args.backup_dir)
    if args.apply:
        print(apply(migration,appdaemon_stopped=args.appdaemon_stopped))
    else:
        print(json.dumps(migration,indent=2))


if __name__=='__main__':
    main()
