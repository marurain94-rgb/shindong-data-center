# 배포 가이드 — Shindong Data Center

공개 URL(예: `https://shindong-data-center.onrender.com`)로 배포하는 방법입니다.

> ⚠️ **보안**: 이 저장소에는 `.gitignore`로 `storage/`·`*.db`·`ingest_skills.py`가 제외되어
> 로컬에 보관된 자격증명/파일은 절대 푸시되지 않습니다. 공개 인스턴스는 **빈 상태**로 시작하며,
> `DC_PASSWORD` 환경변수로 **반드시 로그인 비밀번호를 설정**해야 합니다.

---

## 사전 준비 (공통)

1. **GitHub 계정** 생성/로그인 후 새 저장소 만들기 (예: `shindong-data-center`).
2. 로컬에서 원격 연결 후 푸시:
   ```bash
   cd C:\Users\shindong\datacenter
   git branch -M main
   git remote add origin https://github.com/<내아이디>/shindong-data-center.git
   git push -u origin main
   ```

---

## 방법 A. Render (추천)

1. https://render.com 가입 (GitHub로 로그인 가능).
2. **New + → Blueprint** 선택 → 위 GitHub 저장소 연결.
   - 저장소의 `render.yaml`을 자동 인식 → 서비스 이름 `shindong-data-center`로 생성됨.
   - 최종 주소: **`https://shindong-data-center.onrender.com`**
3. 배포 설정 중 `DC_PASSWORD` 값을 입력 (로그인 비밀번호).
4. **Create** → 빌드/배포 완료까지 1~2분 대기.

### 영구 저장(중요)
- `render.yaml`에는 `/data` 영구 디스크가 포함되어 있어 업로드 파일이 보존됩니다.
- **단, 디스크는 Render 유료 플랜(Starter, 월 $7~)에서만 동작**합니다.
- **무료로 먼저 테스트**하려면 `render.yaml`에서 `plan: starter`를 `plan: free`로 바꾸고
  `disk:` 블록을 삭제하세요. (이 경우 재시작 시 업로드 파일이 사라집니다 — 테스트용.)

---

## 방법 B. Railway (무료 볼륨 제공)

1. https://railway.app 가입.
2. **New Project → Deploy from GitHub repo** → 저장소 선택.
   - `Procfile`을 인식해 `python server.py`로 실행. `PORT`는 자동 주입됩니다.
3. **Variables** 탭에서 설정:
   - `DC_PASSWORD` = 원하는 로그인 비밀번호
   - `HOST` = `0.0.0.0`
   - `DATA_DIR` = `/data`
4. **Volumes** 에서 볼륨을 만들고 마운트 경로를 `/data` 로 지정 (업로드 영구 보존).
5. **Settings → Networking → Generate Domain** 으로 공개 URL 발급.
   - 도메인 이름에 `shindong-data-center`를 넣어 설정할 수 있습니다.

---

## 배포 후 확인

- 공개 URL 접속 → 로그인 화면 → `DC_PASSWORD`로 로그인 → 대시보드.
- 우측 상단 **로그아웃** 버튼으로 세션 종료.

## 비밀번호 변경
- 플랫폼의 환경변수에서 `DC_PASSWORD` 값을 바꾸고 재배포하면 됩니다.
- 변경 시 기존 로그인 쿠키는 무효화됩니다.
