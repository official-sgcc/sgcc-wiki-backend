"""회원가입·로그인, 2FA, 이메일 인증, 비밀번호 재설정 엔드포인트."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from core.config import FRONTEND_URL, RESERVED_USERNAMES, limiter, logger
from core.database import engine
from core.deps import get_current_user
from core.login_utils import (
    hash_password, verify_password, create_jwt_token,
    validate_username, validate_password, validate_email,
    create_mfa_token, verify_mfa_token,
    create_password_reset_token, read_reset_token_subject, verify_password_reset_token,
    verify_email_verification_token,
    generate_totp_secret, totp_provisioning_uri, matched_totp_step,
    DUMMY_PASSWORD_HASH, PASSWORD_RESET_EXPIRE_MINUTES,
)
from core.maintenance import send_email, send_email_verification
from schemas.wiki_doc import WikiDocVersion
from schemas.wiki_user import (
    WikiUser, UserIdAndPassword,
    PasswordResetRequest, PasswordResetConfirm, TotpCode, TotpLogin,
    EmailUpdate, EmailVerify,
)

router = APIRouter()

@router.post('/register')
@limiter.limit('3/minute')
async def register_user(request: Request, user_info: UserIdAndPassword):
    """새 사용자를 등록한다. (rate limit: IP당 분당 3회)

    username(3~32자, [a-zA-Z0-9_-])과 password(8자 이상, 영문+숫자 포함) 정책을
    검증한 뒤, 중복 아이디와 예약어(RESERVED_USERNAMES)를 거부한다. 신규 계정 권한은
    'login_user'이며 비밀번호는 bcrypt로 해시해 저장한다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        user_info: 가입 정보(username, password).

    Returns:
        dict: `{'message': 'User ... has been registered successfully.'}`

    Raises:
        HTTPException 400: username/password 정책 위반, 중복 아이디, 또는 예약어일 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    validate_username(user_info.username)
    validate_password(user_info.password)

    with Session(engine) as session:
        if session.get(WikiUser, user_info.username):
            raise HTTPException(status_code=400, detail='Username already exists.')

        if user_info.username.lower() in RESERVED_USERNAMES:
            raise HTTPException(
                status_code=400,
                detail='This username is reserved and cannot be used.',
            )

        user = WikiUser(username=user_info.username, password=hash_password(user_info.password), permission='login_user', bio='', email=None)

        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info('user registered: %s', user_info.username)
        return {'message': f'User {user_info.username} has been registered successfully.'}

@router.get('/users/{username}')
async def get_user_info(username: str, current_user: WikiUser = Depends(get_current_user)):
    """사용자 프로필과 편집 이력을 조회한다. (인증 선택)

    사용자가 작성한 모든 문서 버전을 최신순으로 함께 반환한다. password와 totp_secret은
    항상 제외하며, 본인 조회(토큰의 사용자 == 조회 대상)가 아니면 email도 숨긴다.

    Args:
        username: 조회 대상 사용자명.
        current_user: 인증 사용자(본인 여부 판별용). 없으면 비본인으로 취급.

    Returns:
        dict: WikiUser 필드(password 제외, 비본인은 email도 제외)에
              `edit_versions`(list[WikiDocVersion], 최신순)를 더한 객체.

    Raises:
        HTTPException 404: 해당 사용자가 없을 때.
    """
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=404, detail='Cannot find user with the corresponding username.')

        edit_versions = session.exec(
            select(WikiDocVersion)
            .where(WikiDocVersion.updated_by == username)
            .order_by(WikiDocVersion.updated_at.desc())
        ).all()
        if current_user is None or current_user.username != username:
            user_data = user.model_dump(exclude={'password', 'email', 'totp_secret'})
        else:
            user_data = user.model_dump(exclude={'password', 'totp_secret'})
        user_data['edit_versions'] = edit_versions
        return user_data

