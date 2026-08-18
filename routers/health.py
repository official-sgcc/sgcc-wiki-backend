"""서비스 상태 확인 엔드포인트."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlmodel import Session
from core.config import logger
from core.database import engine

router = APIRouter()

@router.get('/healthz')
async def healthz():
    """백엔드와 DB가 정상 동작 중인지 확인한다. (인증 불필요)

    Returns:
        dict: 정상이면 {'status': 'ok'}.

    Raises:
        HTTPException: DB 연결·질의에 실패하면 503.
    """
    try:
        with Session(engine) as session:
            session.exec(text('SELECT 1'))
    except Exception as e:
        logger.error(f'Health check failed: {e}')
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Database unavailable.')
    return {'status': 'ok'}
