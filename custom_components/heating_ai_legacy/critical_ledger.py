"""Dormant reservation journal, not authentication, an owner lease or a sender.

The directory and identity must come from future trusted server code. Reopening
an outstanding reservation fences it; reconciliation never re-enables HEAT.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time


class LedgerError(RuntimeError):
    """No dispatch or retry may follow a journal failure."""


class LedgerConflict(LedgerError):
    """Stale CAS/identity or duplicate command with different immutable content."""


VERSION = 1
MAX_RECORDS = 256
MAX_REQUEST_BYTES = 4096
MAX_BODY_BYTES = 2 * 1024 * 1024
FILENAME = 'critical-reservations.sqlite3'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value):
    if type(value) is not str or not 0 < len(value.encode()) <= 128:
        raise LedgerError('Invalid identity')


def _counter(value):
    if type(value) is not int or not 0 <= value < 2**63 - 1:
        raise LedgerError('Invalid counter')


def _at(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise LedgerError('Invalid journal time')


def _fingerprint(record):
    return sha256(_json({key: record[key] for key in (
        'version', 'owner_id', 'boot_id', 'owner_epoch', 'command_id', 'action', 'request'
    )}).encode()).hexdigest()


class CriticalLedger:
    physical_commands_enabled = False

    def __init__(self, data_dir, *, owner_id=None, boot_id=None, owner_epoch=None):
        supplied = (owner_id, boot_id, owner_epoch) != (None, None, None)
        if supplied:
            for value in (owner_id, boot_id):
                _text(value)
            _counter(owner_epoch)
        root = Path(data_dir).absolute()
        if not root.is_dir() or root.resolve() != root:
            raise LedgerError('Trusted non-symlink directory required')
        self.path = root / FILENAME
        self.failed = False
        self._inode = None
        self._check_files()
        created = False
        try:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                             | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
                created = True
                # Persist creation of the fixed database name as well as its contents.
                if hasattr(os, 'O_DIRECTORY'):
                    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            self._check_files()
            st = self.path.stat()
            self._inode = (st.st_dev, st.st_ino)
            with self._transaction(initial=created) as con:
                if created:
                    if not supplied:
                        raise LedgerError('Initial server identity required')
                    con.execute('CREATE TABLE journal (id INTEGER PRIMARY KEY CHECK(id=1), '
                                'revision INTEGER NOT NULL, body TEXT NOT NULL, checksum TEXT NOT NULL)')
                    con.execute('PRAGMA user_version=1')
                    body = dict(version=VERSION, owner_id=owner_id, boot_id=boot_id,
                                owner_epoch=owner_epoch, fenced=False, commands={})
                    self._write(con, 0, body, insert=True)
                revision, body = self._read(con)
                if supplied:
                    self._identity(body, owner_id, boot_id, owner_epoch)
                if any(r['stage'] in ('PREPARED', 'SENT') for r in body['commands'].values()):
                    self._unknown(body, heat_only=False)
                    body['fenced'] = True
                    body['owner_epoch'] += 1
                    self._write(con, revision + 1, body)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            self.failed = True
            raise LedgerError('Journal initialization failed') from None

    def _check_files(self):
        for suffix in ('', '-wal', '-shm', '-journal'):
            path = Path(str(self.path) + suffix)
            try:
                st = path.lstat()
            except FileNotFoundError:
                if suffix == '' and self._inode is not None:
                    raise LedgerError('Journal disappeared')
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > 8 * 1024 * 1024:
                raise LedgerError('Linked, invalid or oversized journal file')
            if suffix == '' and self._inode is not None and (st.st_dev, st.st_ino) != self._inode:
                raise LedgerError('Journal replaced')

    @contextmanager
    def _transaction(self, *, initial=False):
        if self.failed:
            raise LedgerError('Journal failed closed')
        con = None
        try:
            self._check_files()
            con = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                                  timeout=.25, isolation_level=None)
            con.execute('PRAGMA busy_timeout=250')
            if initial:
                if con.execute('PRAGMA journal_mode=WAL').fetchone()[0] != 'wal':
                    raise LedgerError('WAL unavailable')
            if con.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                raise LedgerError('Unexpected journal mode')
            con.execute('PRAGMA synchronous=FULL')
            if con.execute('PRAGMA synchronous').fetchone()[0] != 2:
                raise LedgerError('FULL durability unavailable')
            con.execute('PRAGMA max_page_count=1024')
            con.execute('PRAGMA wal_autocheckpoint=16')
            con.execute('PRAGMA journal_size_limit=1048576')
            con.execute('BEGIN IMMEDIATE')
            self._check_files()
            if con.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise LedgerError('Journal integrity failure')
            if not initial and con.execute('PRAGMA user_version').fetchone()[0] != VERSION:
                raise LedgerError('Unsupported journal schema')
            yield con
            con.commit()
        except LedgerConflict:
            raise
        except (LedgerError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            self.failed = True
            raise LedgerError('Journal failed closed') from None
        finally:
            if con is not None:
                con.close()  # Also rolls back any uncommitted transaction.

    def _read(self, con):
        rows = con.execute('SELECT revision,body,checksum FROM journal').fetchall()
        if len(rows) != 1:
            raise LedgerError('Invalid journal row count')
        revision, raw, checksum = rows[0]
        _counter(revision)
        if (type(raw) is not str or len(raw.encode()) > MAX_BODY_BYTES
                or sha256(f'{VERSION}:{revision}:{raw}'.encode()).hexdigest() != checksum):
            raise LedgerError('Journal checksum mismatch')
        body = json.loads(raw)
        self._validate(body)
        return revision, body

    def _validate(self, body):
        if set(body) != {'version', 'owner_id', 'boot_id', 'owner_epoch', 'fenced', 'commands'}:
            raise LedgerError('Invalid journal shape')
        if type(body['version']) is not int or body['version'] != VERSION or type(body['fenced']) is not bool:
            raise LedgerError('Unsupported journal payload')
        for key in ('owner_id', 'boot_id'):
            _text(body[key])
        _counter(body['owner_epoch'])
        commands = body['commands']
        if type(commands) is not dict or len(commands) > MAX_RECORDS:
            raise LedgerError('Journal capacity exceeded')
        for identity, record in commands.items():
            _text(identity)
            if set(record) != {'version', 'owner_id', 'boot_id', 'owner_epoch', 'command_id',
                               'action', 'request', 'fingerprint', 'stage', 'prepared_at', 'sent_at', 'ha_echo_at'}:
                raise LedgerError('Invalid command shape')
            for key in ('owner_id', 'boot_id', 'command_id'):
                _text(record[key])
            _counter(record['owner_epoch'])
            if (type(record['version']) is not int or record['version'] != VERSION or record['command_id'] != identity
                    or record['action'] not in ('HEAT', 'STOP')
                    or type(record['request']) is not dict
                    or len(_json(record['request']).encode()) > MAX_REQUEST_BYTES
                    or _fingerprint(record) != record['fingerprint']
                    or record['stage'] not in ('PREPARED', 'SENT', 'HA_ECHO', 'UNKNOWN')):
                raise LedgerError('Invalid immutable command')
            _at(record['prepared_at'])
            sent, echo = record['sent_at'], record['ha_echo_at']
            if sent is not None:
                _at(sent)
                if sent < record['prepared_at']:
                    raise LedgerError('Invalid send chronology')
            if echo is not None:
                _at(echo)
                if sent is None or echo <= sent:
                    raise LedgerError('Invalid observation chronology')
            if (record['stage'] == 'PREPARED' and (sent is not None or echo is not None)
                    or record['stage'] == 'SENT' and (sent is None or echo is not None)
                    or record['stage'] == 'HA_ECHO' and echo is None):
                raise LedgerError('Missing stage evidence')

    def _write(self, con, revision, body, *, insert=False):
        _counter(revision)
        self._validate(body)
        raw = _json(body)
        if len(raw.encode()) > MAX_BODY_BYTES:
            raise LedgerError('Journal capacity exceeded')
        checksum = sha256(f'{VERSION}:{revision}:{raw}'.encode()).hexdigest()
        if insert:
            con.execute('INSERT INTO journal VALUES (1,?,?,?)', (revision, raw, checksum))
        else:
            updated = con.execute('UPDATE journal SET revision=?,body=?,checksum=? WHERE id=1 AND revision=?',
                                  (revision, raw, checksum, revision - 1))
            if updated.rowcount != 1:
                raise LedgerConflict('CAS mismatch')

    @staticmethod
    def _identity(body, owner_id, boot_id, owner_epoch):
        if (body['owner_id'], body['boot_id'], body['owner_epoch']) != (owner_id, boot_id, owner_epoch):
            raise LedgerConflict('Owner/boot/epoch mismatch')

    def _current(self, con, expected_revision, owner_id, boot_id, owner_epoch):
        _counter(expected_revision)
        _counter(owner_epoch)
        _text(owner_id)
        _text(boot_id)
        revision, body = self._read(con)
        self._identity(body, owner_id, boot_id, owner_epoch)
        if revision != expected_revision:
            raise LedgerConflict('CAS mismatch')
        return revision, body

    @staticmethod
    def _unknown(body, *, heat_only):
        for record in body['commands'].values():
            if record['stage'] in ('PREPARED', 'SENT') and (not heat_only or record['action'] == 'HEAT'):
                record['stage'] = 'UNKNOWN'

    @staticmethod
    def _trim_confirmed(body, keep=128):
        """Bound confirmed history without ever deleting unresolved evidence."""
        confirmed = sorted(
            (record for record in body['commands'].values()
             if record['stage'] == 'HA_ECHO'),
            key=lambda record: (record['ha_echo_at'], record['command_id']),
        )
        remove = max(0, len(body['commands']) - keep)
        for record in confirmed[:remove]:
            body['commands'].pop(record['command_id'])

    def prepare(self, command_id, action, request, *, expected_revision, owner_id,
                boot_id, owner_epoch, at=None):
        _text(command_id)
        for value in (owner_id, boot_id):
            _text(value)
        _counter(owner_epoch)
        at = time.time() if at is None else at
        _at(at)
        if action not in ('HEAT', 'STOP') or type(request) is not dict:
            raise LedgerError('Invalid request')
        # Canonical roundtrip detaches caller-owned data and rejects non-JSON values.
        try:
            encoded = _json(request)
            if len(encoded.encode()) > MAX_REQUEST_BYTES:
                raise ValueError
            request = json.loads(encoded)
        except (ValueError, TypeError, RecursionError):
            raise LedgerError('Invalid or oversized request') from None
        record = dict(version=VERSION, owner_id=owner_id, boot_id=boot_id, owner_epoch=owner_epoch,
                      command_id=command_id, action=action, request=request, stage='PREPARED',
                      prepared_at=at, sent_at=None, ha_echo_at=None)
        record['fingerprint'] = _fingerprint(record)
        with self._transaction() as con:
            revision, body = self._current(con, expected_revision, owner_id, boot_id, owner_epoch)
            old = body['commands'].get(command_id)
            if old is not None:
                if old['fingerprint'] != record['fingerprint']:
                    raise LedgerConflict('Immutable command conflict')
                return dict(created=False, revision=revision, command=old)
            if body['fenced'] and action == 'HEAT':
                raise LedgerConflict('HEAT fenced; external reconciliation required')
            if len(body['commands']) >= MAX_RECORDS:
                self._trim_confirmed(body, MAX_RECORDS - 1)
            if len(body['commands']) >= MAX_RECORDS:
                raise LedgerError('Journal full; no eviction or implicit reset')
            if action == 'STOP':
                # A safe STOP is the only command allowed to supersede an
                # unresolved reservation. Preserve every prior record as
                # UNKNOWN evidence, keep the fence closed, and let the
                # independently echoed STOP open it through complete_safe_stop.
                self._unknown(body, heat_only=False)
                body['fenced'] = True
            elif any(r['stage'] in ('PREPARED', 'SENT') for r in body['commands'].values()):
                raise LedgerConflict('Outstanding reservation')
            body['commands'][command_id] = record
            self._write(con, revision + 1, body)
            return dict(created=True, revision=revision + 1, command=record)

    def _record(self, command_id, stage, at, *, expected_revision, owner_id, boot_id, owner_epoch):
        _at(at)
        with self._transaction() as con:
            revision, body = self._current(con, expected_revision, owner_id, boot_id, owner_epoch)
            record = body['commands'].get(command_id)
            previous = 'PREPARED' if stage == 'SENT' else 'SENT'
            if (record is None or record['stage'] != previous
                    or (record['owner_id'], record['boot_id'], record['owner_epoch']) != (owner_id, boot_id, owner_epoch)
                    or body['fenced'] and record['action'] == 'HEAT'):
                raise LedgerConflict('Unconfirmed/stale transition')
            record['stage'] = stage
            record['sent_at' if stage == 'SENT' else 'ha_echo_at'] = at
            self._write(con, revision + 1, body)
            return revision + 1

    def record_sent(self, command_id, *, at, **identity):
        """Service initiation only; not even an HA state echo."""
        return self._record(command_id, 'SENT', at, **identity)

    def record_ha_echo(self, command_id, *, at, **identity):
        """Journal an independently qualified HA observation, never hardware ACK."""
        return self._record(command_id, 'HA_ECHO', at, **identity)

    def fence(self, *, expected_revision, owner_id, boot_id, owner_epoch):
        self.reconcile_restart(boot_id, expected_revision=expected_revision,
                               owner_id=owner_id, boot_id=boot_id, owner_epoch=owner_epoch)

    def reconcile_restart(self, new_boot_id, *, expected_revision, owner_id, boot_id, owner_epoch):
        _text(new_boot_id)
        with self._transaction() as con:
            revision, body = self._current(con, expected_revision, owner_id, boot_id, owner_epoch)
            self._unknown(body, heat_only=False)
            body.update(boot_id=new_boot_id, owner_epoch=owner_epoch + 1, fenced=True)
            self._write(con, revision + 1, body)

    def claim_boot(self, new_boot_id, *, expected_owner_id):
        """Fence every process start and return the new trusted identity."""
        _text(new_boot_id)
        _text(expected_owner_id)
        with self._transaction() as con:
            revision, body = self._read(con)
            if body['owner_id'] != expected_owner_id:
                raise LedgerConflict('Owner mismatch')
            self._unknown(body, heat_only=False)
            body.update(boot_id=new_boot_id, owner_epoch=body['owner_epoch'] + 1,
                        fenced=True)
            self._write(con, revision + 1, body)
            return dict(body, revision=revision + 1)

    def complete_safe_stop(self, command_id, *, expected_revision,
                           owner_id, boot_id, owner_epoch):
        """Open the HEAT fence only after a journalled STOP has an HA echo."""
        with self._transaction() as con:
            revision, body = self._current(
                con, expected_revision, owner_id, boot_id, owner_epoch)
            record = body['commands'].get(command_id)
            if (record is None or record['action'] != 'STOP'
                    or record['stage'] != 'HA_ECHO'
                    or record['request'].get('domain') != 'climate'):
                raise LedgerConflict('Qualified STOP evidence required')
            body['fenced'] = False
            self._trim_confirmed(body)
            self._write(con, revision + 1, body)
            return revision + 1

    def status(self):
        with self._transaction() as con:
            revision, body = self._read(con)
            return dict(body, revision=revision, physical_commands_enabled=False,
                        physical_authority=False, physical_ack=False, contacts_open=None)
