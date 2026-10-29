from datetime import datetime, timezone
from collections import defaultdict, deque
import secrets
from threading import Lock
from time import monotonic
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import jwt
from app.auth.security import issue_token, hash_password, verify_password
from app.core.config import settings
from app.db.session import get_db, engine
from app.models import User, AuditLog, KavachEventRecord, DecisionRecord, DatasetRegistry, RevokedToken, Role
from app.schemas.events import KavachEvent
from app.schemas.api import UserOut, DatasetIn, DatasetOut, DatasetPatch, LoginIn, LoginOut, RegisterIn, RoleUpdateIn

router=APIRouter(); bearer=HTTPBearer(auto_error=False)
_login_failures=defaultdict(deque); _login_lock=Lock(); _LOGIN_WINDOW_SECONDS=900; _LOGIN_MAX_FAILURES=5
def audit(db, actor, action, resource, outcome, details=None, rid=None):
    db.add(AuditLog(actor_user_id=actor,action=action,resource_type=resource,resource_id=rid,outcome=outcome,details=details or {})); db.commit()
def current_user(request:Request, creds:HTTPAuthorizationCredentials|None=Depends(bearer), db:Session=Depends(get_db)):
    token=creds.credentials if creds else request.cookies.get("kavach_session")
    if not token: raise HTTPException(401,"Authentication required")
    if not creds and request.method not in {"GET","HEAD","OPTIONS"}:
        csrf_cookie=request.cookies.get("kavach_csrf",""); csrf_header=request.headers.get("X-CSRF-Token","")
        if not csrf_cookie or not secrets.compare_digest(csrf_cookie,csrf_header): raise HTTPException(403,"CSRF validation failed")
    try: payload=jwt.decode(token,settings.secret_key,algorithms=["HS256"])
    except jwt.PyJWTError: raise HTTPException(401,"Invalid or expired credentials")
    if db.get(RevokedToken,payload.get("jti")): raise HTTPException(401,"Session revoked")
    user=db.get(User,payload.get("sub"))
    if not user or not user.is_active: raise HTTPException(401,"Invalid or expired credentials")
    return user
def roles(*allowed):
    def check(user:User=Depends(current_user)):
        if user.role not in allowed: raise HTTPException(403,"Insufficient permissions")
        return user
    return check
@router.post("/auth/login",response_model=LoginOut)
def login(body:LoginIn, response:Response, db:Session=Depends(get_db)):
    identity=body.identity.strip(); password=body.password
    key=identity.casefold(); now=monotonic()
    with _login_lock:
        failures=_login_failures[key]
        while failures and now-failures[0]>=_LOGIN_WINDOW_SECONDS: failures.popleft()
        if len(failures)>=_LOGIN_MAX_FAILURES: raise HTTPException(429,"Too many failed sign-in attempts. Try again later.")
    user=db.scalar(select(User).where((User.username==identity)|(User.email==identity.lower())))
    if not user or not user.is_active or not verify_password(password,user.password_hash):
        with _login_lock: _login_failures[key].append(now)
        audit(db,user.id if user else None,"auth.login","session","denied",{"identity_supplied":bool(identity)})
        raise HTTPException(401,"Invalid username/email or password")
    with _login_lock: _login_failures.pop(key,None)
    user.last_login_at=datetime.now(timezone.utc); db.commit(); token=issue_token(user.id)
    audit(db,user.id,"auth.login","session","success")
    response.set_cookie("kavach_session",token,httponly=True,secure=settings.cookie_secure,samesite="lax",max_age=settings.access_token_minutes*60,path="/")
    response.set_cookie("kavach_csrf",secrets.token_urlsafe(32),httponly=False,secure=settings.cookie_secure,samesite="lax",max_age=settings.access_token_minutes*60,path="/")
    return {"access_token":token,"token_type":"bearer","user":UserOut.model_validate(user)}
@router.post("/auth/logout")
def logout(request:Request,response:Response, user:User=Depends(current_user), creds:HTTPAuthorizationCredentials|None=Depends(bearer), db:Session=Depends(get_db)):
    token=creds.credentials if creds else request.cookies.get("kavach_session")
    if token:
        payload=jwt.decode(token,settings.secret_key,algorithms=["HS256"])
        db.add(RevokedToken(jti=payload["jti"],expires_at=datetime.fromtimestamp(payload["exp"],timezone.utc)))
    response.delete_cookie("kavach_session",path="/"); response.delete_cookie("kavach_csrf",path="/"); audit(db,user.id,"auth.logout","session","success"); return {"status":"logged_out"}
