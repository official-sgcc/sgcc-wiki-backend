"""메일 발송, DB 백업, 관리자 계정 부트스트랩 같은 앱 부수 작업."""

import os
import smtplib
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape as html_escape
from uuid import uuid4
import httpx
from sqlmodel import Session
from core.config import (
    BACKUP_DIR, DB_PATH, FRONTEND_URL, logger,
    EMAIL_PROVIDER, EMAIL_FROM, EMAIL_DAILY_LIMIT, EMAIL_COOLDOWN_SECONDS, RESEND_API_KEY,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
)
from core.database import engine
from core.login_utils import (
    EMAIL_VERIFY_EXPIRE_MINUTES, PASSWORD_RESET_EXPIRE_MINUTES,
    create_email_verification_token, hash_password,
)
from schemas.wiki_user import WikiUser, EmailVerification

RESEND_API_URL = 'https://api.resend.com/emails'
EMAIL_SEND_TIMEOUT_SECONDS = 10
# 일시 장애(네트워크 오류·5xx·429) 재시도 전 대기 시간(초). 튜플 길이가 곧 재시도 횟수.
EMAIL_RETRY_DELAYS = (5, 30)

# 발송 한도 추적용 인메모리 상태. 재시작하면 초기화되지만 동아리 규모에는 충분하다.
_send_lock = threading.Lock()
_recent_sends: deque[datetime] = deque()
_last_send_by_recipient: dict[str, datetime] = {}


class PermanentEmailError(Exception):
    """재시도해도 성공할 수 없는 발송 실패(잘못된 주소, 미인증 도메인, 인증 정보 오류)."""


def _deliver_log(send_id: str, to: str, subject: str, body: str, html: str | None = None) -> str | None:
    """실제 발송 대신 내용을 로그에 남긴다(개발·미설정 환경)."""
    logger.info('email provider=log; would send to %s [%s]:\n%s', to, subject, body)
    return None


def _deliver_smtp(send_id: str, to: str, subject: str, body: str, html: str | None = None) -> str:
    """SMTP(STARTTLS, 587)로 발송하고 Message-ID를 반환한다.

    Raises:
        PermanentEmailError: 인증 실패·수신자 거부처럼 재시도가 무의미한 오류.
        smtplib.SMTPException | OSError: 연결·타임아웃 등 일시 오류(호출측에서 재시도).
    """
    msg = EmailMessage()
    msg['From'] = EMAIL_FROM
    msg['To'] = to
    msg['Subject'] = subject
    msg['Message-ID'] = (message_id := make_msgid(idstring=send_id))
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype='html')
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=EMAIL_SEND_TIMEOUT_SECONDS) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused) as exc:
        raise PermanentEmailError(str(exc)) from exc
    return message_id


def _deliver_resend(send_id: str, to: str, subject: str, body: str, html: str | None = None) -> str | None:
    """Resend HTTP API로 발송하고 Resend가 준 메시지 ID를 반환한다.

    Idempotency-Key에 send_id를 실어 타임아웃 뒤 재시도해도 같은 메일이 두 번 나가지 않게 한다.

    Raises:
        PermanentEmailError: 4xx 응답(잘못된 키, 미인증 도메인, 잘못된 주소 등).
        RuntimeError: 네트워크 오류·5xx·429(호출측에서 재시도).
    """
    payload = {'from': EMAIL_FROM, 'to': [to], 'subject': subject, 'text': body}
    if html:
        payload['html'] = html
    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Idempotency-Key': f'sgcc-wiki/{send_id}'},
            json=payload,
            timeout=EMAIL_SEND_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f'resend request failed: {exc}') from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RuntimeError(f'resend returned {resp.status_code}: {resp.text[:200]}')
    if resp.status_code >= 400:
        raise PermanentEmailError(f'resend rejected: {resp.status_code} {resp.text[:200]}')
    return resp.json().get('id')


_PROVIDERS = {'log': _deliver_log, 'smtp': _deliver_smtp, 'resend': _deliver_resend}