@router.post('/login')
@limiter.limit('5/minute')
async def login_user(request: Request, user_info: UserIdAndPassword):
    """자격 증명을 검증하고 JWT를 발급한다. (rate limit: IP당 분당 5회)

    보안상 "아이디 없음"과 "비밀번호 불일치"를 구분하지 않고 동일한 401 메시지를
    반환한다(username enumeration 방지). 이 메시지를 분리하지 말 것. 또한 아이디가 없을
    때도 bcrypt 검증을 수행해 응답 시간을 일정하게 유지하여 timing attack을 방지한다.

    2단계 인증: 대상 사용자가 2FA를 켜 두었다면(totp_enabled) 이 단계에서는 진짜
    토큰을 주지 않고 `{'mfa_required': True, 'mfa_token': ...}`를 반환한다. 프론트는
    받은 mfa_token과 인증앱 6자리 코드를 `/login/2fa`로 보내 최종 토큰을 받는다.
    2FA를 쓰지 않는 사용자는 기존과 동일하게 곧바로 `{'token': ...}`를 받는다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        user_info: 로그인 정보(username, password).

    Returns:
        dict: 2FA 미사용 시 `{'token': '<JWT>'}`.
              2FA 사용 시 `{'mfa_required': True, 'mfa_token': '<임시 JWT>'}`.

    Raises:
        HTTPException 401: 아이디가 없거나 비밀번호가 틀렸을 때(동일 메시지).
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    with Session(engine) as session:
        user = session.get(WikiUser, user_info.username)
        password_valid = verify_password(user_info.password, user.password) if user else verify_password(user_info.password, DUMMY_PASSWORD_HASH)
        if not user or not password_valid:
            logger.warning('login failed for username: %s', user_info.username)
            raise HTTPException(status_code=401, detail='Invalid username or password.')

        if user.totp_enabled:
            logger.info('login step 1 ok, awaiting 2fa: %s', user_info.username)
            return {'mfa_required': True, 'mfa_token': create_mfa_token(user_info.username)}

        token = create_jwt_token(user_info.username)
        logger.info('login success: %s', user_info.username)
        return {'token': token}

@router.post('/login/2fa')
@limiter.limit('5/minute')
async def login_verify_2fa(request: Request, mfa_in: TotpLogin):
    """2단계 인증 로그인의 두 번째 단계. (rate limit: IP당 분당 5회)

    `/login`이 발급한 임시 mfa_token과 인증앱 6자리 코드를 검증해 최종 JWT를 발급한다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        mfa_in: `{mfa_token, code}`.

    Returns:
        dict: `{'token': '<JWT>'}`.

    Raises:
        HTTPException 401: mfa_token이 없거나 만료됐을 때, 또는 사용자가 없을 때.
        HTTPException 400: 2FA가 켜져 있지 않거나 코드가 틀렸을 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    username = verify_mfa_token(mfa_in.mfa_token)
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')
        if not user.totp_enabled or not user.totp_secret:
            raise HTTPException(status_code=400, detail='Two-factor authentication is not enabled for this account.')
        step = matched_totp_step(user.totp_secret, mfa_in.code)
        if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
            logger.warning('2fa verification failed: %s', username)
            raise HTTPException(status_code=400, detail='Invalid authentication code.')

        user.totp_last_step = step
        session.add(user)
        session.commit()
        token = create_jwt_token(username)
        logger.info('2fa login success: %s', username)
        return {'token': token}

