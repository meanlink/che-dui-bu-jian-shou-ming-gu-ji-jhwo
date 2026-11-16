"""Intake clearing sheets.

A sheet freezes the supplier table and the field-conversion convention at
receipt. Every row is dispositioned as accepted or quarantined with a reason.
Accepted rows append exactly once to a deterministic trial batch; quarantined
rows can be corrected and replayed without touching the raw row or any accepted
row. Asset conflicts and settlement are explicit decisions.
"""
import copy
import math
import time

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from src.models import Dataset, IntakeConvention, IntakeRow, IntakeSheet
from src.utils.serialization import checksum

FIELD_KEYS = ('asset', 'model', 'km', 'failed')

DEFAULT_CONVENTION_CONFIG = {
    'fields': [
        {'key': 'asset', 'label': '部件资产号', 'type': 'string', 'required': True,
         'aliases': ['asset', '资产号', '资产编号', '部件资产号']},
        {'key': 'model', 'label': '型号', 'type': 'string', 'required': True,
         'aliases': ['model', '型号', '压缩机型号']},
        {'key': 'km', 'label': '观测运行里程', 'type': 'number', 'required': True,
         'aliases': ['km', '里程', '运行里程', '观测运行里程'],
         'min': 1, 'max': 10_000_000, 'unit': '公里'},
        {'key': 'failed', 'label': '失效标记', 'type': 'flag', 'required': True,
         'aliases': ['failed', '失效', '是否失效', '失效标记'],
         'true_values': ['1', 'true', 'yes', 'y', '是', '已失效'],
         'false_values': ['0', 'false', 'no', 'n', '否', '在役', '截止仍在役']},
    ],
    'models': ['AC-8', 'AC-12'],
    'strip_thousands': True,
    'allow_unit_suffix': True,
    'asset_unique': True,
}

REASONS = {
    'row_not_object': '整行不是键值对象，无法读取字段',
    'missing_field': '缺少必填字段',
    'invalid_number': '数值字段无效',
    'invalid_flag': '失效标记只能是 0/1（也接受 是/否、true/false）',
    'unknown_model': '型号不在约定允许清单内',
    'asset_conflict': '资产号与已接纳件冲突，不能覆盖已接纳件',
}



# ---------- conventions ----------

def _validate_config(config):
    if not isinstance(config, dict):
        raise HTTPException(422, '约定必须是键值对象')
    config = copy.deepcopy(config)
    fields = config.get('fields')
    if not isinstance(fields, list):
        raise HTTPException(422, '约定必须包含 fields 列表')
    seen = {}
    for field in fields:
        if not isinstance(field, dict) or not field.get('key'):
            raise HTTPException(422, '每个字段约定必须包含 key')
        key = str(field['key'])
        if key in seen:
            raise HTTPException(422, f'字段 {key} 在约定中重复')
        if field.get('type') not in ('string', 'number', 'flag'):
            raise HTTPException(422, f'字段 {key} 的 type 只能是 string/number/flag')
        seen[key] = field
    for key in FIELD_KEYS:
        if key not in seen:
            raise HTTPException(422, f'约定缺少必需字段 {key}')
        if not seen[key].get('required', True):
            raise HTTPException(422, f'字段 {key} 是清理与试算的必需字段，不能设为可选')
        seen[key]['required'] = True
    for key in ('asset', 'model'):
        seen[key]['type'] = 'string'
    if seen['km']['type'] != 'number':
        raise HTTPException(422, '字段 km 的 type 必须是 number')
    if seen['failed']['type'] != 'flag':
        raise HTTPException(422, '字段 failed 的 type 必须是 flag')
    for key in ('km',):
        low, high = seen[key].get('min'), seen[key].get('max')
        if low is not None and high is not None and float(low) >= float(high):
            raise HTTPException(422, f'字段 {key} 的 min 必须小于 max')
    models = config.get('models')
    if models is not None and (not isinstance(models, list) or not models):
        raise HTTPException(422, 'models 必须是非空列表')
    return config


def _latest(db):
    return db.scalar(select(IntakeConvention).order_by(IntakeConvention.version.desc()).limit(1))


