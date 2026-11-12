"""Private bounded native settings/archive. Not Recorder, a lease or an actuator."""
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import stat

SETTINGS = {
    'day_target', 'night_target', 'day_upper', 'night_upper', 'sleep_start', 'sleep_end',
    'heating_start', 'heating_end', 'strategy', 'power_model', 'fan_mode',
    'fan_night', 'fan_standard', 'fan_intense', 'min_voltage_v', 'max_voltage_v',
    'hysteresis_v', 'stable_recovery_s', 'voltage_max_age_s', 'room_max_age_s', 'energy_budget_kwh',
}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate(values):
    if type(values) is not dict or not set(values) <= SETTINGS:
        raise ValueError('Unknown settings')
    for key, value in values.items():
        if key in ('sleep_start', 'sleep_end', 'heating_start', 'heating_end'):
            if not isinstance(value, str) or len(value) != 8:
                raise ValueError('Expected HH:MM:SS')
            h, m, s = map(int, value.split(':'))
            if not 0 <= h < 24 or not 0 <= m < 60 or s != 0:
                raise ValueError('Invalid time')
        elif key in ('strategy', 'power_model', 'fan_mode'):
            choices = {'strategy': ('Тільки в дозволені години', 'Переважно вночі', 'Комфорт протягом доби'),
                       'power_model': ('fixed_3', 'fixed_6', 'fixed_9'),
                       'fan_mode': ('Авто', 'Ручний запуск', 'Зупинити')}
            if value not in choices[key]:
                raise ValueError('Invalid selection')
        else:
            bounds = ((10, 32) if key.endswith(('_target', '_upper')) else
                      (0, 1000) if key == 'energy_budget_kwh' else
                      (0, 100) if key.startswith('fan_') else
                      (100, 300) if key in ('min_voltage_v', 'max_voltage_v') else
                      (0, 50) if key == 'hysteresis_v' else (1, 86400))
            if type(value) not in (int, float) or not math.isfinite(value) or not bounds[0] <= value <= bounds[1]:
                raise ValueError('Invalid numeric setting')
    for band in ('day', 'night'):
        if band+'_target' in values and band+'_upper' in values and values[band+'_target'] >= values[band+'_upper']:
            raise ValueError('Target must be below upper limit')
    if all(k in values for k in ('min_voltage_v', 'max_voltage_v', 'hysteresis_v')):
        if values['min_voltage_v'] + 2*values['hysteresis_v'] >= values['max_voltage_v']:
            raise ValueError('Invalid voltage recovery band')
    return values


