"""Tự điều chỉnh chất lượng HÌNH app gửi lên điện thoại (Zalo) theo phản hồi của điện thoại — như YouTube hạ/tăng chất lượng.

Tín hiệu (đã đo sẵn trong udp_transport._diag_video_rtcp): khối báo cáo RTCP của điện thoại về luồng hình CỦA TA (tỉ lệ mất gói 0–255, jitter theo xung 90 kHz) và số gói NACK/PLI
điện thoại xin. Ba mức, mức 0 = đúng cấu hình đã đặt (width/height/fps/bitrate_kbps trong cài đặt thiết bị), mức 1 và 2 là hệ số giảm của mức 0.

Luật (cố ý chậm hạ-nhanh, tăng-chậm để khỏi dao động):
  * mẫu «xấu»: có báo cáo mới và (mất gói ≥ 5 % hoặc jitter ≥ 40 ms) hoặc NACK tăng ≥ 30 hoặc PLI tăng ≥ 3 trong chu kỳ lấy mẫu; 10 s đầu (khởi động) không tính;
  * HẠ một mức khi ≥ 2 trong 3 mẫu gần nhất xấu và đã qua DOWN_COOLDOWN giây kể từ lần đổi trước;
  * TĂNG một mức khi liên tục không có mẫu xấu suốt `up_wait` giây (mặc định 15). Hạ lại ngay sau khi vừa tăng (trong 20 s) thì lần tăng kế phải chờ gấp đôi (tối đa 120 s) — nhận ra mạng
    chưa chịu nổi mức cao; ổn định 60 s thì chờ lại như ban đầu.
Hàm thuần, đồng hồ do người gọi cấp (dễ kiểm thử)."""

LOSS_BAD = 13              # 13/256 ≈ 5 %
JITTER_BAD_MS = 40.0
NACK_BAD = 30              # trong một chu kỳ 2 s (đo 18/11: 21 NACK lúc khung khoá đầu cuộc gọi 800 kbit/s đã làm hạ mức oan rồi tăng lại 16 s sau)
WARMUP_S = 10.0            # 10 s đầu cuộc phát hình: khung khoá đầu to gây dồn gói/NACK, KHÔNG tính là xấu
PLI_BAD = 3
DOWN_COOLDOWN = 6.0
UP_WAIT = 15.0
UP_WAIT_MAX = 120.0
FAST_REDEGRADE_S = 20.0
CALM_RESET_S = 60.0

# (hệ số độ phân giải, hệ số fps, hệ số bitrate) so với mức 0
SCALES = ((1.0, 1.0, 1.0), (0.75, 0.8, 0.5), (0.5, 0.55, 0.25))
NAMES = ("cao", "vua", "thap")


def level_params(base, level):
    """(width, height, fps, bitrate_kbps) của `level` từ cấu hình gốc `base` (dict width/height/fps/bitrate_kbps). Kích thước làm chẵn, có sàn để x264 không lỗi."""
    sr, sf, sb = SCALES[max(0, min(level, len(SCALES) - 1))]
    even = lambda v: max(2, int(round(v / 2.0)) * 2)      # noqa: E731
    return (even(int(base["width"]) * sr), even(int(base["height"]) * sr),
            max(5, int(round(int(base["fps"]) * sf))), max(40, int(round(int(base["bitrate_kbps"]) * sb))))


def is_bad(sample):
    """sample: {"fresh": bool, "fraction": 0–255|None, "jitter_ms": float|None, "nack": delta, "pli": delta}."""
    if sample.get("fresh"):
        fr = sample.get("fraction")
        if fr is not None and fr >= LOSS_BAD:
            return True
        jit = sample.get("jitter_ms")
        if jit is not None and jit >= JITTER_BAD_MS:
            return True
    return (sample.get("nack") or 0) >= NACK_BAD or (sample.get("pli") or 0) >= PLI_BAD


class Controller:
    def __init__(self, now=0.0, max_level=len(SCALES) - 1):
        self.level, self.max_level = 0, max_level
        self._t0 = now
        self._recent = []                  # (bad: bool) của 3 mẫu gần nhất
        self._last_change = now
        self._last_bad = now
        self._up_wait = UP_WAIT
        self._last_up_at = None
        self.reason = ""

    def update(self, sample, now):
        """Nạp một mẫu; trả mức mới nếu cần đổi, ngược lại None."""
        bad = is_bad(sample) and now - self._t0 >= WARMUP_S
        self._recent = (self._recent + [bad])[-3:]
        if bad:
            self._last_bad = now
        if self.level >= self.max_level or now - self._last_change < DOWN_COOLDOWN:
            pass
        elif sum(self._recent) >= 2:
            if self._last_up_at is not None and now - self._last_up_at < FAST_REDEGRADE_S:
                self._up_wait = min(UP_WAIT_MAX, self._up_wait * 2)             # vừa tăng đã phải hạ: lần tăng sau chờ lâu hơn
            return self._change(self.level + 1, now, "xau: mat=%s jit=%s nack=%s pli=%s" % (
                sample.get("fraction"), None if sample.get("jitter_ms") is None else round(sample["jitter_ms"], 1), sample.get("nack"), sample.get("pli")))
        if self.level > 0 and not bad and now - self._last_bad >= self._up_wait and now - self._last_change >= self._up_wait:
            self._last_up_at = now
            return self._change(self.level - 1, now, "on dinh %.0fs" % (now - self._last_bad))
        if self.level >= 0 and now - self._last_change >= CALM_RESET_S and now - self._last_bad >= CALM_RESET_S:
            self._up_wait = UP_WAIT
        return None

    def _change(self, level, now, reason):
        self.level, self._last_change, self.reason = level, now, reason
        self._recent = []
        return level