def ensure_default_convention(db):
    convention = _latest(db)
    if convention is None:
        convention = IntakeConvention(version=1, name='默认农机部件接入约定', config=copy.deepcopy(DEFAULT_CONVENTION_CONFIG), created_by='system', created_at=time.time())
        db.add(convention)
        db.flush()
    return convention


def create_convention(db, data, actor):
    ensure_default_convention(db)
    config = _validate_config(data.config)
    version = (db.scalar(select(func.max(IntakeConvention.version))) or 0) + 1
    convention = IntakeConvention(version=version, name=data.name.strip(), config=config, created_by=actor, created_at=time.time())
    db.add(convention)
    db.flush()
    return convention


def convention_dict(c):
    return {'id': c.id, 'version': c.version, 'name': c.name, 'config': c.config, 'created_by': c.created_by, 'created_at': c.created_at}


# ---------- row evaluation ----------

def _alias_index(field):
    aliases = [field['key']] + [str(a) for a in field.get('aliases', [])]
    return {alias.casefold(): alias for alias in aliases}


def _find_value(raw, field):
    if field['key'] in raw:
        return True, raw[field['key']]
    lookup = _alias_index(field)
    for key, value in raw.items():
        if str(key).casefold() in lookup:
            return True, value
    return False, None


def _parse_number(value, field, config):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        if config.get('strip_thousands', True):
            text = text.replace(',', '').replace('，', '').replace(' ', '')
        if config.get('allow_unit_suffix', True):
            for suffix in ('公里', 'km', 'KM', 'Km'):
                if text.endswith(suffix):
                    text = text[:-len(suffix)]
                    break
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(number):
        return None
    if 'min' in field and number < float(field['min']):
        return None
    if 'max' in field and number > float(field['max']):
        return None
    return number


def _parse_flag(value, field):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return int(value) if float(value) in (0.0, 1.0) else None
    text = str(value).strip().casefold()
    true_values = {str(v).casefold() for v in field.get('true_values', ['1', 'true', 'yes', '是'])}
    false_values = {str(v).casefold() for v in field.get('false_values', ['0', 'false', 'no', '否'])}
    if text in true_values:
        return 1
    if text in false_values:
        return 0
    return None


def evaluate(raw, config):
    """Return (normalized, None) or (None, (code, message))."""
    if not isinstance(raw, dict):
        return None, ('row_not_object', REASONS['row_not_object'])
    normalized = {}
    fields = {field['key']: field for field in config['fields']}
    for key in FIELD_KEYS:
        field = fields[key]
        found, value = _find_value(raw, field)
        if not found or value is None or (isinstance(value, str) and not value.strip()):
            if field.get('required', True):
                return None, ('missing_field', f'缺少 {field["label"]}（可识别别名：{" / ".join(field.get("aliases", []))}）')
            continue
        if field['type'] == 'number':
            number = _parse_number(value, field, config)
            if number is None:
                bounds = ''
                if 'min' in field or 'max' in field:
                    bounds = f'，范围 {field.get("min", "—")} 至 {field.get("max", "—")}'
                return None, ('invalid_number', f'{field["label"]} 必须是有限数值{bounds}；收到 {value!r}')
            normalized[key] = number
        elif field['type'] == 'flag':
            flag = _parse_flag(value, field)
            if flag is None:
                return None, ('invalid_flag', f'{field["label"]}：{REASONS["invalid_flag"]}；收到 {value!r}')
            normalized[key] = flag
        else:
            normalized[key] = str(value).strip()
    models = config.get('models') or []
    allowed = [str(m['value']) if isinstance(m, dict) else str(m) for m in models]
    if allowed and normalized['model'] not in allowed:
        return None, ('unknown_model', f'{REASONS["unknown_model"]}：{normalized["model"]!r}（允许：{" / ".join(allowed)}）')
    return normalized, None


# ---------- helpers ----------

def _sheet_for_update(db, sheet_id):
    sheet = db.scalar(select(IntakeSheet).where(IntakeSheet.id == sheet_id).with_for_update())
    if sheet is None:
        raise HTTPException(404, '接入清理单不存在')
    return sheet


