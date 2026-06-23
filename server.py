# -*- coding: utf-8 -*-
"""
데이터 센터 클라우드 (로컬)
- RAW 데이터(excel, pdf, docx 등)와 Claude로 생성된 skill을 아카이빙
- 표준 라이브러리만 사용 (외부 패키지 설치 불필요)
- SQLite로 메타데이터 관리, 파일은 storage/ 에 보관
실행: python server.py  ->  http://localhost:8765
"""
import os
import io
import json
import sqlite3
import hashlib
import mimetypes
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import hf_sync  # 선택적 HF Dataset 백업(미설정 시 모든 호출이 no-op)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 영구 디스크가 있는 PaaS에서는 DATA_DIR 환경변수로 저장 위치를 지정 (예: /data)
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
STORAGE_DIR = os.path.join(DATA_DIR, "storage")
DB_PATH = os.path.join(DATA_DIR, "datacenter.db")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
# PaaS는 PORT를 환경변수로 주입하고 0.0.0.0 바인딩이 필요하다
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
MAX_UPLOAD = 1024 * 1024 * 1024  # 1GB

# DC_PASSWORD가 설정되면 로그인 인증을 요구한다 (공개 배포 시 필수).
# 미설정(로컬)이면 인증 없이 바로 사용.
PASSWORD = os.environ.get("DC_PASSWORD", "")
AUTH_SALT = "shindong-data-center-v1"

os.makedirs(STORAGE_DIR, exist_ok=True)


def auth_token():
    return hashlib.sha256((AUTH_SALT + PASSWORD).encode("utf-8")).hexdigest()


LOGIN_HTML = """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>로그인 · Shindong Data Center</title>
<style>
 body{{margin:0;font-family:"Segoe UI","Malgun Gothic",system-ui,sans-serif;
  background:#0f1419;color:#e6edf3;display:grid;place-items:center;height:100vh}}
 .box{{background:#171e26;border:1px solid #2a343f;border-radius:14px;padding:34px;width:320px}}
 .logo{{font-size:34px;text-align:center}}
 h1{{font-size:18px;text-align:center;margin:10px 0 4px}}
 .sub{{color:#8b98a5;font-size:12px;text-align:center;margin-bottom:22px}}
 input{{width:100%;box-sizing:border-box;background:#1e2730;border:1px solid #2a343f;color:#e6edf3;
  padding:11px 12px;border-radius:8px;font-size:14px;outline:none}}
 input:focus{{border-color:#4493f8}}
 button{{width:100%;margin-top:12px;background:#4493f8;color:#fff;border:none;padding:11px;
  border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}}
 .err{{color:#f85149;font-size:13px;text-align:center;margin-top:12px;min-height:18px}}
</style></head><body>
<form class="box" method="POST" action="/api/login">
 <div class="logo">🗄️</div>
 <h1>Shindong Data Center</h1>
 <div class="sub">접근하려면 비밀번호를 입력하세요</div>
 <input type="password" name="password" placeholder="비밀번호" autofocus>
 <button type="submit">로그인</button>
 <div class="err">{error}</div>
</form></body></html>"""


# ----------------------------- DB -----------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            category    TEXT NOT NULL DEFAULT 'raw',   -- 'raw' | 'skill'
            tags        TEXT NOT NULL DEFAULT '',       -- 쉼표 구분
            description TEXT NOT NULL DEFAULT '',
            filename    TEXT NOT NULL,                  -- 원본 파일명
            stored_name TEXT NOT NULL,                  -- 저장된 실제 파일명
            ext         TEXT NOT NULL DEFAULT '',
            size        INTEGER NOT NULL DEFAULT 0,
            sha256      TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# --------------------------- helpers --------------------------
def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def row_to_dict(row):
    d = dict(row)
    return d


