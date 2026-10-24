from datetime import datetime,timezone
from uuid import uuid4
import asyncio
import pytest
import httpx
import jwt
from pydantic import ValidationError
from app.auth.security import hash_password,verify_password
from app.schemas.events import KavachEvent
from app.main import app
from app.core.config import settings
from app.db.session import SessionLocal
from app.models import User,Role,AuditLog,KavachEventRecord,RevokedToken
async def request(method,path,**kwargs):
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        return await client.request(method,path,**kwargs)
def test_password_hashing():
    h=hash_password("Long-development-password-82!")
    assert h!="Long-development-password-82!" and verify_password("Long-development-password-82!",h) and not verify_password("wrong",h)
def sample(**overrides):
    data=dict(event_id=uuid4(),timestamp=datetime.now(timezone.utc),source_module="SYSTEM",event_type="heartbeat",severity="INFO",security_risk=0,situation_risk=0,network_health=100,priority="P3",recommended_action="ALLOW",evidence={},confidence=1,status="NEW",simulation=False);data.update(overrides);return data
def test_event_schema_validates_ranges_and_timezone():
    assert KavachEvent(**sample()).timestamp.tzinfo
    with pytest.raises(ValidationError):KavachEvent(**sample(security_risk=1.1))
    with pytest.raises(ValidationError):KavachEvent(**sample(timestamp=datetime.now()))
def test_health_endpoints():
    assert asyncio.run(request('GET','/api/v1/health')).json()['status']=='ok'
    assert asyncio.run(request('GET','/api/v1/health/ready')).status_code==200
def test_registration_disabled_by_default():
    response=asyncio.run(request('POST','/api/v1/auth/register',json={'username':'test-user','email':'test.user@gmail.com','password':'long-password-x'}))
    assert response.status_code==404
def test_protected_endpoint_requires_auth():
    assert asyncio.run(request('GET','/api/v1/dashboard/summary')).status_code==401
def test_login_current_user_and_logout_invalidation():
    suffix=uuid4().hex; password="Integration-password-183!"; db=SessionLocal()
    user=User(username=f"login-{suffix}",email=f"login-{suffix}@gmail.com",password_hash=hash_password(password),role=Role.ADMIN,is_active=True)
    db.add(user); db.commit(); db.refresh(user); uid=user.id; token=None
    try:
        failed=asyncio.run(request('POST','/api/v1/auth/login',json={'identity':user.username,'password':'incorrect-password'}))
        assert failed.status_code==401
        logged=asyncio.run(request('POST','/api/v1/auth/login',json={'identity':user.username,'password':password}))
        assert logged.status_code==200; token=logged.json()['access_token']; headers={'Authorization':f'Bearer {token}'}
        assert asyncio.run(request('GET','/api/v1/auth/me',headers=headers)).json()['id']==uid
        assert asyncio.run(request('POST','/api/v1/auth/logout',headers=headers)).status_code==200
        assert asyncio.run(request('GET','/api/v1/auth/me',headers=headers)).status_code==401
    finally:
        db.query(AuditLog).filter(AuditLog.actor_user_id==uid).delete(synchronize_session=False)
        if token:
            jti=jwt.decode(token,settings.secret_key,algorithms=['HS256'],options={'verify_exp':False})['jti']
            db.query(RevokedToken).filter(RevokedToken.jti==jti).delete(synchronize_session=False)
        db.query(User).filter(User.id==uid).delete(synchronize_session=False); db.commit(); db.close()
def test_role_enforcement_and_event_dashboard_persistence():
    suffix=uuid4().hex; password="Integration-password-183!"; db=SessionLocal()
    admin=User(username=f"admin-{suffix}",email=f"admin-{suffix}@gmail.com",password_hash=hash_password(password),role=Role.ADMIN,is_active=True)
    viewer=User(username=f"viewer-{suffix}",email=f"viewer-{suffix}@gmail.com",password_hash=hash_password(password),role=Role.VIEWER,is_active=True)
    db.add_all([admin,viewer]); db.commit(); db.refresh(admin); db.refresh(viewer); ids=[admin.id,viewer.id]; tokens=[]; event_id=str(uuid4())
    event={'event_id':event_id,'timestamp':datetime.now(timezone.utc).isoformat(),'source_module':'SYSTEM','event_type':'test_event','severity':'INFO','security_risk':0.1,'situation_risk':0.2,'network_health':98,'priority':'P3','recommended_action':'STORE','evidence':{'source':'automated-test'},'confidence':0.9,'status':'NEW','correlation_id':None,'simulation':True}
    try:
        auth=[]
        for username in (admin.username,viewer.username):
            result=asyncio.run(request('POST','/api/v1/auth/login',json={'identity':username,'password':password})); assert result.status_code==200
            token=result.json()['access_token'];tokens.append(token);auth.append({'Authorization':f'Bearer {token}'})
        assert asyncio.run(request('POST','/api/v1/events',headers=auth[0],json=event)).status_code==201
        assert asyncio.run(request('POST','/api/v1/events',headers=auth[1],json=event|{'event_id':str(uuid4())})).status_code==403
        assert asyncio.run(request('GET','/api/v1/users',headers=auth[1])).status_code==403
        summary=asyncio.run(request('GET','/api/v1/dashboard/summary',headers=auth[0])).json()
        assert any(row['event_id']==event_id for row in summary['recent_events'])
        assert summary['total_events']>=1
    finally:
        db.query(AuditLog).filter(AuditLog.actor_user_id.in_(ids)).delete(synchronize_session=False)
        db.query(KavachEventRecord).filter(KavachEventRecord.event_id==event_id).delete(synchronize_session=False)
        for token in tokens:
            jti=jwt.decode(token,settings.secret_key,algorithms=['HS256'],options={'verify_exp':False})['jti']
            db.query(RevokedToken).filter(RevokedToken.jti==jti).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False); db.commit(); db.close()