def _claim_decision(db, sheet):
    """Atomically take the next decision slot. Only one concurrent decision wins."""
    seq = sheet.decision_seq
    result = db.execute(
        update(IntakeSheet)
        .where(IntakeSheet.id == sheet.id, IntakeSheet.decision_seq == seq, IntakeSheet.status == 'open')
        .values(decision_seq=seq + 1)
    )
    if result.rowcount != 1:
        raise HTTPException(409, '清理单已被其他操作更新，请刷新后重试')
    sheet.decision_seq = seq + 1
    return seq


def _require_seq(sheet, expected):
    if expected is not None and expected != sheet.decision_seq:
        raise HTTPException(409, f'清理单已被其他操作更新（当前决定序号 {sheet.decision_seq}），请刷新后重试')


def _conflict_row(db, sheet_id, asset, exclude_row_id):
    return db.scalar(select(IntakeRow).where(
        IntakeRow.sheet_id == sheet_id,
        IntakeRow.status == 'accepted',
        IntakeRow.asset == asset,
        IntakeRow.id != exclude_row_id,
    ).order_by(IntakeRow.batch_position).limit(1))


def _next_position(db, sheet_id):
    return (db.scalar(select(func.max(IntakeRow.batch_position)).where(IntakeRow.sheet_id == sheet_id)) or 0) + 1


def _accept(db, sheet, row, normalized, position):
    row.status = 'accepted'
    row.reason_code = 'accepted'
    row.reason = ''
    row.asset = normalized['asset']
    row.normalized = normalized
    row.needs_adjudication = False
    row.resolution = 'accepted'
    row.resolution_note = ''
    row.batch_position = position
    row.updated_at = time.time()


def _quarantine(row, code, message, needs_adjudication=False):
    row.status = 'quarantined'
    row.reason_code = code
    row.reason = message
    row.normalized = None
    row.needs_adjudication = needs_adjudication
    row.resolution = 'pending'
    row.batch_position = None
    row.updated_at = time.time()


def _merge(raw, draft):
    merged = dict(raw) if isinstance(raw, dict) else {}
    for key, value in (draft or {}).items():
        merged[key] = value
    return merged


def _sync_counts(sheet, accepted, quarantined):
    sheet.accepted_count = accepted
    sheet.quarantined_count = quarantined
    if accepted + quarantined != sheet.row_count:
        raise HTTPException(409, f'行数核对失败：原行 {sheet.row_count} 不等于接纳 {accepted} 加隔离 {quarantined}')


def _refresh_counts(db, sheet):
    accepted = db.scalar(select(func.count()).select_from(IntakeRow).where(IntakeRow.sheet_id == sheet.id, IntakeRow.status == 'accepted'))
    quarantined = db.scalar(select(func.count()).select_from(IntakeRow).where(IntakeRow.sheet_id == sheet.id, IntakeRow.status == 'quarantined'))
    _sync_counts(sheet, accepted, quarantined)


def _batch_name(sheet):
    return f'接入清理单 #{sheet.id} · 可试算批次'


def _sync_batch(db, sheet):
    """Append accepted rows to the deterministic trial batch, never rewriting old ones."""
    accepted = list(db.scalars(select(IntakeRow).where(
        IntakeRow.sheet_id == sheet.id, IntakeRow.status == 'accepted'
    ).order_by(IntakeRow.batch_position)))
    records = [row.normalized for row in accepted]
    if not records:
        return
    name = _batch_name(sheet)
    dataset = db.get(Dataset, sheet.batch_id) if sheet.batch_id else None
    if dataset is None:
        dataset = db.scalar(select(Dataset).where(Dataset.name == name))
    if dataset is None:
        dataset = Dataset(name=name, source=sheet.source or '接入清理', records=records,
                          checksum=checksum(records), created_at=time.time())
        db.add(dataset)
        db.flush()
    sheet.batch_id = dataset.id
    existing = list(dataset.records or [])
    if len(existing) > len(records) or existing != records[:len(existing)]:
        raise HTTPException(409, '可试算批次中已接纳的记录与逐行去向不一致，拒绝覆盖；请先核对批次去向')
    appended = records[len(existing):]
    if appended:
        dataset.records = existing + appended
        dataset.checksum = checksum(dataset.records)
    db.flush()


# ---------- sheet lifecycle ----------