def _deliver_with_retry(send_id: str, to: str, subject: str, body: str, html: str | None = None):
    """백그라운드 스레드에서 실행되는 실제 발송. 일시 장애는 EMAIL_RETRY_DELAYS만큼 재시도한다."""
    deliver = _PROVIDERS[EMAIL_PROVIDER]
    for attempt in range(len(EMAIL_RETRY_DELAYS) + 1):
        try:
            message_id = deliver(send_id, to, subject, body, html)
        except PermanentEmailError as exc:
            logger.error('email failed permanently: send_id=%s to=%s [%s]: %s', send_id, to, subject, exc)
            return
        except Exception as exc:
            if attempt == len(EMAIL_RETRY_DELAYS):
                logger.error('email failed after %d attempts: send_id=%s to=%s [%s]: %s', attempt + 1, send_id, to, subject, exc)
                return
            delay = EMAIL_RETRY_DELAYS[attempt]
            logger.warning('email send failed (attempt %d), retrying in %ds: send_id=%s to=%s: %s', attempt + 1, delay, send_id, to, exc)
            time.sleep(delay)
        else:
            logger.info('email sent: send_id=%s to=%s [%s] provider=%s message_id=%s', send_id, to, subject, EMAIL_PROVIDER, message_id)
            return


def _run_in_background(func, *args):
    """발송을 요청 처리와 분리해 별도 스레드에서 돌린다. 테스트에서는 동기 실행으로 교체한다."""
    threading.Thread(target=func, args=args, daemon=True).start()


def reserve_email_slot(to: str) -> bool:
    """수신자 쿨다운과 24시간 발송 상한을 검사하고, 통과하면 슬롯을 소비한다.

    비로그인 엔드포인트가 임의 주소로 메일을 보낼 수 있으므로, 도메인 평판과
    발송 서비스 무료 한도를 지키기 위한 최소 안전장치다.
    """
    now = datetime.utcnow()
    with _send_lock:
        while _recent_sends and _recent_sends[0] < now - timedelta(days=1):
            _recent_sends.popleft()
        last = _last_send_by_recipient.get(to)
        if last and last > now - timedelta(seconds=EMAIL_COOLDOWN_SECONDS):
            logger.warning('email suppressed (cooldown %ds): to=%s', EMAIL_COOLDOWN_SECONDS, to)
            return False
        if len(_recent_sends) >= EMAIL_DAILY_LIMIT:
            logger.warning('email suppressed (daily limit %d reached): to=%s', EMAIL_DAILY_LIMIT, to)
            return False
        if len(_last_send_by_recipient) > 1000:
            cutoff = now - timedelta(seconds=EMAIL_COOLDOWN_SECONDS)
            for key in [k for k, v in _last_send_by_recipient.items() if v < cutoff]:
                del _last_send_by_recipient[key]
        _recent_sends.append(now)
        _last_send_by_recipient[to] = now
        return True


def _dispatch_email(to: str, subject: str, body: str, html: str | None = None):
    """발송 ID를 붙여 백그라운드 발송을 시작한다(한도 검사 없음)."""
    send_id = uuid4().hex
    logger.info('email queued: send_id=%s to=%s [%s] provider=%s', send_id, to, subject, EMAIL_PROVIDER)
    _run_in_background(_deliver_with_retry, send_id, to, subject, body, html)


def send_email(to: str, subject: str, body: str, html: str | None = None) -> bool:
    """이메일 발송을 예약한다. 한도(수신자 쿨다운·일일 상한)에 걸리면 False.

    실제 전송은 EMAIL_PROVIDER(log/smtp/resend)로 백그라운드 스레드에서 수행하므로
    응답을 막지 않고, 전송 실패도 사용자 응답에 영향을 주지 않는다(로그에만 남는다).

    Args:
        to: 수신자 주소.
        subject: 제목.
        body: 본문(plain text). HTML을 못 보는 클라이언트용 대체 텍스트이기도 하다.
        html: 있으면 HTML 본문으로 함께 보낸다(render_email_html 참고).

    Returns:
        bool: 발송이 예약됐으면 True, 한도에 걸려 건너뛰었으면 False.
    """
    if not reserve_email_slot(to):
        return False
    _dispatch_email(to, subject, body, html)
    return True


def _format_minutes(minutes: int) -> str:
    """만료 시간을 사람이 읽기 좋게 바꾼다(1440 → '24시간', 30 → '30분')."""
    return f'{minutes // 60}시간' if minutes % 60 == 0 else f'{minutes}분'