@router.post('/password-reset/request')
@limiter.limit('3/minute')
async def request_password_reset(request: Request, reset_in: PasswordResetRequest):
    """비밀번호 재설정 링크를 이메일로 발송한다. (rate limit: IP당 분당 3회)

    사용자 존재 여부·이메일 등록 여부와 무관하게 항상 동일한 200 응답을 반환한다
    (username/email enumeration 방지). 대상 사용자가 있고 **인증된 이메일**이 등록돼
    있을 때만 실제로 재설정 링크를 발송한다. 링크는 FRONTEND_URL 기준으로 만들어지며
    토큰은 30분(PASSWORD_RESET_EXPIRE_MINUTES) 후 만료된다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        reset_in: `{username}`.

    Returns:
        dict: 항상 동일한 안내 메시지.

    Raises:
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    with Session(engine) as session:
        user = session.get(WikiUser, reset_in.username)
        if user and user.email and user.email_verified:
            token = create_password_reset_token(user.username, user.password)
            reset_link = f'{FRONTEND_URL}/reset-password?token={token}'
            send_email(
                user.email,
                'SGCC Wiki 비밀번호 재설정',
                f'아래 링크에서 비밀번호를 재설정하세요 (30분 내 유효):\n\n{reset_link}',
            )
            logger.info('password reset requested: %s', user.username)
    return {'message': 'If the account exists, a password reset link has been sent.'}

@router.post('/password-reset/confirm')
@limiter.limit('5/minute')
async def confirm_password_reset(request: Request, confirm_in: PasswordResetConfirm):
    """재설정 토큰과 새 비밀번호로 비밀번호를 교체한다. (rate limit: IP당 분당 5회)

    토큰은 발급 시점의 비밀번호 해시로 서명되므로, 한 번 재설정에 성공하면(해시 변경)
    같은 토큰을 재사용할 수 없다(단일 사용). 새 비밀번호는 기존 정책 검증을 거친다.

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        confirm_in: `{token, new_password}`.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 400: 토큰이 유효하지 않거나 만료됐을 때, 또는 새 비밀번호가
                           정책을 위반했을 때.
    """
    validate_password(confirm_in.new_password)
    username = read_reset_token_subject(confirm_in.token)
    with Session(engine) as session:
        user = session.get(WikiUser, username) if username else None
        if not user:
            raise HTTPException(status_code=400, detail='Invalid or expired reset token.')

        verify_password_reset_token(confirm_in.token, user.password)

        user.password = hash_password(confirm_in.new_password)
        session.add(user)
        session.commit()
        logger.info('password reset completed: %s', user.username)
        return {'message': 'Password has been reset successfully.'}

@router.post('/2fa/setup')
async def setup_2fa(current_user: WikiUser = Depends(get_current_user)):
    """2FA용 TOTP 시크릿을 발급한다. (로그인 필요, 아직 활성화 아님)

    새 시크릿을 생성해 저장하고, 인증앱(Google Authenticator 등)에 등록할 수 있는
    provisioning URI(otpauth://...)를 반환한다. 실제 활성화는 `/2fa/enable`에서 코드를
    확인한 뒤에 이뤄진다. 이미 활성화된 계정에서 재호출하면 400.

    Returns:
        dict: `{'secret': '<base32>', 'otpauth_uri': 'otpauth://...'}`.
              프론트는 otpauth_uri로 QR을 그리거나 secret을 수동 입력하게 한다.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 이미 2FA가 활성화돼 있을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if user.totp_enabled:
            raise HTTPException(status_code=400, detail='Two-factor authentication is already enabled.')

        secret = generate_totp_secret()
        user.totp_secret = secret
        session.add(user)
        session.commit()
        logger.info('2fa setup initiated: %s', user.username)
        return {'secret': secret, 'otpauth_uri': totp_provisioning_uri(secret, user.username)}

@router.post('/2fa/enable')
async def enable_2fa(body: TotpCode, current_user: WikiUser = Depends(get_current_user)):
    """`/2fa/setup`으로 받은 시크릿을 코드로 확인하고 2FA를 활성화한다. (로그인 필요)

    Args:
        body: `{code}` — 인증앱이 표시하는 현재 6자리 코드.
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: setup을 먼저 하지 않았거나, 이미 활성화됐거나, 코드가 틀렸을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if user.totp_enabled:
            raise HTTPException(status_code=400, detail='Two-factor authentication is already enabled.')
        if not user.totp_secret:
            raise HTTPException(status_code=400, detail='Call /2fa/setup before enabling.')
        # enable은 소유 증명일 뿐이라 스텝을 소비하지 않는다. single-use는 실제 인증
        # 이벤트(login/2fa, disable)에서만 강제해, 같은 창에서 enable 직후 로그인이
        # 정상 동작하도록 한다.
        if matched_totp_step(user.totp_secret, body.code) is None:
            raise HTTPException(status_code=400, detail='Invalid authentication code.')

        user.totp_enabled = True
        session.add(user)
        session.commit()
        logger.info('2fa enabled: %s', user.username)
        return {'message': 'Two-factor authentication has been enabled.'}

@router.post('/2fa/disable')
async def disable_2fa(body: TotpCode, current_user: WikiUser = Depends(get_current_user)):
    """현재 코드를 확인하고 2FA를 비활성화한다. (로그인 필요)

    소유 증명을 위해 유효한 인증 코드를 요구한다. 성공 시 시크릿도 함께 제거한다.

    Args:
        body: `{code}` — 인증앱이 표시하는 현재 6자리 코드.
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 2FA가 켜져 있지 않거나 코드가 틀렸을 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if not user.totp_enabled or not user.totp_secret:
            raise HTTPException(status_code=400, detail='Two-factor authentication is not enabled.')
        step = matched_totp_step(user.totp_secret, body.code)
        if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
            raise HTTPException(status_code=400, detail='Invalid authentication code.')

        user.totp_enabled = False
        user.totp_secret = None
        user.totp_last_step = None
        session.add(user)
        session.commit()
        logger.info('2fa disabled: %s', user.username)
        return {'message': 'Two-factor authentication has been disabled.'}

@router.put('/email')
async def set_email(body: EmailUpdate, current_user: WikiUser = Depends(get_current_user)):
    """본인 계정에 이메일을 등록하거나 변경한다. (로그인 필요)

    새 이메일은 항상 미인증 상태(email_verified=False)로 저장되고, 곧바로 인증 링크를
    발송한다. 이메일은 계정 간 유일해야 한다.

    Args:
        body: `{email}`.
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 안내 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 이메일 형식이 올바르지 않을 때.
        HTTPException 409: 다른 사용자가 이미 사용 중인 이메일일 때.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    validate_email(body.email)
    with Session(engine) as session:
        owner = session.exec(select(WikiUser).where(WikiUser.email == body.email)).first()
        if owner and owner.username != current_user.username:
            raise HTTPException(status_code=409, detail='Email already in use.')

        user = session.get(WikiUser, current_user.username)
        user.email = body.email
        user.email_verified = False
        try:
            session.add(user)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail='Email already in use.')

        send_email_verification(user.username, body.email)
        logger.info('email set (unverified): %s', user.username)
        return {'message': 'Email updated. A verification link has been sent.'}

@router.post('/email/verify-request')
@limiter.limit('3/minute')
async def request_email_verification(request: Request, current_user: WikiUser = Depends(get_current_user)):
    """현재 등록된 이메일로 인증 링크를 재발송한다. (로그인 필요, 분당 3회)

    Args:
        request: slowapi rate limiter가 요구하는 요청 객체(직접 사용하지 않음).
        current_user: 인증 사용자. None이면 401.

    Returns:
        dict: 안내 메시지.

    Raises:
        HTTPException 401: 비로그인 상태.
        HTTPException 400: 등록된 이메일이 없거나 이미 인증됐을 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login required.')
    with Session(engine) as session:
        user = session.get(WikiUser, current_user.username)
        if not user.email:
            raise HTTPException(status_code=400, detail='No email registered.')
        if user.email_verified:
            raise HTTPException(status_code=400, detail='Email is already verified.')

        send_email_verification(user.username, user.email)
        logger.info('email verification resent: %s', user.username)
        return {'message': 'A verification link has been sent.'}

@router.post('/email/verify')
@limiter.limit('5/minute')
async def verify_email(request: Request, body: EmailVerify):
    """인증 토큰으로 이메일 인증을 완료한다. (비로그인 — 메일 링크에서 호출, 분당 5회)

    토큰에 담긴 이메일이 현재 계정 이메일과 일치할 때만 인증 처리한다(그 사이 이메일을
    바꿨다면 토큰은 무효).

    Args:
        body: `{token}` — 인증 메일 링크에 담긴 토큰.

    Returns:
        dict: 성공 메시지.

    Raises:
        HTTPException 400: 토큰이 유효하지 않거나 만료됐을 때, 또는 그 사이 이메일이
                           변경돼 토큰의 이메일과 현재 이메일이 다를 때.
        HTTPException 429: 분당 요청 한도 초과(rate limit).
    """
    username, email = verify_email_verification_token(body.token)
    with Session(engine) as session:
        user = session.get(WikiUser, username)
        if not user or user.email != email:
            raise HTTPException(status_code=400, detail='Invalid or expired verification token.')
        if user.email_verified:
            return {'message': 'Email is already verified.'}

        user.email_verified = True
        session.add(user)
        session.commit()
        logger.info('email verified: %s', user.username)
        return {'message': 'Email has been verified.'}
