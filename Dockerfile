# Hugging Face Spaces (Docker SDK)용 이미지
# - 표준 라이브러리 앱 + 영구 백업용 huggingface_hub 만 설치
# - HF Spaces는 7860 포트로 접속하므로 0.0.0.0:7860 에 바인딩한다
FROM python:3.12-slim

# HF 권장: uid 1000 비루트 사용자
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR /home/user/app

# 영구 백업 라이브러리 설치
RUN pip install --no-cache-dir --user huggingface_hub

# 앱 코드 복사
COPY --chown=user server.py hf_sync.py index.html ./

# 런타임 환경
#  - HOST/PORT: HF Spaces 외부 노출 포트(7860)
#  - DATA_DIR : 휘발성 디스크. 시작 시 HF Dataset에서 복원되고, 변경 시 다시 백업됨
#  - DC_PASSWORD / HF_TOKEN / HF_REPO_ID 는 Space Secrets로 주입
ENV HOST=0.0.0.0 \
    PORT=7860 \
    DATA_DIR=/home/user/app/data

EXPOSE 7860

CMD ["python", "server.py"]