def render_email_html(title: str, message: str, button_label: str, link: str, note: str) -> str:
    """버튼 하나짜리 공용 HTML 메일을 렌더링한다.

    메일 클라이언트 호환을 위해 table 레이아웃과 인라인 스타일만 쓴다. 링크는 버튼과
    본문 텍스트 두 곳에 넣어 버튼이 안 보이는 클라이언트에서도 열 수 있게 한다.

    Args:
        title: 카드 상단 제목.
        message: 제목 아래 안내 문장.
        button_label: 버튼 문구.
        link: 버튼·텍스트 링크 URL.
        note: 하단 회색 안내(만료 시간 등).
    """
    safe_link = html_escape(link, quote=True)
    return f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;border-top:4px solid #c8102e;">
        <tr><td style="padding:32px 0 0;font-size:26px;font-weight:700;line-height:1.3;color:#1a1a1a;">{html_escape(title)}</td></tr>
        <tr><td style="padding:16px 0 0;font-size:16px;line-height:1.7;color:#444444;">{html_escape(message)}</td></tr>
        <tr><td style="padding:32px 0 0;">
          <table role="presentation" cellpadding="0" cellspacing="0"><tr>
            <td style="background:#c8102e;border-radius:4px;">
              <a href="{safe_link}" style="display:inline-block;padding:14px 28px;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;">{html_escape(button_label)}</a>
            </td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:24px 0 0;font-size:13px;line-height:1.6;color:#888888;">버튼이 열리지 않으면 이 주소로 들어가세요.<br>
          <a href="{safe_link}" style="color:#c8102e;word-break:break-all;">{safe_link}</a></td></tr>
        <tr><td style="padding:40px 0 0;border-bottom:1px solid #e6e6e6;"></td></tr>
        <tr><td style="padding:20px 0 0;font-size:13px;line-height:1.7;color:#888888;">{html_escape(note)}</td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>'''


def send_email_verification(username: str, email: str) -> bool:
    """해당 이메일로 인증 링크를 발송하고 EmailVerification 레코드를 남긴다.

    Args:
        username: 인증 대상 사용자명(가입 전이어도 됨).
        email: 인증 링크를 받을 주소.

    Returns:
        bool: 발송이 예약됐으면 True. 한도에 걸리면 레코드를 만들지 않고 False.
    """
    if not reserve_email_slot(email):
        return False
    token = create_email_verification_token(username, email)
    expires = datetime.utcnow() + timedelta(minutes=EMAIL_VERIFY_EXPIRE_MINUTES)
    ev = EmailVerification(
        username=username,
        email=email,
        token=token,
        verified=False,
        created_at=datetime.utcnow(),
        expires_at=expires,
    )
    with Session(engine) as session:
        session.add(ev)
        session.commit()

    verify_link = f'{FRONTEND_URL}/verify-email?token={token}'
    valid_for = _format_minutes(EMAIL_VERIFY_EXPIRE_MINUTES)
    _dispatch_email(
        email,
        'SGCC Wiki 이메일 인증',
        f'아래 링크로 이메일을 인증하세요 ({valid_for} 내 유효):\n\n{verify_link}',
        render_email_html(
            '이메일 인증',
            'SGCC Wiki 가입을 마치려면 이메일 인증이 필요합니다.',
            '이메일 인증하기',
            verify_link,
            f'인증 링크는 {valid_for} 동안 유효합니다.',
        ),
    )
    return True


def send_password_reset_email(to: str, reset_link: str) -> bool:
    """비밀번호 재설정 링크 메일을 예약한다. 한도에 걸리면 False.

    Args:
        to: 계정에 등록된(인증된) 이메일.
        reset_link: 프론트의 재설정 페이지 URL(토큰 포함).

    Returns:
        bool: 발송이 예약됐으면 True.
    """
    valid_for = _format_minutes(PASSWORD_RESET_EXPIRE_MINUTES)
    return send_email(
        to,
        'SGCC Wiki 비밀번호 재설정',
        f'아래 링크에서 비밀번호를 재설정하세요 ({valid_for} 내 유효):\n\n{reset_link}',
        render_email_html(
            '비밀번호 재설정',
            '비밀번호 재설정 요청이 접수됐습니다. 아래 버튼에서 새 비밀번호를 정하세요.',
            '비밀번호 재설정하기',
            reset_link,
            f'재설정 링크는 {valid_for} 동안 유효하고 한 번만 쓸 수 있습니다.',
        ),
    )


def send_email_now(to: str, subject: str, body: str, html: str | None = None) -> dict:
    """재시도·백그라운드 없이 provider를 한 번 호출하고 결과를 반환한다(설정 점검용).

    관리자의 테스트 발송에 쓴다. 한도 검사는 호출측(reserve_email_slot)이 담당한다.

    Returns:
        dict: `{'provider': <log|smtp|resend>, 'message_id': <str | None>}`

    Raises:
        PermanentEmailError | Exception: provider 오류를 그대로 전파해 응답에 드러낸다.
    """
    send_id = uuid4().hex
    message_id = _PROVIDERS[EMAIL_PROVIDER](send_id, to, subject, body, html)
    logger.info('email sent (sync test): send_id=%s to=%s provider=%s message_id=%s', send_id, to, EMAIL_PROVIDER, message_id)
    return {'provider': EMAIL_PROVIDER, 'message_id': message_id}


def send_test_email_now(to: str) -> dict:
    """관리자 설정 점검용 테스트 메일을 동기 발송한다(`POST /email/test`)."""
    message = f'이 메일이 보이면 SGCC Wiki 메일 설정(provider={EMAIL_PROVIDER})이 정상입니다.'
    return send_email_now(
        to,
        'SGCC Wiki 메일 테스트',
        message,
        render_email_html('메일 테스트', message, '위키 열기', FRONTEND_URL, '관리자가 메일 설정을 확인하려고 보낸 메일입니다.'),
    )


def backup_database():
    """현재 SQLite DB를 db_backups/에 타임스탬프 파일로 스냅샷한다.

    단순 파일 복사(shutil.copy2)가 아니라 sqlite3.Connection.backup(SQLite Backup API)을
    쓴다. 쓰기 트랜잭션이 진행 중이어도 일관된 스냅샷을 뜨기 위함이며,
    단순 파일 복사로 회귀시키면 백업이 깨질 수 있다.

    lifespan에 등록된 자정(00:00) cron 스케줄러가 호출한다. 백업 파일명은
    `db_backup_YYYYMMDD_HHhMMmSSs.db` 형식.

    Raises:
        Exception: 백업 실패 시 로그를 남기고 예외를 그대로 재전파한다.
                   (원본/대상 커넥션은 finally에서 항상 닫힌다.)
    """
    today_str = datetime.now().strftime('%Y%m%d_%Hh%Mm%Ss')
    backup_path = f'{BACKUP_DIR}/db_backup_{today_str}.db'

    source = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(backup_path)
    try:
        with dest:
            source.backup(dest)
        logger.info('database backup created: %s', backup_path)
    except Exception:
        logger.exception('database backup failed: %s', backup_path)
        raise
    finally:
        source.close()
        dest.close()

def bootstrap_admin():
    """환경변수로 지정된 관리자 계정을 앱 시작 시 보장한다.

    ADMIN_USERNAME / ADMIN_PASSWORD 환경변수를 읽어:
      - 둘 중 하나라도 비어 있으면 아무 동작도 하지 않고 반환한다.
      - 해당 사용자가 없으면 permission='admin'으로 새로 생성한다.
      - 이미 있으면 permission을 'admin'으로 승격한다(이미 admin이면 그대로 둠).

    register API는 RESERVED_USERNAMES 때문에 'admin' 가입을 막으므로, 관리자 계정은
    이 부트스트랩 경로로만 만들어진다.
    """
    admin_username = os.getenv('ADMIN_USERNAME')
    admin_password = os.getenv('ADMIN_PASSWORD')
    if not admin_username or not admin_password:
        return

    with Session(engine) as session:
        user = session.get(WikiUser, admin_username)
        if user:
            if user.permission != 'admin':
                user.permission = 'admin'
                session.add(user)
                session.commit()
                logger.info('admin bootstrap: promoted existing user to admin: %s', admin_username)
        else:
            user = WikiUser(
                username=admin_username,
                password=hash_password(admin_password),
                permission='admin',
                bio='',
                email=None,
            )
            session.add(user)
            session.commit()
            logger.info('admin bootstrap: created admin user: %s', admin_username)
