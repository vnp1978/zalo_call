"""Xem ANH / VIDEO tren Zalo thang tu may chu Zalo, KHONG luu ve dia (nhu Zalo Web): may chu chuyen tiep (proxy) dung phien Zalo, giao dien chi thay mot
dia chi cung goc. Ly do can proxy: dia chi anh/video cua Zalo doi cookie phien + User-Agent dung kieu, trinh duyet o 127.0.0.1 khong tu tai duoc (hien the «Mo»).

An toan (dia chi KHONG nhan tu nguoi dung):
  - URL lay tu tin da luu trong message_store (theo thread + msg_id), khong tu tham so.
  - Chi https toi ten mien Zalo (ZALO_MEDIA_SUFFIXES), khong IP, khong cong la.
  - Chi tra noi dung image/*, video/*, audio/* (octet-stream thi doan theo duoi ten tep); HTML/JS bi tu choi de khong thanh XSS cung goc.
"""
import ipaddress
import mimetypes
import urllib.parse

ZALO_MEDIA_SUFFIXES = ("zdn.vn", "zadn.vn", "dlfl.vn", "dlmd.me", "zalo.me", "zaloapp.com", "zalocdn.com")      # đo trên .200 19/11 (kho tin thật): zdn.vn = ảnh; dlfl.vn, dlmd.me = VIDEO (vd fg41.dlfl.vn, video-stal-6.dlmd.me); các tên miền này không đòi cookie/Referer và hỗ trợ Range
MEDIA_PREFIXES = ("image/", "video/", "audio/")


def allowed_url(url):
    """True khi `url` la https toi mot ten mien Zalo cho phep (khong IP, khong cong khac 443, khong user:pass@)."""
    try:
        p = urllib.parse.urlsplit(str(url or ""))
        host = (p.hostname or "").lower()
        if p.scheme != "https" or not host or p.username or p.password or (p.port not in (None, 443)):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return any(host == s or host.endswith("." + s) for s in ZALO_MEDIA_SUFFIXES)
    except ValueError:
        return False


def pick_url(msg, thumb=False):
    """Dia chi de xem cua mot tin: thumb=True uu tien anh thu nho; khong co thi lay file_url."""
    if not isinstance(msg, dict):
        return ""
    raw = msg.get("raw") if isinstance(msg.get("raw"), dict) else {}
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    order = (msg.get("thumb_url"), content.get("thumb"), msg.get("file_url")) if thumb else (msg.get("file_url"), msg.get("thumb_url"))
    for u in order:
        if u and isinstance(u, str):
            return u
    return ""


_KIND_DEFAULT = {"video": "video/mp4", "photo": "image/jpeg"}


def media_type(upstream_type, file_name="", kind=""):
    """Content-Type an toan de tra cho trinh duyet, hoac "" khi KHONG phai anh/video/am thanh (phai tu choi).
    Zalo hay tra application/octet-stream va dia chi khong co duoi tep (vd .../a55d/4477): doan theo duoi ten tep, khong co thi theo loai tin (video → mp4, anh → jpeg)."""
    t = (upstream_type or "").split(";")[0].strip().lower()
    if t.startswith(MEDIA_PREFIXES) and t != "image/svg+xml":
        return t
    if t in ("", "application/octet-stream", "binary/octet-stream"):
        g = (mimetypes.guess_type(file_name or "")[0] or "").lower()
        if g.startswith(MEDIA_PREFIXES) and g != "image/svg+xml":
            return g
        return _KIND_DEFAULT.get(str(kind or "").lower(), "")
    return ""