@router.get("/auth/me",response_model=UserOut)
def me(user:User=Depends(current_user)): return user
@router.post("/auth/register",status_code=201,response_model=UserOut)
def register(body:RegisterIn,db:Session=Depends(get_db)):
    if not settings.registration_enabled: raise HTTPException(404,"Registration is disabled")
    if db.scalar(select(User).where((User.username==body.username)|(User.email==str(body.email).lower()))): raise HTTPException(409,"Account already exists")
    user=User(username=body.username,email=str(body.email).lower(),password_hash=hash_password(body.password),role=Role.VIEWER); db.add(user); db.commit(); db.refresh(user); audit(db,user.id,"user.register","user","success"); return user
@router.get("/health")
def health(): return {"status":"ok","service":"api"}
@router.get("/health/ready")
def ready(db:Session=Depends(get_db)):
    try: db.execute(select(1)); return {"status":"ready","database":"ok"}
    except Exception: raise HTTPException(503,"Database unavailable")
@router.get("/system/status")
def system_status(user:User=Depends(current_user),db:Session=Depends(get_db)):
    db.execute(select(1)); return {"status":"operational","api":"ok","database":"ok","modules":{"security":"not_configured","edge_ai":"not_configured","network":"not_configured"}}
@router.get("/dashboard/summary")
def summary(user:User=Depends(current_user),db:Session=Depends(get_db)):
    count=db.scalar(select(func.count()).select_from(KavachEventRecord)) or 0
    high=db.scalar(select(func.count()).select_from(KavachEventRecord).where(KavachEventRecord.severity.in_(["HIGH","CRITICAL"]))) or 0
    pending=db.scalar(select(func.count()).select_from(KavachEventRecord).where(KavachEventRecord.status.in_(["NEW","QUEUED"]))) or 0
    dist={row[0]:row[1] for row in db.execute(select(KavachEventRecord.source_module,func.count()).group_by(KavachEventRecord.source_module))}
    recent=db.scalars(select(KavachEventRecord).order_by(KavachEventRecord.timestamp.desc()).limit(10)).all()
    return {"total_events":count,"high_critical_events":high,"awaiting_action":pending,"by_module":dist,"recent_events":[event_dict(x) for x in recent],"system_status":"operational"}
def event_dict(e):
    values={k:getattr(e,k) for k in ("event_id","timestamp","source_module","event_type","severity","security_risk","situation_risk","network_health","priority","recommended_action","evidence","confidence","status","correlation_id","simulation")}
    stamp=values["timestamp"]
    values["timestamp"]=stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    return values
@router.get("/events")
def events(offset:int=0,limit:int=50,user:User=Depends(current_user),db:Session=Depends(get_db)):
    if limit>200: raise HTTPException(422,"limit must be at most 200")
    return {"items":[event_dict(e) for e in db.scalars(select(KavachEventRecord).order_by(KavachEventRecord.timestamp.desc()).offset(offset).limit(limit)).all()],"offset":offset,"limit":limit}
