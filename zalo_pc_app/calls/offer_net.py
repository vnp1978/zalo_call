"""Chất lượng đường mạng của ĐIỆN THOẠI gọi đến, đọc từ chính lời mời (offer): điện thoại tự đo từng máy chủ trung chuyển (``extendData.serverResult``: rtt ms, số gói nhận) và Zalo
tự khai ngưỡng xếp loại (``settings.rangeRtt`` = [tốt, xấu], hiện [300, 800] ms). Ghi vào nhật ký để đối chiếu với cuộc gọi bị cắt ``dead_media`` / «tín hiệu yếu» mà không cần bắt gói.

Đo 16/11/2026 (7 cuộc gọi đến, 2 máy chủ khác nhau): rtt cao nhất đều chết (938, 1480, 1517 ms) nhưng một cuộc 610 ms cũng chết, cuộc 739 ms vẫn chạy ⇒ rtt chỉ là DẤU HIỆU,
không phải ngưỡng cắt: không dùng nó để quyết định gì, chỉ ghi lại."""
import json

OFFER_NET = {}               # call_id -> dict tóm tắt (giữ tối đa MAX_KEEP cuộc gần nhất)
MAX_KEEP = 50
ALLOC = {}                   # call_id -> ["ip:port", ...] relay mà Zalo CẤP RIÊNG cho bên nghe máy (lệnh 11300), khác với relay trong lời mời của bên gọi


def remember_alloc(call_id, addrs):
    ALLOC[str(call_id)] = list(addrs)
    while len(ALLOC) > MAX_KEEP:
        ALLOC.pop(next(iter(ALLOC)))


def _obj(x):
    if isinstance(x, str):
        try:
            x = json.loads(x)
        except ValueError:
            return {}
    return x if isinstance(x, dict) else {}


def _ip(addr):
    return str(addr or "").rsplit(":", 1)[0]


def summarize(event):
    """Từ sự kiện «request» (lời mời) → {relay, rtt, recv, rating, limits, servers:[(ip, rtt, recv)]}; {} nếu lời mời không mang số đo."""
    d = _obj((event or {}).get("data"))
    pr = _obj(d.get("params"))
    ex = _obj(pr.get("extendData"))
    st = _obj(pr.get("settings"))
    addrs = [str(s.get("rtp")) for s in (ex.get("serverResult") or []) if isinstance(s, dict) and isinstance(s.get("rtt"), (int, float)) and s.get("rtp")]
    rows = [(_ip(s.get("rtp")), s.get("rtt"), s.get("recv")) for s in (ex.get("serverResult") or []) if isinstance(s, dict) and isinstance(s.get("rtt"), (int, float))]
    if not rows:
        return {}
    relay = _ip(d.get("rtpAddress") or (event or {}).get("rtp_address"))
    mine = next((r for r in rows if r[0] == relay), rows[0])
    lim = st.get("rangeRtt")
    limits = [float(x) for x in lim][:2] if isinstance(lim, list) and len(lim) >= 2 else [300.0, 800.0]
    rtt = float(mine[1])
    rating = "tot" if rtt <= limits[0] else ("vua" if rtt <= limits[1] else "xau")
    return {"relay": mine[0], "rtt": int(rtt), "recv": mine[2], "rating": rating, "limits": limits, "servers": rows, "addrs": addrs}


def remember(call_id, info):
    OFFER_NET[str(call_id)] = info
    while len(OFFER_NET) > MAX_KEEP:
        OFFER_NET.pop(next(iter(OFFER_NET)))


def line(info):
    """Một dòng chữ ngắn cho nhật ký; '' nếu không có số đo."""
    if not info:
        return ""
    return "relay=%s rtt_dien_thoai=%dms recv=%s/20 danh_gia=%s (nguong zalo %s) cac_relay=%s" % (
        info["relay"], info["rtt"], info["recv"], info["rating"], "/".join("%d" % x for x in info["limits"]),
        [(ip, r) for ip, r, _ in info["servers"]])
