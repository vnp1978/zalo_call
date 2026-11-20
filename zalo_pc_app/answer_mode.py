"""Chế độ NGHE MÁY cuộc gọi đến tự chọn và NHỚ (fallback thay cho biến môi trường mà add-on không đặt được).

Vì sao có: lệnh nghe máy 11303 bị Zalo trả `-101` khi điện thoại bên gọi báo «đã nghe» TRƯỚC 11303 (đo 17/11: 5/5 cuộc `-101` mất media, 2/2 cuộc 11303 ok nhận 100%).
Cách tránh có hai cờ (giữ gói tiếng tới khi 11303 xong = «hold»; thân 11303 kiểu gọn = «early»), nhưng cờ nào đủ trên máy NÀO thì chưa biết trước, và người dùng add-on không
đặt được biến môi trường. Nên: thử lần lượt các bậc từ bậc đã đo tốt nhất, ghi nhận từng cuộc, hai cuộc `-101` liên tiếp ở một bậc thì sang bậc kế; bậc nào chạy đúng thì đi tiếp ở bậc đó;
sau RETRY_TOP_AFTER cuộc tốt liên tiếp ở bậc thấp thì thử lại bậc đầu một lần (vì mạng/phiên có thể đã khác). Biến môi trường đặt rõ thì thắng (không tự đổi).

Tín hiệu «xấu» CHỈ là `-101` của 11303 (dấu hiệu đã đo, khớp 5/5): không tính «chưa thấy gói media» vì nguyên nhân đó còn có thể là mạng điện thoại (relay rtt > 800 ms), đổi bậc không chữa được.
"""
import json
import os
import threading

# (hold, early): bậc 0 = cấu hình đã đo tốt trên máy .200 (11303 ok, nhận 100%); bậc cuối = hành vi cũ (không giữ tiếng, thân 11303 đầy đủ).
LADDER = ((True, True), (True, False), (False, True), (False, False))
BAD_STREAK_TO_STEP = 2
RETRY_TOP_AFTER = 8
_LOCK = threading.Lock()


def _empty():
    return {"step": 0, "bad_streak": 0, "good_since_step": 0, "stats": {}}


class AnswerModeBook:
    def __init__(self, path):
        self.path = path

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            if not isinstance(d, dict):
                return _empty()
            out = _empty()
            out.update({k: d[k] for k in out if k in d})
            if not (isinstance(out["step"], int) and 0 <= out["step"] < len(LADDER)):
                out["step"] = 0
            return out
        except (OSError, ValueError):
            return _empty()

    def _save(self, d):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass                                    # không ghi được thì mỗi cuộc chọn lại từ trạng thái cũ: không làm hỏng cuộc gọi

    def current(self):
        """(bậc, hold, early) dùng cho cuộc gọi kế."""
        with _LOCK:
            step = self._load()["step"]
        return (step,) + LADDER[step]

    def record(self, step, got_101):
        """Ghi kết quả một cuộc đã nghe ở `step`. Trả bậc dùng cho cuộc kế (có thể đã đổi)."""
        with _LOCK:
            d = self._load()
            key = "%d" % step
            st = d["stats"].setdefault(key, {"ok": 0, "minus101": 0})
            if got_101:
                st["minus101"] += 1
                d["bad_streak"] += 1
                d["good_since_step"] = 0
                if step == d["step"] and d["bad_streak"] >= BAD_STREAK_TO_STEP:
                    d["step"] = (d["step"] + 1) % len(LADDER)
                    d["bad_streak"] = 0
            else:
                st["ok"] += 1
                d["bad_streak"] = 0
                if step == d["step"] and d["step"] > 0:
                    d["good_since_step"] += 1
                    if d["good_since_step"] >= RETRY_TOP_AFTER:
                        d["step"], d["good_since_step"] = 0, 0       # thử lại bậc đầu: nếu lại -101 hai lần thì tự rơi xuống bậc cũ
            self._save(d)
            return d["step"]


def resolve(env_hold, env_early, book):
    """Gộp biến môi trường (nếu đặt) với bậc tự chọn. env_* là chuỗi hoặc None (chưa đặt). Trả (step|None, hold, early, nguồn)."""
    if env_hold is not None or env_early is not None:
        step, hold, early = book.current()
        return (None,
                (env_hold == "1") if env_hold is not None else hold,
                (env_early == "1") if env_early is not None else early,
                "env")
    step, hold, early = book.current()
    return step, hold, early, "auto"
