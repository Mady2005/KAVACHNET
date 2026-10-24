from datetime import datetime, timedelta, timezone
import uuid, jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from app.core.config import settings
ph=PasswordHasher()
def hash_password(password:str)->str: return ph.hash(password)
def verify_password(password:str, hashed:str)->bool:
    try:return ph.verify(hashed,password)
    except (VerifyMismatchError,VerificationError):return False
def issue_token(user_id:str):
    now=datetime.now(timezone.utc); exp=now+timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub":user_id,"iat":now,"exp":exp,"jti":str(uuid.uuid4())},settings.secret_key,algorithm="HS256")
