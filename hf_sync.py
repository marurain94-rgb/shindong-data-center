# -*- coding: utf-8 -*-
"""
HF Dataset 영구 백업 모듈 (선택적)

- HF Spaces 무료 티어는 디스크가 휘발성이라 재시작 시 업로드 데이터가 사라진다.
- 이 모듈은 비공개 HF Dataset 저장소를 영구 백업소로 사용한다.
- 환경변수 HF_TOKEN + HF_REPO_ID 가 둘 다 있으면 활성화되고,
  없으면 모든 함수가 no-op 이라 로컬 실행에는 아무 영향이 없다.

server.py 가 호출하는 인터페이스:
  enabled()                       -> 백업 활성 여부
  ensure_repo()                   -> Dataset 저장소 보장(없으면 생성)
  restore(data_dir)               -> 시작 시 원격 데이터를 로컬로 복원
  commit(adds=[(local, remote)], deletes=[remote], msg) -> 변경 묶음 커밋
"""
import os
import threading

HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_REPO_ID = os.environ.get("HF_REPO_ID", "")  # 예: "marurain94-rgb/shindong-dc-data"

_api = None
_ENABLED = False
_lock = threading.Lock()  # 커밋 직렬화(HF는 동시 커밋 충돌 가능)

try:
    if HF_TOKEN and HF_REPO_ID:
        from huggingface_hub import HfApi  # noqa: WPS433 (선택적 의존성)

        _api = HfApi(token=HF_TOKEN)
        _ENABLED = True
except Exception as exc:  # huggingface_hub 미설치 등 → 백업 비활성
    print(f"[hf_sync] 비활성 (이유: {exc})")
    _ENABLED = False


def enabled():
    return _ENABLED


def ensure_repo():
    """Dataset 저장소가 없으면 비공개로 생성한다."""
    if not _ENABLED:
        return
    try:
        _api.create_repo(
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            private=True,
            exist_ok=True,
        )
        print(f"[hf_sync] 백업 저장소 준비됨: {HF_REPO_ID}")
    except Exception as exc:
        print(f"[hf_sync] ensure_repo 실패: {exc}")


def restore(data_dir):
    """원격 Dataset의 datacenter.db + storage/ 를 로컬 data_dir로 복원한다."""
    if not _ENABLED:
        return
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            local_dir=data_dir,
            token=HF_TOKEN,
        )
        print(f"[hf_sync] 백업 복원 완료 → {data_dir}")
    except Exception as exc:
        # 저장소가 비었거나 처음 실행이면 복원할 게 없을 수 있다 → 무시
        print(f"[hf_sync] 복원 건너뜀: {exc}")


def commit(adds=None, deletes=None, msg="update"):
    """파일 추가/삭제를 단일 커밋으로 원격에 반영한다(동기).

    adds:    [(local_path, path_in_repo), ...]
    deletes: [path_in_repo, ...]
    """
    if not _ENABLED:
        return
    try:
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete

        ops = []
        for local_path, path_in_repo in (adds or []):
            ops.append(
                CommitOperationAdd(path_in_repo=path_in_repo, path_or_fileobj=local_path)
            )
        for path_in_repo in (deletes or []):
            ops.append(CommitOperationDelete(path_in_repo=path_in_repo))
        if not ops:
            return
        with _lock:
            _api.create_commit(
                repo_id=HF_REPO_ID,
                repo_type="dataset",
                operations=ops,
                commit_message=msg,
            )
    except Exception as exc:
        # 백업 실패가 앱 동작을 막지 않도록 삼킨다(로그만 남김).
        print(f"[hf_sync] 커밋 실패: {exc}")