class NativeStore:
    def __init__(self, directory):
        root = Path(directory).absolute()
        if not root.is_dir() or root.resolve() != root:
            raise ValueError('Private trusted directory required')
        self.path = root/'native-state.sqlite3'
        self.failed = False
        self.inode = None
        self._files()
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            created = False
        else:
            os.close(fd)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            created = True
        info = self.path.stat()
        self.inode = (info.st_dev, info.st_ino)
        with self.transaction(initial=created) as con:
            if created:
                con.execute('CREATE TABLE config(id INTEGER PRIMARY KEY, revision INTEGER, body TEXT, checksum TEXT)')
                con.execute('CREATE TABLE samples(at REAL PRIMARY KEY, room REAL, counter REAL, power REAL, quality TEXT)')
                con.execute('CREATE TABLE energy(start REAL, end REAL PRIMARY KEY, kwh REAL, category TEXT)')
                con.execute('CREATE TABLE forecasts(id TEXT PRIMARY KEY, received REAL, issued REAL, body TEXT, checksum TEXT)')
                con.execute('PRAGMA user_version=1')
                self._write(con, 0, dict(values={}, requests={}, manual_pause=True,
                                         one_shot=dict(state='unknown_outcome', expires=None),
                                         counter=None, counter_at=None, counter_gap=True,
                                         energy_fault=None, model_frozen=True))
            self._read(con)

    def _files(self):
        for suffix in ('', '-wal', '-shm', '-journal'):
            path = Path(str(self.path)+suffix)
            if path.is_symlink():
                raise ValueError('Symlink storage denied')
            if path.exists():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64*1024*1024:
                    raise ValueError('Invalid/oversized storage')
                if suffix == '' and self.inode is not None and (info.st_dev, info.st_ino) != self.inode:
                    raise ValueError('Storage replaced')
            elif suffix == '' and self.inode is not None:
                raise ValueError('Storage disappeared')

    @contextmanager
    def transaction(self, initial=False):
        if self.failed:
            raise ValueError('Persistence unavailable')
        con = None
        try:
            self._files()
            con = sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True, timeout=.25, isolation_level=None)
            if initial:
                con.execute('PRAGMA journal_mode=WAL')
            if con.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                raise ValueError('WAL required')
            con.execute('PRAGMA synchronous=FULL')
            con.execute('PRAGMA max_page_count=16384')
            con.execute('PRAGMA wal_autocheckpoint=32')
            con.execute('BEGIN IMMEDIATE')
            self._files()
            if con.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('Storage integrity error')
            if not initial and con.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('Unsupported storage schema')
            yield con
            con.commit()
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            self.failed = True
            raise ValueError('Native persistence failed closed') from None
        finally:
            if con:
                con.close()

    def _read(self, con):
        rows = con.execute('SELECT revision,body,checksum FROM config WHERE id=1').fetchall()
        if len(rows) != 1:
            raise ValueError('Missing state')
        revision, raw, checksum = rows[0]
        if type(revision) is not int or revision < 0 or len(raw) > 65536 or sha256(f'{revision}:{raw}'.encode()).hexdigest() != checksum:
            raise ValueError('State checksum mismatch')
        body = json.loads(raw)
        validate(body['values'])
        return revision, body

    def _write(self, con, revision, body):
        raw = encode(body)
        if len(raw) > 65536:
            raise ValueError('State full')
        con.execute('INSERT OR REPLACE INTO config VALUES(1,?,?,?)',
                    (revision, raw, sha256(f'{revision}:{raw}'.encode()).hexdigest()))

    def status(self):
        with self.transaction() as con:
            revision, body = self._read(con)
            return dict(body, revision=revision)

    def seed(self, values):
        validate(values)
        with self.transaction() as con:
            revision, body = self._read(con)
            if revision == 0 and not body['values'] and values:
                body['values'] = values
                self._write(con, 1, body)

    def mutate(self, expected, request_id, operation, payload):
        if type(expected) is not int or type(request_id) is not str or not 0 < len(request_id) <= 128:
            raise ValueError('Invalid mutation identity')
        digest = sha256(encode([operation, payload]).encode()).hexdigest()
        if operation == 'settings/save':
            validate(payload)
        with self.transaction() as con:
            revision, body = self._read(con)
            previous = body['requests'].get(request_id)
            if previous:
                return dict(status='saved' if previous == [digest, revision] else 'conflict', revision=revision)
            if revision != expected:
                return dict(status='conflict', revision=revision)
            if operation == 'settings/save':
                body['values'] = payload
            elif operation == 'pause':
                body['manual_pause'] = True
                body['one_shot'] = dict(state='cancelled', expires=None)
            elif operation == 'resume':
                body['manual_pause'] = False  # Planning preference, never physical permission.
            elif operation == 'cancel':
                body['one_shot'] = dict(state='cancelled', expires=None)
            elif operation == 'freeze':
                body['model_frozen'] = True
            else:
                return dict(status='denied', revision=revision)
            body['requests'][request_id] = [digest, revision+1]
            body['requests'] = dict(list(body['requests'].items())[-64:])
            self._write(con, revision+1, body)
            # Read back from the transaction; commit is completed before caller sees success.
            if self._read(con) != (revision+1, body):
                raise ValueError('CAS readback mismatch')
            return dict(status='saved', revision=revision+1)

    def observe(self, at, room, counter, power, counter_at=None):
        counter_at = at if counter_at is None else counter_at
        with self.transaction() as con:
            revision, body = self._read(con)
            if con.execute('SELECT count(*) FROM samples').fetchone()[0] >= 100000:
                raise ValueError('History full')
            if at is not None:
                con.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)', (at, room, counter, power, 'HA_CACHE'))
            if counter is None:
                body['counter_gap'] = True
            elif body['counter'] is None:
                body.update(counter=counter, counter_at=counter_at)
            elif counter_at > body['counter_at'] and not body['energy_fault']:
                if counter < body['counter']:
                    body['energy_fault'] = 'counter_decreased_no_reset_or_rebase'
                else:
                    category = 'recovered' if body['counter_gap'] else 'derived'
                    con.execute('INSERT OR IGNORE INTO energy VALUES(?,?,?,?)',
                                (body['counter_at'], counter_at, counter-body['counter'], category))
                    body.update(counter=counter, counter_at=counter_at, counter_gap=False)
            # Counter cursor updates do not invalidate settings CAS revisions.
            self._write(con, revision, body)

    def archive(self, forecast):
        raw = encode(forecast)
        issued, received = forecast.get('issued_at'), forecast.get('received_at')
        # Unknown issuance can be displayed but is NEVER an original 12h record.
        if type(issued) not in (int, float) or type(received) not in (int, float) or not 0 < issued <= received:
            return
        if len(raw) > 128000:
            raise ValueError('Forecast too large')
        identity = sha256(raw.encode()).hexdigest()
        with self.transaction() as con:
            if con.execute('SELECT count(*) FROM forecasts').fetchone()[0] >= 4096:
                raise ValueError('Forecast archive full')
            con.execute('INSERT OR IGNORE INTO forecasts VALUES(?,?,?,?,?)',
                        (identity, received, issued, raw, sha256(raw.encode()).hexdigest()))

    def history(self, start, end, now):
        if not all(type(x) in (int, float) and math.isfinite(x) for x in (start, end, now)) or not 0 < start < end <= now or end-start > 31*86400:
            raise ValueError('Invalid history range')
        with self.transaction() as con:
            actual = [dict(at=t, value=v, quality='estimated') for t, v in con.execute(
                'SELECT at,room FROM samples WHERE at>=? AND at<=? ORDER BY at LIMIT 10000', (start, end))]
            energy = [dict(start=a, end=b, kwh=k, category=c) for a,b,k,c in con.execute(
                'SELECT * FROM energy WHERE start>=? AND end<=? ORDER BY end LIMIT 10000', (start,end))]
            originals = []
            for received, issued, raw, checksum in con.execute(
                    'SELECT received,issued,body,checksum FROM forecasts WHERE received<=? AND issued<=? ORDER BY received DESC LIMIT 1',
                    (start-43200, start-43200)):
                if sha256(raw.encode()).hexdigest() != checksum:
                    raise ValueError('Archive checksum mismatch')
                originals.append(json.loads(raw))
            return dict(actual=actual, energy=energy, original=originals[0] if originals else None,
                        original_status='AVAILABLE' if originals else 'MISSING',
                        source='native_archive_HA_cache', truncated=len(actual)==10000)