def create(db, data, user):
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, '清理单名称去除空格后至少需要两个字符')
    if not isinstance(data.rows, list) or not data.rows:
        raise HTTPException(422, '请提供至少一行原始记录')
    if len(data.rows) > 5000:
        raise HTTPException(422, '单次最多受理 5,000 行')
    key = (data.idempotency_key or '').strip() or None
    digest = checksum(data.rows)
    if key:
        existing = db.scalar(select(IntakeSheet).where(IntakeSheet.idempotency_key == key))
        if existing is not None:
            return existing, False
    existing = db.scalar(select(IntakeSheet).where(IntakeSheet.name == name, IntakeSheet.checksum == digest))
    if existing is not None:
        return existing, False  # retry of the same table and name: reopen the same sheet, no second batch
    convention = ensure_default_convention(db)
    now = time.time()
    sheet = IntakeSheet(
        name=name, source=(data.source or '').strip(), status='open',
        raw_rows=copy.deepcopy(data.rows), checksum=digest, row_count=len(data.rows),
        convention=copy.deepcopy(convention.config), convention_version=convention.version,
        decision_seq=1, created_by=user.id, created_at=now, idempotency_key=key,
    )
    db.add(sheet)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(IntakeSheet).where(IntakeSheet.name == name, IntakeSheet.checksum == digest))
        if existing is not None:
            return existing, False
        raise HTTPException(409, '同一张表正在受理，请刷新后重试')

    first_seen = {}
    position = 0
    for index, raw in enumerate(data.rows):
        row = IntakeRow(sheet_id=sheet.id, row_index=index, raw=copy.deepcopy(raw), draft={}, revision=1, updated_at=now)
        db.add(row)
        normalized, problem = evaluate(raw, sheet.convention)
        if problem is None and sheet.convention.get('asset_unique', True) and normalized['asset'] in first_seen:
            prior = first_seen[normalized['asset']]
            _quarantine(row, 'asset_conflict', f'{REASONS["asset_conflict"]}：资产号 {normalized["asset"]} 已在第 {prior + 1} 行接纳；可修正资产号后重放，或留待裁决。', True)
        elif problem is not None:
            _quarantine(row, *problem)
        else:
            first_seen[normalized['asset']] = index
            position += 1
            _accept(db, sheet, row, normalized, position)
    db.flush()
    _refresh_counts(db, sheet)
    _sync_batch(db, sheet)
    db.flush()
    return sheet, True


def replay(db, sheet_id, row_id, data):
    sheet = _sheet_for_update(db, sheet_id)
    if sheet.status != 'open':
        raise HTTPException(409, '清理单已结清，逐行去向不可再改')
    _require_seq(sheet, data.expected_seq)
    row = db.get(IntakeRow, row_id)
    if row is None or row.sheet_id != sheet.id:
        raise HTTPException(404, '清理行不存在')
    if row.status == 'accepted':
        raise HTTPException(409, '该行已接纳，不会再次写入可试算批次')
    fields = {field['key']: field for field in sheet.convention['fields']}
    unknown = set(data.corrections or {}) - set(fields)
    if unknown:
        raise HTTPException(422, f'修正只能使用约定字段：{" / ".join(fields)}')
    _claim_decision(db, sheet)
    row.draft = {**(row.draft or {}), **{k: v for k, v in data.corrections.items() if v not in (None, '')}}
    row.replay_count += 1
    row.revision += 1
    normalized, problem = evaluate(_merge(row.raw, row.draft), sheet.convention)
    if problem is not None:
        _quarantine(row, *problem)
    else:
        conflict = _conflict_row(db, sheet.id, normalized['asset'], row.id) if sheet.convention.get('asset_unique', True) else None
        if conflict is not None:
            _quarantine(row, 'asset_conflict', f'{REASONS["asset_conflict"]}：资产号 {normalized["asset"]} 已在第 {conflict.row_index + 1} 行接纳；可改资产号后重放，或留待裁决。', True)
        else:
            _accept(db, sheet, row, normalized, _next_position(db, sheet.id))
            _sync_batch(db, sheet)
    row.updated_at = time.time()
    _refresh_counts(db, sheet)
    db.flush()
    return sheet, row


