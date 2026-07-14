from sqlmodel import Relationship, SQLModel, Field
from pydantic import BaseModel
from .permissions import Permissions

class UserIdAndPassword(BaseModel):
    username: str
    password: str

class PasswordResetRequest(BaseModel):
    username: str

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

class TotpCode(BaseModel):
    code: str

class TotpLogin(BaseModel):
    mfa_token: str
    code: str

class EmailUpdate(BaseModel):
    email: str

class EmailVerify(BaseModel):
    token: str

class WikiUser(SQLModel, table=True):
    username: str = Field(primary_key=True)
    password: str
    permission: str
    bio: str
    email: str | None = Field(default=None, unique=True, index=True)
    email_verified: bool = Field(default=False)
    totp_secret: str | None = Field(default=None)
    totp_enabled: bool = Field(default=False)
    # 마지막으로 인증에 성공한 TOTP 타임스텝. 같은 스텝의 코드 재사용을 막는다(single-use).
    totp_last_step: int | None = Field(default=None)