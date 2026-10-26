import secrets
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.session import get_db
from app.models import RevokedToken, User

bearer=HTTPBearer(auto_error=False)

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
