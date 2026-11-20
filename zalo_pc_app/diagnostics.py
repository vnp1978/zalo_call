"""Báo cáo chẩn đoán cho người dùng không biết gõ lệnh: một khối chữ (phiên bản, môi trường, kiểm chứng chỉ HTTPS, trạng thái danh bạ, vài trăm dòng nhật ký gần nhất)
để bấm «Sao chép» hoặc «Tải về» ngay trong Cài đặt. Mọi thứ nhạy cảm (mật khẩu, token, cookie, imei, khoá) bị che TRƯỚC khi ra khỏi máy chủ.
Chỉ đọc: không gọi Zalo, không ghi gì."""
import os
import platform
import re
import socket
import ssl
import sys
import time

ENV_SHOWN = ("SSL_CERT_FILE", "ZALO_PC_VERSION", "ZALO_PC_CALL_CLIENT", "ZALO_PC_PORT", "ZALO_PC_HOST", "FFMPEG_BIN", "ZALO_PC_LOG_TS")

# Tên trường nhạy cảm đứng trước «:» hoặc «=»; giá trị là chuỗi trong nháy hoặc tới khoảng trắng/dấu phân cách.
_SECRET_KEY = re.compile(
    r"""(?ix)\b([\w.-]*(?:pass(?:word|wd)?|mat_?khau|secret|zpw_enk|zpw_sek|zpsid|zpw_sid|cookie|authorization|token|imei|api[_-]?key|sess(?:_?id)?|enk|sek)[\w.-]*)(["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;&}\]]+)""")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_LONG_BLOB = re.compile(r"[A-Za-z0-9_+=-]{40,}")           # khoá/mã dài còn sót (base64, hex); KHÔNG gồm «/» để đường dẫn tệp còn đọc được
_URL_QUERY = re.compile(r"(?i)([?&](?:t|token|key|sid|code|auth)=)[^&\s]+")          # mã trong địa chỉ (vd. /api/app/autologin?t=...)
_URL_AUTH = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")


def redact(text):
    text = _URL_AUTH.sub(r"\1***:***@", text or "")
    text = _URL_QUERY.sub(r"\1***", text)
    text = _BEARER.sub(lambda m: m.group(1) + " ***", text)
    text = _SECRET_KEY.sub(lambda m: m.group(1) + m.group(2) + "***", text)
    return _LONG_BLOB.sub("***", text)


def tail_lines(path, n=300, max_bytes=262144):
    """n dòng cuối của tệp (đọc tối đa max_bytes cuối tệp); thiếu tệp → []."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]                                   # dòng đầu có thể bị cắt giữa chừng
    return lines[-n:]


def tls_probe(host="zalo.me", port=443, timeout=6.0, connect=socket.create_connection, wrap=None):
    """«ok» khi bắt tay TLS và xác minh chứng chỉ qua; «LỖI chứng chỉ: …» CHỈ khi chứng chỉ bị từ chối (đúng lớp lỗi CERTIFICATE_VERIFY_FAILED); mất mạng thì «không kết nối được: …»."""
    try:
        sock = connect((host, port), timeout=timeout)
    except OSError as exc:
        return "không kết nối được: %s" % exc
    try:
        ctx = ssl.create_default_context()
        with (wrap or ctx.wrap_socket)(sock, server_hostname=host):
            return "ok"
    except ssl.SSLCertVerificationError as exc:
        return "LỖI chứng chỉ: %s" % exc
    except (ssl.SSLError, OSError) as exc:
        return "lỗi TLS khác: %s" % exc
    finally:
        try:
            sock.close()
        except OSError:
            pass


def contacts_summary(cache, backoff_until=0.0, now=None):
    now = time.time() if now is None else now
    out = []
    if cache:
        out.append("đã tải: %d bạn bè, %d nhóm, cách đây %d giây" % (len(cache.get("friends") or []), len(cache.get("groups") or []), max(0, int(now - (cache.get("fetched_at") or now)))))
    else:
        out.append("chưa có bản tải nào trong bộ nhớ")
    if backoff_until and backoff_until > now:
        out.append("đang chờ Zalo giới hạn tần suất (429) thêm %d giây" % int(backoff_until - now))
    return "; ".join(out)


def build_report(data_dir, version, status, contacts, probe=tls_probe, env=None, log_lines=300):
    env = os.environ if env is None else env
    st = status or {}
    sync = st.get("sync") if isinstance(st.get("sync"), dict) else {}
    parts = [
        "== Zalo Call — báo cáo chẩn đoán (%s) ==" % time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "phiên bản: %s" % (version or "?"),
        "hệ điều hành: %s | python %s | đóng gói: %s" % (platform.platform(), platform.python_version(), "có" if getattr(sys, "frozen", False) else "không"),
        "",
        "-- Môi trường --",
    ]
    parts += ["%s=%s" % (k, env[k]) for k in ENV_SHOWN if env.get(k)]
    ssl_paths = ssl.get_default_verify_paths()
    parts.append("kho chứng chỉ mặc định: cafile=%s capath=%s" % (ssl_paths.cafile, ssl_paths.capath))
    parts += ["", "-- Kiểm tra HTTPS tới zalo.me --", probe(), "", "-- Trạng thái --",
              "đã đăng nhập Zalo: %s | kiểu: %s" % ("có" if st.get("login") else "không", st.get("client") or "?"),
              "phụ thuộc: %s" % (", ".join("%s=%s" % (k, "ok" if v else "THIẾU") for k, v in sorted((st.get("deps") or {}).items())) or "?"),
              "đồng bộ: %s" % (sync or "?"),
              "lỗi đồng bộ: %s" % (st.get("sync_error") or "không"),
              "kênh ctrl lỗi: %s" % (st.get("ctrl_error") or "không"),
              "giữ phiên: %s" % ((st.get("keepalive") or {}).get("state") or "?"),
              "danh bạ: %s" % contacts, "",
              "-- %d dòng nhật ký gần nhất --" % log_lines]
    parts += tail_lines(os.path.join(data_dir, "logs", "app.log"), log_lines) or ["(chưa có nhật ký)"]
    return redact("\n".join(parts)) + "\n"
