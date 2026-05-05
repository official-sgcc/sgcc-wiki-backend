# sgcc-wiki-backend
## 실행

```bash
pip install fastapi sqlmodel bcrypt pyjwt python-dotenv uvicorn
```

```bash
uvicorn main:app --reload
```

서버: http://127.0.0.1:8000

Swagger UI: http://127.0.0.1:8000/docs

## API

### 문서
- `GET /documents` - 문서 목록 조회  
  - parameter:
    - keyword - 제목 및 본문 검색어  
  - response:
    - 문서 목록 (제목, 본문, 태그 등)

- `POST /documents` - 문서 생성
  - request body:
    - title - 문서 제목
    - content - 본문
    - tags - 태그 리스트
  - response:
    - 생성된 문서

- `GET /documents/{title}` - 문서 조회
  - parameter:
    - title - 문서 제목
  - response:
    - 해당 문서

- `PUT /documents/{title}` - 문서 수정
  - parameter:
    - title - 문서 제목
  - headers:
    - jwt 토큰
  - request body:
    - 수정할 문서 내용 (content, tags 등)
  - response:
    - 수정된 문서

### 사용자
- `POST /register` - 회원가입
  - request body:
    - username - 아이디
    - password - 비밀번호
  - response:
    - 가입 완료

- `POST /login` - 로그인
  - request body:
    - username - 아이디
    - password - 비밀번호
  - response:
    - jwt 토큰

- `GET /users/{username}` - 사용자 정보 조회
  - parameter:
    - username - 아이디
  - response:
    - 사용자 정보