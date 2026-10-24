from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from app.models.entities import Role
class UserOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:str; username:str; email:str; role:Role; is_active:bool; created_at:datetime; last_login_at:datetime|None
class LoginIn(BaseModel): identity:str=Field(min_length=1,max_length=255); password:str=Field(min_length=1,max_length=256)
class RegisterIn(BaseModel): username:str=Field(min_length=3,max_length=64,pattern=r"^[a-zA-Z0-9_.-]+$"); email:EmailStr; password:str=Field(min_length=12,max_length=256)
class LoginOut(BaseModel): access_token:str; token_type:str="bearer"; user:UserOut
class DatasetIn(BaseModel): name:str; owning_member:str; source_url:str|None=None; license:str|None=None; description:str; provenance:dict={}; schema_metadata:dict={}
class DatasetOut(DatasetIn):
    model_config=ConfigDict(from_attributes=True)
    dataset_id:str; import_status:str; created_at:datetime
