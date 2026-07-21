"""FastAPI 앱 조립: 미들웨어, 수명주기, 라우터 등록.

엔드포인트는 routers/ 하위 도메인별 모듈에 있다. 설정은 config.py,
엔진은 database.py, 공통 의존성은 deps.py, 부수 작업은 maintenance.py.
"""

from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from core.config import FRONTEND_URL, limiter
from core.maintenance import backup_database, bootstrap_admin
from routers import categories, documents, tags, users

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 수명주기 훅.

    시작 시: 관리자 계정 부트스트랩 후 백그라운드 스케줄러를 띄워 매일 자정
    backup_database를 예약한다. 종료 시: 스케줄러를 정리한다.

    Args:
        app: FastAPI 인스턴스(프레임워크가 주입, 여기서는 사용하지 않음).
    """
    bootstrap_admin()
    scheduler = BackgroundScheduler()
    scheduler.add_job(backup_database, 'cron', hour=0, minute=0)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(
    title='SGCC Wiki API',
    description='소규모 위키 백엔드 — 문서 CRUD/버전·diff, JWT 인증, 태그·카테고리, 문서별 권한, 자동 백업',
    version='1.0.0',
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, 'http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'auth', 'Authorization'],
)

app.include_router(documents.router)
app.include_router(users.router)
app.include_router(tags.router)
app.include_router(categories.router)