def human_size(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024.0:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


# --------------------------- handler --------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "DataCenter/1.0"

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} - {fmt % args}")

    # ---- response helpers ----
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data, content_type, status=200, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _get_cookie(self, name):
        raw = self.headers.get("Cookie", "") or ""
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == name:
                    return v
        return None

    def is_authed(self):
        if not PASSWORD:
            return True
        return self._get_cookie("dc_auth") == auth_token()

    def login_page(self, error=""):
        body = LOGIN_HTML.format(error=error).encode("utf-8")
        self._send_bytes(body, "text/html; charset=utf-8")

    def api_login(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        data = urllib.parse.parse_qs(raw)
        pwd = (data.get("password", [""])[0] or "")
        if PASSWORD and pwd == PASSWORD:
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                f"dc_auth={auth_token()}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.login_page(error="비밀번호가 올바르지 않습니다.")

    def logout(self):
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", "dc_auth=; Path=/; Max-Age=0")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_file(self, path, content_type, download_name=None, inline=False):
        try:
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            if download_name:
                disp = "inline" if inline else "attachment"
                enc = urllib.parse.quote(download_name)
                self.send_header(
                    "Content-Disposition",
                    f"{disp}; filename*=UTF-8''{enc}",
                )
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except FileNotFoundError:
            self._send_json({"error": "파일을 찾을 수 없습니다."}, 404)

    # ------------------------- GET -------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/login":
            return self.login_page()
        if path == "/logout":
            return self.logout()
        if not self.is_authed():
            if path.startswith("/api/"):
                return self._send_json({"error": "unauthorized"}, 401)
            return self._redirect("/login")

        if path == "/" or path == "/index.html":
            return self._send_file(INDEX_PATH, "text/html; charset=utf-8")

        if path == "/api/items":
            return self.api_list(qs)

        if path == "/api/stats":
            return self.api_stats()

        if path.startswith("/api/download/"):
            return self.api_file(path.rsplit("/", 1)[-1], inline=False)

        if path.startswith("/api/preview/"):
            return self.api_file(path.rsplit("/", 1)[-1], inline=True)

        return self._send_json({"error": "not found"}, 404)

    # ------------------------- POST ------------------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/login":
            return self.api_login()
        if not self.is_authed():
            return self._send_json({"error": "unauthorized"}, 401)

        if path == "/api/upload":
            return self.api_upload(qs)

        if path.startswith("/api/update/"):
            return self.api_update(path.rsplit("/", 1)[-1], qs)

        return self._send_json({"error": "not found"}, 404)

    # ------------------------ DELETE -----------------------
    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self.is_authed():
            return self._send_json({"error": "unauthorized"}, 401)
        if path.startswith("/api/items/"):
            return self.api_delete(path.rsplit("/", 1)[-1])
        return self._send_json({"error": "not found"}, 404)

    # ----------------------- API impl ----------------------
    def api_list(self, qs):
        search = (qs.get("search", [""])[0] or "").strip()
        category = (qs.get("category", [""])[0] or "").strip()
        tag = (qs.get("tag", [""])[0] or "").strip()
        sort = (qs.get("sort", ["new"])[0] or "new").strip()

        where = []
        params = []
        if category in ("raw", "skill"):
            where.append("category = ?")
            params.append(category)
        if search:
            where.append("(title LIKE ? OR description LIKE ? OR tags LIKE ? OR filename LIKE ?)")
            like = f"%{search}%"
            params += [like, like, like, like]
        if tag:
            where.append("tags LIKE ?")
            params.append(f"%{tag}%")

        order = {
            "new": "created_at DESC",
            "old": "created_at ASC",
            "name": "title COLLATE NOCASE ASC",
            "size": "size DESC",
        }.get(sort, "created_at DESC")

        sql = "SELECT * FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + order

        conn = get_db()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        items = []
        for r in rows:
            d = row_to_dict(r)
            d["size_human"] = human_size(d["size"])
            d["tag_list"] = [t.strip() for t in d["tags"].split(",") if t.strip()]
            items.append(d)
        return self._send_json({"items": items, "count": len(items)})

    def api_stats(self):
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(size),0) s FROM items").fetchone()
        raw = conn.execute("SELECT COUNT(*) c FROM items WHERE category='raw'").fetchone()
        skill = conn.execute("SELECT COUNT(*) c FROM items WHERE category='skill'").fetchone()
        tagrows = conn.execute("SELECT tags FROM items").fetchall()
        conn.close()
        tagset = {}
        for tr in tagrows:
            for t in tr["tags"].split(","):
                t = t.strip()
                if t:
                    tagset[t] = tagset.get(t, 0) + 1
        top_tags = sorted(tagset.items(), key=lambda x: -x[1])
        return self._send_json(
            {
                "total": total["c"],
                "total_size": total["s"],
                "total_size_human": human_size(total["s"]),
                "raw": raw["c"],
                "skill": skill["c"],
                "tags": [{"name": k, "count": v} for k, v in top_tags],
            }
        )

    def api_upload(self, qs):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._send_json({"error": "빈 파일입니다."}, 400)
        if length > MAX_UPLOAD:
            return self._send_json({"error": "파일이 너무 큽니다 (최대 1GB)."}, 413)

        def g(key, default=""):
            return (qs.get(key, [default])[0] or default)

        filename = urllib.parse.unquote(self.headers.get("X-Filename", "") or g("filename", "upload.bin"))
        filename = os.path.basename(filename) or "upload.bin"
        title = urllib.parse.unquote(g("title")) or filename
        category = g("category", "raw")
        if category not in ("raw", "skill"):
            category = "raw"
        tags = urllib.parse.unquote(g("tags"))
        description = urllib.parse.unquote(g("description"))
        ext = os.path.splitext(filename)[1].lower().lstrip(".")

        # 본문(파일 바이트) 읽기 + sha256
        h = hashlib.sha256()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stored_name = f"{ts}_{filename}"
        stored_path = os.path.join(STORAGE_DIR, stored_name)
        remaining = length
        written = 0
        with open(stored_path, "wb") as out:
            while remaining > 0:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                written += len(chunk)
                remaining -= len(chunk)

        conn = get_db()
        cur = conn.execute(
            """INSERT INTO items
               (title, category, tags, description, filename, stored_name, ext, size, sha256, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (title, category, tags, description, filename, stored_name, ext, written, h.hexdigest(), now_iso()),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        # 영구 백업: 새 파일 + 갱신된 DB를 한 커밋으로 반영
        hf_sync.commit(
            adds=[(stored_path, f"storage/{stored_name}"), (DB_PATH, "datacenter.db")],
            msg=f"upload: {filename}",
        )
        return self._send_json({"ok": True, "id": new_id})

    def api_update(self, item_id, qs):
        try:
            item_id = int(item_id)
        except ValueError:
            return self._send_json({"error": "잘못된 ID"}, 400)

        def g(key):
            v = qs.get(key)
            return urllib.parse.unquote(v[0]) if v else None

        fields = {}
        for k in ("title", "category", "tags", "description"):
            v = g(k)
            if v is not None:
                fields[k] = v
        if "category" in fields and fields["category"] not in ("raw", "skill"):
            fields["category"] = "raw"
        if not fields:
            return self._send_json({"error": "수정할 항목 없음"}, 400)

        sets = ", ".join(f"{k}=?" for k in fields)
        params = list(fields.values()) + [item_id]
        conn = get_db()
        conn.execute(f"UPDATE items SET {sets} WHERE id=?", params)
        conn.commit()
        conn.close()
        # 메타데이터만 바뀌므로 DB만 백업
        hf_sync.commit(adds=[(DB_PATH, "datacenter.db")], msg=f"update: #{item_id}")
        return self._send_json({"ok": True})

    def api_file(self, item_id, inline):
        try:
            item_id = int(item_id)
        except ValueError:
            return self._send_json({"error": "잘못된 ID"}, 400)
        conn = get_db()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        conn.close()
        if not row:
            return self._send_json({"error": "항목 없음"}, 404)
        path = os.path.join(STORAGE_DIR, row["stored_name"])
        ctype = mimetypes.guess_type(row["filename"])[0] or "application/octet-stream"
        return self._send_file(path, ctype, download_name=row["filename"], inline=inline)

    def api_delete(self, item_id):
        try:
            item_id = int(item_id)
        except ValueError:
            return self._send_json({"error": "잘못된 ID"}, 400)
        conn = get_db()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            conn.close()
            return self._send_json({"error": "항목 없음"}, 404)
        path = os.path.join(STORAGE_DIR, row["stored_name"])
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
        # 영구 백업: 원격 파일 삭제 + 갱신된 DB를 한 커밋으로 반영
        hf_sync.commit(
            adds=[(DB_PATH, "datacenter.db")],
            deletes=[f"storage/{row['stored_name']}"],
            msg=f"delete: #{item_id}",
        )
        return self._send_json({"ok": True})


def main():
    # 백업이 켜져 있으면(클라우드) 기존 DB/파일을 먼저 복원한 뒤 DB 초기화
    if hf_sync.enabled():
        hf_sync.ensure_repo()
        hf_sync.restore(DATA_DIR)
        os.makedirs(STORAGE_DIR, exist_ok=True)
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print("=" * 50)
    print("  데이터 센터 클라우드")
    print(f"  주소: {url}")
    print(f"  저장 위치: {STORAGE_DIR}")
    print(f"  영구 백업: {'ON (' + hf_sync.HF_REPO_ID + ')' if hf_sync.enabled() else 'OFF (로컬 전용)'}")
    print("  종료: Ctrl+C")
    print("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
        server.shutdown()


if __name__ == "__main__":
    main()
