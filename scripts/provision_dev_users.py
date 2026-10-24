"""Idempotently provision local development demo users (development only)."""
import getpass, os, secrets, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from sqlalchemy import select
from app.core.config import settings
from app.db.session import SessionLocal
from app.models import User,Role
from app.auth.security import hash_password
if settings.environment.lower() not in ("development","dev","local"):
    raise SystemExit("Refusing to provision demo users unless ENVIRONMENT is development/dev/local")
db=SessionLocal(); print("KAVACHNET local development user provisioning")
try:
    for role in Role:
        username=role.value.lower(); email=f"{username}@kavachnet.local"
        user=db.scalar(select(User).where(User.username==username))
        if user:
            print(f"Existing {role.value}: {username} (password unchanged)"); continue
        envkey=f"KAVACH_{role.value}_PASSWORD"; password=os.getenv(envkey)
        if not password: password=getpass.getpass(f"Set password for {role.value} (leave blank to generate): ")
        generated=not password; password=password or secrets.token_urlsafe(18)
        user=User(username=username,email=email,password_hash=hash_password(password),role=role,is_active=True)
        db.add(user); db.commit()
        print(f"Created {role.value} user {username}; password: {password}" if generated or os.getenv(envkey) else f"Created {role.value} user {username}")
finally: db.close()
