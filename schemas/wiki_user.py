from sqlmodel import Relationship, SQLModel, Field
from pydantic import BaseModel
from .permissions import Permissions
from typing import Optional
from datetime import datetime

class UserRegisterForm(BaseModel):
    username: str
    password: str
    email: str
    verification_token: str | None = None

class RegisterEmailRequest(BaseModel):
    username: str
    email: str

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

class BioUpdate(BaseModel):
    bio: str

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


class EmailVerification(SQLModel, table=True):
    """임시 이메일 인증 요청을 서버에 저장한다.

    토큰 기반 인증을 보조하여 다른 기기에서 인증이 완료된 상태를 서버에서
    조회할 수 있게 한다. 토큰(메일 링크)은 `token` 필드에 저장되며,
    `verified`가 True가 되면 인증 완료로 처리한다.
    """
    id: int | None = Field(default=None, primary_key=True)
    username: str
    email: str
    token: str
    verified: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = Field(default=None)