def adjudicate(db, sheet_id, row_id, data, user):
    sheet = _sheet_for_update(db, sheet_id)
    if sheet.status != 'open':
        raise HTTPException(409, '清理单已结清，逐行去向不可再改')
    _require_seq(sheet, data.expected_seq)
    row = db.get(IntakeRow, row_id)
    if row is None or row.sheet_id != sheet.id:
        raise HTTPException(404, '清理行不存在')
    if row.status == 'accepted':
        raise HTTPException(409, '已接纳件不可裁决，也不能被覆盖')
    if data.decision == 'accepted':
        raise HTTPException(409, '资产冲突不能通过覆盖已接纳件解决；请修正资产号后重放，或裁决为排除')
    if data.decision not in ('excluded', 'retained'):
        raise HTTPException(422, '裁决只能是 excluded（排除）或 retained（暂缓）')
    _claim_decision(db, sheet)
    path = 'excluded' if data.decision == 'excluded' else 'pending'
    row.resolution = path
    row.needs_adjudication = row.needs_adjudication and path == 'pending'
    row.resolution_note = data.note.strip()
    row.revision += 1
    row.updated_at = time.time()
    _refresh_counts(db, sheet)
    db.flush()
    return sheet, row


def _recover(db, sheet):
    """Re-derive per-row accounting from persisted rows after an interruption.

    Idempotent: appends only accepted rows missing from the trial batch and
    corrects the sheet counters. It never rewrites an accepted row or record."""
    rows = list(db.scalars(select(IntakeRow).where(IntakeRow.sheet_id == sheet.id).order_by(IntakeRow.row_index)))
    if len(rows) != sheet.row_count or len(sheet.raw_rows) != sheet.row_count:
        raise HTTPException(409, '原始行数与清理行数不一致，无法恢复逐行去向')
    _refresh_counts(db, sheet)
    _sync_batch(db, sheet)
    db.flush()
    return rows


def reconcile(db, sheet):
    rows = _recover(db, sheet)
    accepted = [row for row in rows if row.status == 'accepted']
    quarantined = [row for row in rows if row.status == 'quarantined']
    pending = [row for row in quarantined if row.resolution == 'pending']
    conflicts = [row for row in quarantined if row.needs_adjudication]
    checks = []

    def check(key, ok, detail):
        checks.append({'key': key, 'ok': bool(ok), 'detail': detail})

    check('row_count', len(rows) == sheet.row_count == len(sheet.raw_rows),
          f'原始行 {len(sheet.raw_rows)} / 清理行 {len(rows)} / 单头原行 {sheet.row_count}')
    check('row_accounting', len(rows) == len(accepted) + len(quarantined),
          f'清理行 {len(rows)} = 接纳 {len(accepted)} + 隔离 {len(quarantined)}')
    check('counters', sheet.accepted_count == len(accepted) and sheet.quarantined_count == len(quarantined),
          f'单头计数 接纳 {sheet.accepted_count} / 隔离 {sheet.quarantined_count} 与逐行合计一致')
    check('raw_preserved', len(sheet.raw_rows) == len(rows) and all(row.raw == sheet.raw_rows[row.row_index] for row in rows), '每一行都保留了受理时的原始内容')
    positions = [row.batch_position for row in accepted]
    check('positions', all(isinstance(p, int) and p >= 1 for p in positions) and len(set(positions)) == len(positions),
          '接纳件批次位置唯一且为正整数')
    duplicates = {}
    for row in accepted:
        duplicates[row.asset] = duplicates.get(row.asset, 0) + 1
    duplicated = [asset for asset, count in duplicates.items() if count > 1]
    check('asset_unique', not duplicated, '可试算批次内资产号无重复' if not duplicated else f'重复资产：{", ".join(duplicated)}')

    batch = db.get(Dataset, sheet.batch_id) if sheet.batch_id else None
    records = batch.records if batch else []
    check('batch_exists', (not accepted) or batch is not None,
          '有接纳件即存在可试算批次' if not accepted else f'可试算批次 #{batch.id if batch else "—"}')
    check('batch_size', len(records) == len(accepted),
          f'批次记录 {len(records)} = 接纳件 {len(accepted)}')
    ordered = sorted(accepted, key=lambda row: row.batch_position or 0)
    destinations = []
    matches = True
    for index, row in enumerate(ordered):
        destination = records[index] if index < len(records) else None
        same = destination == row.normalized
        matches = matches and same
        destinations.append({'row_id': row.id, 'row_index': row.row_index, 'batch_position': row.batch_position,
                             'asset': row.asset, 'matches': same, 'record': destination})
    check('batch_destinations', matches, '每个接纳件都能在批次中找到同值记录' if matches else '存在接纳件与批次记录不一致')
    if batch is not None:
        check('batch_checksum', batch.checksum == checksum(records), '批次内容指纹与记录一致')
    check('settle_ready', not pending,
          '无待处理隔离件，可以结清' if not pending else f'尚有 {len(pending)} 件隔离件待处理，不能结清')
    return {
        'ok': all(item['ok'] for item in checks),
        'checks': checks,
        'counts': {'raw': sheet.row_count, 'accepted': len(accepted), 'quarantined': len(quarantined),
                   'pending': len(pending), 'conflicts': len(conflicts)},
        'batch': {'id': batch.id, 'name': batch.name, 'rows': len(records), 'checksum': batch.checksum} if batch else None,
        'destinations': destinations,
    }