@router.get("/events/{event_id}")
def event_detail(event_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    e=db.get(KavachEventRecord,event_id)
    if not e: raise HTTPException(404,"Event not found")
    return event_dict(e)
@router.post("/events",status_code=201)
def create_event(event:KavachEvent,response:Response,user:User=Depends(roles(Role.ADMIN,Role.SECURITY_ANALYST,Role.EDGE_OPERATOR,Role.NETWORK_OPERATOR)),db:Session=Depends(get_db)):
    permitted_sources={
        Role.SECURITY_ANALYST: {"SECURITY"},
        Role.EDGE_OPERATOR: {"EDGE_AI"},
        Role.NETWORK_OPERATOR: {"NETWORK"},
    }
    if user.role != Role.ADMIN and event.source_module.value not in permitted_sources.get(user.role, set()):
        raise HTTPException(403,"Role may only submit events for its assigned module")
    existing=db.get(KavachEventRecord,str(event.event_id))
    if existing:
        stored = _replay_or_conflict(existing,event,response)
        _correlate_event(str(event.event_id), user, response, db)
        return stored
    values=event.model_dump(mode="json")
    values.update(event_id=str(event.event_id),timestamp=event.timestamp)
    values={k:(v.value if hasattr(v,"value") else v) for k,v in values.items()}
    e=KavachEventRecord(**values); db.add(e)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing=db.get(KavachEventRecord,str(event.event_id))
        if existing:
            return _replay_or_conflict(existing,event,response)
        raise HTTPException(409,"Event could not be created because of a concurrent conflict")
    audit(db,user.id,"event.create","event","success",rid=str(event.event_id))
    _correlate_event(str(event.event_id), user, response, db)
    return event

def _correlate_event(event_id, user, response, db):
    from app.modules.edge_ai.correlation_service import correlate_persisted_event
    try:
        incident = correlate_persisted_event(db, event_id, actor_user_id=getattr(user, "id", None))
    except Exception as exc:
        response.headers["X-Persisted-Event-ID"] = event_id
        raise HTTPException(503, "Event persisted; incident correlation failed. Retry the same event_id.", headers={"X-Persisted-Event-ID": event_id}) from exc
    if incident:
        response.headers["X-Incident-ID"] = incident.incident_id
        response.headers["X-Incident-Status"] = incident.status

def _replay_or_conflict(existing:KavachEventRecord,event:KavachEvent,response:Response):
    stored=KavachEvent.model_validate(event_dict(existing))
    if stored.model_dump(mode="json") != event.model_dump(mode="json"):
        raise HTTPException(409,"event_id is already associated with a different event payload")
    response.status_code=200
    response.headers["Idempotency-Replayed"]="true"
    return stored
@router.get("/decisions")
def decisions(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return [{"id":x.id,"event_id":x.event_id,"selected_action":x.selected_action,"policy_rule_id":x.policy_rule_id,"explanation":x.explanation,"input_scores":x.input_scores,"timestamp":x.timestamp} for x in db.scalars(select(DecisionRecord).order_by(DecisionRecord.timestamp.desc()).limit(100)).all()]
@router.get("/audit-logs")
def audit_logs(user:User=Depends(roles(Role.ADMIN)),db:Session=Depends(get_db)):
    return [{"id":x.id,"actor_user_id":x.actor_user_id,"action":x.action,"resource_type":x.resource_type,"resource_id":x.resource_id,"timestamp":x.timestamp,"outcome":x.outcome,"details":x.details} for x in db.scalars(select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(200)).all()]
@router.get("/users",response_model=list[UserOut])
def users(user:User=Depends(roles(Role.ADMIN)),db:Session=Depends(get_db)): return db.scalars(select(User).order_by(User.username)).all()
@router.patch("/users/{user_id}/role",response_model=UserOut)
def update_role(user_id:str,body:RoleUpdateIn,user:User=Depends(roles(Role.ADMIN)),db:Session=Depends(get_db)):
    target=db.get(User,user_id)
    if not target: raise HTTPException(404,"User not found")
    role=body.role
    if target.is_active and target.role == Role.ADMIN and role != Role.ADMIN:
        active_admins=db.scalar(select(func.count()).select_from(User).where(User.role==Role.ADMIN,User.is_active.is_(True))) or 0
        if active_admins <= 1: raise HTTPException(409,"Cannot demote the last active administrator")
    old=target.role.value; target.role=role; db.commit(); audit(db,user.id,"user.role.update","user","success",{"from":old,"to":role.value},user_id); return target
@router.get("/datasets",response_model=list[DatasetOut])
def datasets(user:User=Depends(current_user),db:Session=Depends(get_db)): return db.scalars(select(DatasetRegistry).order_by(DatasetRegistry.created_at.desc())).all()
@router.post("/datasets",response_model=DatasetOut,status_code=201)
def add_dataset(data:DatasetIn,user:User=Depends(roles(Role.ADMIN)),db:Session=Depends(get_db)):
    row=DatasetRegistry(**data.model_dump()); db.add(row); db.commit(); db.refresh(row); audit(db,user.id,"dataset.create","dataset","success",rid=row.dataset_id); return row
@router.patch("/datasets/{dataset_id}",response_model=DatasetOut)
def edit_dataset(dataset_id:str,data:DatasetPatch,user:User=Depends(roles(Role.ADMIN)),db:Session=Depends(get_db)):
    row=db.get(DatasetRegistry,dataset_id)
    if not row: raise HTTPException(404,"Dataset not found")
    values=data.model_dump(exclude_unset=True)
    nullable_fields={"source_url","license","checksum"}
    for key,value in values.items():
        if value is None and key not in nullable_fields: raise HTTPException(422,f"{key} cannot be null")
        setattr(row,key,value)
    db.commit();db.refresh(row);audit(db,user.id,"dataset.update","dataset","success",rid=dataset_id);return row
