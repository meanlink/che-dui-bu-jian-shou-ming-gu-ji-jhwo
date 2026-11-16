"""Intake clearing-sheet rule checks (SQLite, no Docker, no Redis).

Run from backend/:  python tests/test_intake_rules.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp(prefix='intake-rules-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(TMP, 'rules.db')
os.environ['SEED_DEMO'] = 'false'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6399/0'

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

from migrations.initial import migrate  # noqa: E402
from src.config.database import Session  # noqa: E402
from src.models import Dataset, IntakeRow, IntakeSheet, User  # noqa: E402
from src.schemas.intake import ConventionInput, IntakeCreate, RowAdjudicate, RowReplay, SettleRequest  # noqa: E402
from src.services import intake  # noqa: E402
from src.utils.security import hash_password  # noqa: E402

MESSY = [
    {'asset': 'A-1', 'model': 'AC-8', 'km': '10,000 公里', 'failed': '1'},
    {'asset': 'A-2', 'model': 'AC-8', 'km': '', 'failed': '0'},
    {'asset': 'A-1', 'model': 'AC-8', 'km': '9000', 'failed': '1'},
    {'asset': 'A-3', 'model': 'AC-77', 'km': '7000', 'failed': '1'},
    {'asset': 'A-4', 'model': 'AC-8', 'km': '6000', 'failed': 'maybe'},
    'not-an-object',
    {'资产号': 'A-5', '型号': 'AC-12', '里程': '5000 km', '是否失效': '否'},
]


def need(condition, message):
    if not condition:
        raise AssertionError(message)


def blocked(status, fn, message):
    try:
        fn()
    except HTTPException as error:
        need(error.status_code == status, f'{message}: expected {status}, got {error.status_code} {error.detail}')
        return
    raise AssertionError(f'{message}: expected HTTP {status}, call succeeded')


def main():
    migrate()
    with Session() as db:
        admin = User(email='rules@x', name='rules', role='admin', password=hash_password('x'))
        db.add(admin)
        db.commit()
        admin_id = admin.id

        sheet, created = intake.create(db, IntakeCreate(name='规则月表', source='m.csv', rows=MESSY), admin)
        db.commit()
        sid = sheet.id
        need(created, 'first receipt creates a sheet')
        need(sheet.row_count == sheet.accepted_count + sheet.quarantined_count == len(MESSY), 'raw = accepted + quarantined at receipt')
        need((sheet.accepted_count, sheet.quarantined_count) == (2, 5), 'messy rows are sorted into 2 accepted / 5 quarantined')
        rows = {row.row_index: row for row in db.scalars(select(IntakeRow).where(IntakeRow.sheet_id == sid))}
        need(rows[0].normalized['km'] == 10000.0, 'thousands separators and unit suffix follow the locked convention')
        need(rows[6].normalized == {'asset': 'A-5', 'model': 'AC-12', 'km': 5000.0, 'failed': 0}, 'Chinese aliases and flag words convert')
        need(rows[2].reason_code == 'asset_conflict' and rows[2].needs_adjudication, 'duplicate asset is quarantined as a conflict')
        need(rows[2].raw == MESSY[2], 'original row is preserved on quarantine')
        need(rows[5].reason_code == 'row_not_object', 'non-object row is quarantined, not skipped')

        again, created_again = intake.create(db, IntakeCreate(name='规则月表', source='m.csv', rows=MESSY), admin)
        db.commit()
        need(not created_again and again.id == sid, 'retry of the same table reopens the same sheet')
        need(db.scalar(select(Dataset).where(Dataset.name == f'接入清理单 #{sid} · 可试算批次')) is not None, 'accepted rows produce a trial batch')
        need(len(db.scalars(select(Dataset)).all()) == 1, 'retry does not create a second batch')

        db.refresh(sheet)
        seq = sheet.decision_seq
        blocked(409, lambda: intake.replay(db, sid, rows[0].id, RowReplay(corrections={'km': '1'})), 'accepted row cannot be replayed')
        db.rollback()

        seq = db.get(IntakeSheet, sid).decision_seq
        intake.replay(db, sid, rows[1].id, RowReplay(corrections={'km': '45000'}, expected_seq=seq))
        db.commit()
        need(db.get(IntakeRow, rows[1].id).status == 'accepted', 'corrected quarantined row is accepted on replay')
        need(len(db.get(Dataset, db.get(IntakeSheet, sid).batch_id).records) == 3, 'replayed acceptance appends to the batch once')

        seq = db.get(IntakeSheet, sid).decision_seq
        blocked(409, lambda: intake.replay(db, sid, rows[2].id, RowReplay(corrections={'km': '9000'}, expected_seq=seq - 1)), 'stale decision sequence is rejected')
        db.rollback()

        seq = db.get(IntakeSheet, sid).decision_seq
        blocked(409, lambda: intake.settle(db, sid, SettleRequest(expected_seq=seq), admin), 'cannot settle with quarantined rows pending')
        db.rollback()

        seq = db.get(IntakeSheet, sid).decision_seq
        blocked(409, lambda: intake.adjudicate(db, sid, rows[2].id, RowAdjudicate(decision='accepted', note='覆盖'), admin), 'conflict cannot overwrite an accepted row')
        db.rollback()

        for index in (2, 3, 4, 5):
            seq = db.get(IntakeSheet, sid).decision_seq
            intake.adjudicate(db, sid, rows[index].id, RowAdjudicate(decision='excluded', note='本月不纳入试算', expected_seq=seq), admin)
            db.commit()

        frozen = dict(db.get(IntakeSheet, sid).convention)
        new_config = {**frozen, 'fields': [dict(field) for field in frozen['fields']]}
        new_config['fields'][0] = {**new_config['fields'][0], 'aliases': new_config['fields'][0]['aliases'] + ['编号']}
        intake.create_convention(db, ConventionInput(name='新约定', config=new_config), 'rules')
        db.commit()
        need(new_config != frozen, 'convention update really changes the version')
        need(db.get(IntakeSheet, sid).convention == frozen, 'convention update does not change an in-flight sheet')

        report = intake.reconcile(db, db.get(IntakeSheet, sid))
        db.commit()
        need(report['ok'], 'reconcile passes before settlement')
        need(report['counts']['raw'] == report['counts']['accepted'] + report['counts']['quarantined'], 'reconcile holds raw = accepted + quarantined')

        # Simulate an interruption after rows were stored but before counters/batch were persisted.
        crash = intake.create(db, IntakeCreate(name='中断回放表', rows=MESSY), admin)[0]
        db.commit()
        crash_id = crash.id
        crash.batch_id = None
        crash.accepted_count = 0
        crash.quarantined_count = 0
        for dataset in db.scalars(select(Dataset)).all():
            db.delete(dataset)
        db.commit()
        report = intake.reconcile(db, db.get(IntakeSheet, crash_id))
        db.commit()
        need(report['counts'] == {'raw': 7, 'accepted': 2, 'quarantined': 5, 'pending': 5, 'conflicts': 1}, 'recovery re-derives per-row destinations')
        report_again = intake.reconcile(db, db.get(IntakeSheet, crash_id))
        db.commit()
        need(report_again['batch']['rows'] == 2 and len(db.scalars(select(Dataset)).all()) == 1, 'recovery does not duplicate the batch')

        # Two concurrent decisions on one stale sequence: exactly one wins.
        sheet = db.get(IntakeSheet, sid)
        seq = sheet.decision_seq
        outcomes = []
        barrier = threading.Barrier(2)

        def decide(index, km):
            with Session() as other:
                try:
                    barrier.wait()
                    intake.replay(other, sid, rows[index].id, RowReplay(corrections={'km': km}, expected_seq=seq))
                    other.commit()
                    outcomes.append('won')
                except HTTPException as error:
                    other.rollback()
                    outcomes.append(error.status_code)

        threads = [threading.Thread(target=decide, args=(2, '1')), threading.Thread(target=decide, args=(3, '2'))]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        need(sorted(outcomes, key=str) == [409, 'won'], f'only one concurrent decision wins, got {outcomes}')

        db.expire_all()
        sheet = db.get(IntakeSheet, sid)
        need(sheet.row_count == sheet.accepted_count + sheet.quarantined_count, 'row accounting holds after concurrent decisions')
        for row in db.scalars(select(IntakeRow).where(IntakeRow.sheet_id == sid, IntakeRow.status == 'quarantined', IntakeRow.resolution == 'pending')):
            intake.adjudicate(db, sid, row.id, RowAdjudicate(decision='excluded', note='并发后收口：本月排除'), db.get(User, admin_id))
            db.commit()
        db.expire_all()
        sheet = db.get(IntakeSheet, sid)
        intake.settle(db, sid, SettleRequest(expected_seq=sheet.decision_seq), db.get(User, admin_id))
        db.commit()
        need(db.get(IntakeSheet, sid).status == 'settled', 'sheet settles once the quarantined rows are all excluded')
        need(len(db.get(Dataset, db.get(IntakeSheet, sid).batch_id).records) == db.get(IntakeSheet, sid).accepted_count, 'settled batch size equals accepted count')
        blocked(409, lambda: intake.settle(db, sid, SettleRequest(), db.get(User, admin_id)), 'double settlement is rejected')
        db.rollback()
        blocked(409, lambda: intake.replay(db, sid, rows[4].id, RowReplay(corrections={'km': '8'})), 'settled sheet is frozen')
        db.rollback()

    print('intake rules: all checks passed')


if __name__ == '__main__':
    main()
