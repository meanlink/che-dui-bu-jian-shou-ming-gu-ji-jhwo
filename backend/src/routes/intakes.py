from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.middlewares.auth import current_user, can_write, admin_only
from src.models import IntakeConvention, IntakeRow, IntakeSheet
from src.schemas.intake import ConventionInput, IntakeCreate, RowAdjudicate, RowReplay, SettleRequest
from src.services import intake
from src.services.audit import record

router = APIRouter(prefix='/intakes')


def _load(db, sheet_id):
    sheet = db.get(IntakeSheet, sheet_id)
    if sheet is None:
        raise HTTPException(404, '接入清理单不存在')
    return sheet


def _rows(db, sheet):
    return list(db.scalars(select(IntakeRow).where(IntakeRow.sheet_id == sheet.id).order_by(IntakeRow.row_index)))


@router.get('/conventions')
def conventions(db=Depends(get_db), u=Depends(current_user)):
    intake.ensure_default_convention(db)
    db.commit()
    return [intake.convention_dict(c) for c in db.scalars(select(IntakeConvention).order_by(IntakeConvention.version.desc()))]


@router.post('/conventions', status_code=201)
def create_convention(data: ConventionInput, db=Depends(get_db), u=Depends(admin_only)):
    c = intake.create_convention(db, data, u.email)
    record(db, u.email, 'intake.convention', {'version': c.version, 'name': c.name})
    db.commit()
    return intake.convention_dict(c)


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return [intake.sheet_dict(db, s) for s in db.scalars(select(IntakeSheet).order_by(IntakeSheet.id.desc()))]


@router.post('', status_code=201)
def create(data: IntakeCreate, db=Depends(get_db), u=Depends(can_write)):
    try:
        sheet, created = intake.create(db, data, u)
        if created:
            record(db, u.email, 'intake.create', {'id': sheet.id, 'rows': sheet.row_count,
                                                  'accepted': sheet.accepted_count, 'quarantined': sheet.quarantined_count})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {'created': created, 'sheet': intake.sheet_dict(db, sheet, include_convention=True)}


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    sheet = _load(db, id)
    return {**intake.sheet_dict(db, sheet, include_convention=True),
            'rows': [intake.row_dict(r) for r in _rows(db, sheet)]}


@router.post('/{id}/rows/{row_id}/replay')
def replay(id: int, row_id: int, data: RowReplay, db=Depends(get_db), u=Depends(can_write)):
    try:
        sheet, row = intake.replay(db, id, row_id, data)
        record(db, u.email, 'intake.replay', {'sheet': id, 'row': row_id, 'status': row.status,
                                              'replays': row.replay_count})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {'sheet': intake.sheet_dict(db, sheet), 'row': intake.row_dict(row)}


@router.post('/{id}/rows/{row_id}/adjudicate')
def adjudicate(id: int, row_id: int, data: RowAdjudicate, db=Depends(get_db), u=Depends(admin_only)):
    try:
        sheet, row = intake.adjudicate(db, id, row_id, data, u)
        record(db, u.email, 'intake.adjudicate', {'sheet': id, 'row': row_id,
                                                  'decision': row.resolution, 'note': data.note.strip()})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {'sheet': intake.sheet_dict(db, sheet), 'row': intake.row_dict(row)}


@router.post('/{id}/reconcile')
def reconcile(id: int, db=Depends(get_db), u=Depends(can_write)):
    sheet = _load(db, id)
    try:
        report = intake.reconcile(db, sheet)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return report


@router.post('/{id}/settle')
def settle(id: int, data: SettleRequest, db=Depends(get_db), u=Depends(admin_only)):
    try:
        sheet = intake.settle(db, id, data, u)
        record(db, u.email, 'intake.settle', {'id': id, 'accepted': sheet.accepted_count,
                                              'quarantined': sheet.quarantined_count})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return intake.sheet_dict(db, sheet)