def settle(db, sheet_id, data, user):
    sheet = _sheet_for_update(db, sheet_id)
    if sheet.status == 'settled':
        raise HTTPException(409, '清理单已结清，不能重复结清')
    _require_seq(sheet, data.expected_seq)
    rows = list(db.scalars(select(IntakeRow).where(IntakeRow.sheet_id == sheet.id)))
    accepted = sum(1 for row in rows if row.status == 'accepted')
    quarantined = sum(1 for row in rows if row.status == 'quarantined')
    pending = [row for row in rows if row.status == 'quarantined' and row.resolution == 'pending']
    if pending:
        raise HTTPException(409, f'尚有 {len(pending)} 件隔离件待处理，不能结清；请修正重放或裁决排除')
    if accepted + quarantined != sheet.row_count or len(rows) != sheet.row_count:
        raise HTTPException(409, '行数核对失败，不能结清')
    report = reconcile(db, sheet)
    if not report['ok']:
        failed = '；'.join(item['detail'] for item in report['checks'] if not item['ok'])
        raise HTTPException(409, f'批次去向核对未通过，不能结清：{failed}')
    _claim_decision(db, sheet)
    sheet.status = 'settled'
    sheet.settled_at = time.time()
    sheet.settled_by = user.id
    db.flush()
    return sheet


# ---------- serialization ----------

def sheet_dict(db, sheet, include_convention=False):
    pending = db.scalar(select(func.count()).select_from(IntakeRow).where(
        IntakeRow.sheet_id == sheet.id, IntakeRow.status == 'quarantined', IntakeRow.resolution == 'pending'))
    conflicts = db.scalar(select(func.count()).select_from(IntakeRow).where(
        IntakeRow.sheet_id == sheet.id, IntakeRow.status == 'quarantined', IntakeRow.needs_adjudication == True))
    data = {
        'id': sheet.id, 'name': sheet.name, 'source': sheet.source, 'status': sheet.status,
        'row_count': sheet.row_count, 'accepted_count': sheet.accepted_count, 'quarantined_count': sheet.quarantined_count,
        'pending_count': pending or 0, 'conflict_count': conflicts or 0,
        'batch_id': sheet.batch_id, 'checksum': sheet.checksum,
        'convention_version': sheet.convention_version, 'decision_seq': sheet.decision_seq,
        'created_at': sheet.created_at, 'settled_at': sheet.settled_at, 'settled_by': sheet.settled_by,
    }
    if include_convention:
        data['convention'] = sheet.convention
    return data


def row_dict(row):
    return {
        'id': row.id, 'row_index': row.row_index, 'raw': row.raw, 'draft': row.draft,
        'status': row.status, 'reason_code': row.reason_code, 'reason': row.reason,
        'asset': row.asset, 'normalized': row.normalized, 'needs_adjudication': row.needs_adjudication,
        'resolution': row.resolution, 'resolution_note': row.resolution_note,
        'replay_count': row.replay_count, 'revision': row.revision,
        'batch_position': row.batch_position, 'updated_at': row.updated_at,
    }
