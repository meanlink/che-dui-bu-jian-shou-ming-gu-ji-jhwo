import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, IntakeSheet
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import intake
from src.schemas.intake import IntakeCreate
from src.utils.security import hash_password
from src.utils.serialization import checksum
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        admin=db.scalar(select(User).where(User.role=='admin'))
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        if not db.scalar(select(IntakeSheet).where(IntakeSheet.name=='11 月供应商月表清理')):
            rows=[dict(row) for row in d.records]
            rows[5]={'asset':rows[5]['asset'],'model':'AC-8','km':'','failed':1}
            rows[17]={'asset':rows[17]['asset'],'model':'AC-8','km':abs(float(rows[17]['km'])),'failed':'待确认'}
            rows[29]={'asset':rows[0]['asset'],'model':'AC-12','km':52000,'failed':0}
            rows[41]={'asset':'XB-41','model':'AC-13','km':61000,'failed':1}
            intake.create(db,IntakeCreate(name='11 月供应商月表清理',source='供应商月表-202611.csv',rows=rows,idempotency_key='seed-intake-nov'),admin)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,IntakeSheet]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
