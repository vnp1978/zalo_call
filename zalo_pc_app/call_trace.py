"""Nhật ký CHI TIẾT từng cuộc gọi (16/11/2026): để điều tra lỗi bằng số liệu thay vì đoán.

Mỗi cuộc gọi một tệp văn bản `<DATA_DIR>/calltrace/<call_id>.log`, cuộc TỐT chỉ giữ ``KEEP`` cuộc gần nhất (mặc định 3), cuộc LỖI được đánh dấu (tệp `<call_id>.loi`) và giữ tới ``KEEP_BAD`` cuộc (mặc định 30). Ghi RA TỆP trong thư mục dữ liệu của app
(add-on: /data, Docker: volume dữ liệu) — KHÔNG đi vào cơ sở dữ liệu / recorder của Home Assistant nên không làm DB phình.

Mỗi dòng: `HH:MM:SS.mmm +T.TTTs <loại> nội dung`, T = giây kể từ dòng đầu tiên của cuộc gọi. Gồm:
  - mọi dòng `[call]` có call_id (vòng đời, 11300/11303, answer, dead_media, offer-net, t-mốc…) — chép NGUYÊN VĂN từ `_log_call`;
  - mọi dòng `[media]` của transport (chọn relay, gói đầu, thử lại thiết lập…);
  - TỪNG GÓI UDP gửi/nhận (hướng, độ dài, byte đầu, loại, relay số mấy hay p2p, 12 byte đầu dạng hex): đủ ``PACKET_FULL_SECONDS`` giây đầu,
    sau đó chỉ mỗi ``PACKET_SAMPLE_EVERY`` gói một dòng (gói điều khiển/lạ luôn được ghi);
  - mỗi giây: HIỆU số của mọi bộ đếm transport (rtp_rx, ping_tx…), thống kê mic và loa; mỗi 5 giây thêm tài nguyên của chính tiến trình
    (CPU %, RSS, số luồng, tải máy) để phân biệt «mạng yếu» với «app/máy quá tải».
Không bao giờ ghi bí mật: giá trị của biến môi trường có chữ PASSWORD/TOKEN/KEY/SECRET/HASH bị bỏ; chuỗi dài giống token bị che. Mọi lỗi của bộ ghi bị nuốt:
chẩn đoán không được làm hỏng cuộc gọi. Tắt hẳn: ZALO_PC_CALLTRACE_KEEP=0.

Cuộc LỖI = (khi kết thúc nhật ký) có một trong: mã gọi đánh dấu tường minh `mark_bad()` (vd 11303 bị `-101`); kết thúc vì dead_media/lỗi/quá hạn; đã nghe máy mà transport
media chẳng bao giờ dựng; transport có nhưng `rtp_rx` = 0 (điện thoại không gửi gói nào); lỗi socket gửi/nhận. Không dựa danh sách từ khoá của nội dung log."""
import os
import re
import threading
import time

KEEP = int(os.environ.get("ZALO_PC_CALLTRACE_KEEP", "3") or 0)                  # số cuộc TỐT giữ lại; 0 = tắt hẳn
KEEP_BAD = int(os.environ.get("ZALO_PC_CALLTRACE_KEEP_BAD", "30") or 30)        # số cuộc LỖI giữ lại (mỗi cuộc ≤ MAX_BYTES)
MAX_BYTES = int(os.environ.get("ZALO_PC_CALLTRACE_MAX_KB", "6144") or 6144) * 1024   # trần mỗi cuộc (6 MB): ~5 phút đầy đủ từng gói
PACKET_FULL_SECONDS = float(os.environ.get("ZALO_PC_CALLTRACE_FULL_S", "20") or 20)
PACKET_SAMPLE_EVERY = int(os.environ.get("ZALO_PC_CALLTRACE_EVERY", "25") or 25)
SAMPLE_SECONDS = 1.0
SNAP_EVERY = 5
TAIL_AFTER_END = 6.0             # sau dòng kết thúc còn ghi thêm vài giây (bộ đếm cuối, dọn dẹp)
MAX_SAMPLER_SECONDS = 4 * 3600
_CALL_ID = re.compile(r"call_id=(\d+)")
_END = re.compile(r"^(end|logendcall) call_id=")
_END_REASON = re.compile(r"^end call_id=\d+ reason=(\S+)")
_BAD_REASON = re.compile(r"dead_media|error|fail|timeout|expired", re.I)
_ANSWERED = re.compile(r"^answered call_id=")
BAD_SUFFIX = ".loi"
_SECRET_NAME = re.compile(r"PASSWORD|TOKEN|KEY|SECRET|HASH|COOKIE", re.I)
_LONG = re.compile(r"[A-Za-z0-9+/=_\-]{40,}")
_PACKET_NAMES = {0x01: "ctl01", 0x02: "setup", 0x04: "media", 0x05: "rtcp", 0x0E: "video", 0x0F: "vrtcp", 0x7F: "p2p"}

_lock = threading.RLock()
_dir = None
_providers = {}
_traces = {}                     # call_id(str) -> _Trace
_active = None                   # call_id gần nhất còn mở: dòng [media] không mang call_id được gắn vào đây


def configure(data_dir, transport=None, mic=None, speaker=None, version="", extra_header=None):
    """Đặt thư mục + các hàm lấy đối tượng theo call_id: transport(cid)->có .counters; mic(cid)/speaker(cid)->có .status()."""
    global _dir, _providers
    _dir = os.path.join(data_dir, "calltrace") if data_dir else None
    _providers = {"transport": transport, "mic": mic, "speaker": speaker, "version": version, "extra": extra_header}


def enabled():
    return KEEP > 0 and _dir is not None


def mask(text):
    return _LONG.sub("<ẩn>", str(text))


class _Trace:
    def __init__(self, call_id, path):
        self.call_id = call_id
        self.path = path
        self.t0 = time.time()
        self.size = 0
        self.lines = 0
        self.full = True                 # còn ghi được (chưa chạm trần)
        self.ended_at = None
        self.closed = False
        self.bad = None                  # lý do lỗi (chuỗi) hoặc None
        self.answered = False
        self.pkt_n = {"rx": 0, "tx": 0}
        self.last_counters = {}
        self.last_cpu = None
        self.fh = open(path, "a", encoding="utf-8", buffering=1)
        self.sampler = None

    def write(self, kind, text, force=False):
        """Một dòng. Chạm trần dung lượng thì chỉ để lại MỘT dòng báo cắt và các dòng `force` (kết thúc, tổng kết)."""
        now = time.time()
        if not self.full and not force:
            return
        line = "%s.%03d +%.3fs %s %s\n" % (time.strftime("%H:%M:%S", time.localtime(now)), int((now % 1) * 1000), now - self.t0, kind, text)
        if self.size + len(line) > MAX_BYTES and not force:
            if self.full:
                self.full = False
                line = "%s.%03d +%.3fs trần dung lượng %d KB: ngừng ghi chi tiết (chỉ còn dòng kết thúc)\n" % (
                    time.strftime("%H:%M:%S", time.localtime(now)), int((now % 1) * 1000), now - self.t0, MAX_BYTES // 1024)
            else:
                return
        try:
            self.fh.write(line)
            self.size += len(line.encode("utf-8"))
            self.lines += 1
        except (OSError, ValueError):
            self.full = False

    def close(self):
        self.closed = True
        try:
            self.fh.close()
        except (OSError, ValueError):
            pass


def _header(t, call_id):
    env = []
    for k in sorted(os.environ):
        if k.startswith("ZALO_PC_") and not _SECRET_NAME.search(k):
            env.append("%s=%s" % (k, mask(os.environ[k])[:60]))
    t.write("mo-dau", "call_id=%s app=%s pid=%d keep=%d max_kb=%d full_s=%g every=%d" % (call_id, _providers.get("version", ""), os.getpid(), KEEP, MAX_BYTES // 1024, PACKET_FULL_SECONDS, PACKET_SAMPLE_EVERY))
    if env:
        t.write("moi-truong", " ".join(env))
    fn = _providers.get("extra")
    if callable(fn):
        try:
            t.write("cau-hinh", mask(fn(call_id))[:900])
        except Exception as exc:  # noqa: BLE001 - chẩn đoán
            t.write("cau-hinh", "loi: %s" % exc)


def _bad_path(log_path):
    return log_path[:-4] + BAD_SUFFIX


def _prune():
    """Cuộc TỐT: giữ KEEP cuộc mới nhất. Cuộc LỖI (có tệp .loi): giữ KEEP_BAD cuộc mới nhất.
    Cuộc CHƯA CÓ KẾT LUẬN (còn đang ghi, hoặc đã hết cuộc nhưng bộ lấy mẫu chưa chốt) KHÔNG được tính vào suất «tốt» và không bị xoá: nếu tính thì
    gọi liên tiếp vài cuộc, cuộc nào rồi cũng thành «lỗi» sẽ chiếm suất và đẩy cuộc tốt cũ ra ngoài (đã gặp 18/11)."""
    try:
        pending = {t.path for t in _traces.values() if not t.closed}
        files = [os.path.join(_dir, f) for f in os.listdir(_dir) if f.endswith(".log")]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        good = [p for p in files if not os.path.exists(_bad_path(p)) and p not in pending]
        bad = [p for p in files if os.path.exists(_bad_path(p))]
        for p in good[KEEP:] + bad[KEEP_BAD:]:
            for victim in (p, _bad_path(p)):
                try:
                    os.remove(victim)
                except OSError:
                    pass
    except OSError:
        pass


def mark_bad(call_id, reason):
    """Mã gọi đánh dấu tường minh cuộc này LỖI (vd 11303 trả -101). Lý do đầu tiên thắng."""
    if not enabled():
        return
    try:
        t = _get(call_id, create=True)
        if t is not None and t.bad is None:
            t.bad = str(reason)[:80]
            t.write("danh-dau-loi", mask(t.bad), force=True)
            with open(_bad_path(t.path), "w", encoding="utf-8") as fh:      # đánh dấu ngay: cuộc đang gọi cũng không bị dọn
                fh.write(t.bad + "\n")
    except Exception:  # noqa: BLE001
        pass


def _verdict(t):
    """Lý do cuộc này lỗi, hoặc None nếu tốt. Chỉ dựa tín hiệu: đánh dấu tường minh, lý do kết thúc, bộ đếm transport."""
    if t.bad:
        return t.bad
    c = t.last_counters
    if t.answered:
        if not c:
            return "da_nghe_may_nhung_khong_dung_media"
        if not c.get("rtp_rx"):
            return "khong_nhan_goi_media_nao"
    if c.get("recv_errors") or c.get("send_errors"):
        return "loi_socket"
    return None


def _get(call_id, create=True):
    global _active
    cid = str(call_id)
    with _lock:
        t = _traces.get(cid)
        if t is not None or not create or not enabled():
            return t
        try:
            os.makedirs(_dir, exist_ok=True)
            t = _Trace(cid, os.path.join(_dir, cid + ".log"))
        except OSError:
            return None
        _traces[cid] = t
        _active = cid
        # chỉ giữ trong bộ nhớ những cuộc còn mở: cuộc đã đóng được dọn khi có cuộc mới
        for k in [k for k, v in _traces.items() if v.ended_at is not None and k != cid and time.time() - v.ended_at > TAIL_AFTER_END + 2]:
            _traces.pop(k).close()
        _header(t, cid)
        _prune()
        _start_sampler(t)
        return t


def mirror(msg):
    """Gọi từ server._log_call cho MỌI dòng [call]; dòng có call_id vào đúng tệp của cuộc đó (tự tạo tệp khi gặp cuộc mới)."""
    if not enabled():
        return
    try:
        m = _CALL_ID.search(msg)
        if not m:
            return
        t = _get(m.group(1))
        if t is None:
            return
        t.write("call", mask(msg))
        if _ANSWERED.match(msg):
            t.answered = True
        m2 = _END_REASON.match(msg)
        if m2 and t.bad is None and _BAD_REASON.search(m2.group(1)):
            t.bad = "ket_thuc_" + m2.group(1)[:40]
        if _END.match(msg) and t.ended_at is None:
            t.ended_at = time.time()
    except Exception:  # noqa: BLE001 - chẩn đoán không được làm hỏng cuộc gọi
        pass


def media(msg, call_id=None):
    """Dòng [media] của transport; call_id biết thì dùng, không thì gắn vào cuộc đang mở gần nhất."""
    if not enabled():
        return
    try:
        t = _get(call_id if call_id else _active, create=False) if (call_id or _active) else None
        if t is not None:
            t.write("media", mask(msg))
    except Exception:  # noqa: BLE001
        pass


def event(call_id, name, text=""):
    """Dòng tuỳ ý (mốc, quyết định, lỗi bắt được) do mã gọi trực tiếp."""
    if not enabled():
        return
    try:
        t = _get(call_id, create=True)
        if t is not None:
            t.write(name, mask(text))
    except Exception:  # noqa: BLE001
        pass


def packet(call_id, direction, data, addr_label="", note=""):
    """Một gói UDP. direction 'rx'|'tx'. Đủ PACKET_FULL_SECONDS giây đầu; sau đó 1 trong PACKET_SAMPLE_EVERY (gói lạ/điều khiển luôn được ghi)."""
    if not enabled():
        return
    try:
        t = _get(call_id, create=False)
        if t is None or not t.full:
            return
        t.pkt_n[direction] = t.pkt_n.get(direction, 0) + 1
        n = t.pkt_n[direction]
        head = data[0] if data else -1
        name = _PACKET_NAMES.get(head, "lạ%02X" % head if head >= 0 else "rỗng")
        if time.time() - t.t0 > PACKET_FULL_SECONDS and head in (0x04, 0x0E) and n % max(1, PACKET_SAMPLE_EVERY) != 0:
            return
        # Gói ĐIỀU KHIỂN nhận về (type-02 trả lời/thông báo của relay, gói lạ): ghi TOÀN BỘ (≤128 B) để đọc được thông báo «đổi relay» của điện thoại
        # (ZRTPChanger → RequestForward sub 0x15); gói media/ping thì chỉ 12 byte đầu.
        full = direction == "rx" and len(data) <= 128 and (head in (0x01, 0x02) or name.startswith("lạ")) and getattr(t, "ctl_full", 0) < 250
        if full:
            t.ctl_full = getattr(t, "ctl_full", 0) + 1                                    # tối đa 250 gói điều khiển/cuộc ghi đầy đủ
        t.write("goi-" + direction, "#%d %s len=%d via=%s hex=%s%s" % (n, name, len(data), addr_label or "?", bytes(data[:128 if full else 12]).hex(), (" " + note) if note else ""))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- lấy mẫu mỗi giây
def _count_fds():
    """Số tệp/ổ cắm đang mở của tiến trình: tăng dần qua các cuộc gọi = bị rò (cuộc trước chưa đóng hết)."""
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return "-"


def _count_children():
    """Số tiến trình con còn sống (ffmpeg của mic/loa/camera). Sau cuộc gọi phải về 0."""
    try:
        me, n = os.getpid(), 0
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open("/proc/%s/stat" % d) as f:
                        if int(f.read().rsplit(")", 1)[1].split()[1]) == me:
                            n += 1
                except (OSError, ValueError, IndexError):
                    pass
        return n
    except OSError:
        return "-"


def _count_udp():
    """Số ổ UDP đang mở trong không gian mạng của container (mỗi cuộc gọi một ổ; sau cuộc gọi phải về mức nền)."""
    n = 0
    for path in ("/proc/net/udp", "/proc/net/udp6"):
        try:
            with open(path) as f:
                n += max(0, len(f.read().splitlines()) - 1)
        except OSError:
            return "-"
    return n


def _proc_stats(t):
    """CPU % (so với 1 lõi) của CHÍNH tiến trình từ lần trước, RSS MB, số luồng, tải máy."""
    try:
        with open("/proc/self/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        ticks = int(fields[11]) + int(fields[12])
        now = time.monotonic()
        cpu = None
        if t.last_cpu is not None:
            dt = now - t.last_cpu[1]
            if dt > 0:
                cpu = (ticks - t.last_cpu[0]) / float(os.sysconf("SC_CLK_TCK")) / dt * 100.0
        t.last_cpu = (ticks, now)
        with open("/proc/self/status") as f:
            st = f.read()
        rss = re.search(r"VmRSS:\s+(\d+)", st)
        thr = re.search(r"Threads:\s+(\d+)", st)
        la = os.getloadavg()
        return "cpu=%s rss=%sMB thr=%s fd=%s con=%s udp=%s load=%.2f/%.2f/%.2f" % (
            "%.0f%%" % cpu if cpu is not None else "-", int(rss.group(1)) // 1024 if rss else "-", thr.group(1) if thr else "-",
            _count_fds(), _count_children(), _count_udp(), la[0], la[1], la[2])
    except (OSError, ValueError, IndexError, AttributeError):
        return "tai-nguyen: khong doc duoc"


def sample_once(t, tick):
    """MỘT lần lấy mẫu (tách riêng cho test): hiệu bộ đếm transport + trạng thái mic/loa; mỗi SNAP_EVERY lần thêm tài nguyên + giá trị tuyệt đối."""
    tr = _providers.get("transport")
    obj = tr(t.call_id) if callable(tr) else None
    cnt = dict(getattr(obj, "counters", {}) or {}) if obj is not None else None
    if cnt is None:
        if tick % SNAP_EVERY == 0:
            t.write("ts-1s", "chua co transport media (hoac da dong)")
    else:
        diff = []
        for k in sorted(cnt):
            v = cnt[k]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                d = v - t.last_counters.get(k, 0)
                if d:
                    diff.append("%s%+d" % (k, d) if isinstance(d, int) else "%s%+.2f" % (k, d))
        t.last_counters = {k: v for k, v in cnt.items() if isinstance(v, (int, float))}
        if diff:
            t.write("dem-1s", " ".join(diff))
        if tick % SNAP_EVERY == 0:
            t.write("dem-tong", " ".join("%s=%s" % (k, cnt[k]) for k in sorted(cnt) if isinstance(cnt[k], (int, float)) and cnt[k]))
    if tick % SNAP_EVERY == 0:
        for name in ("mic", "speaker"):
            fn = _providers.get(name)
            o = fn(t.call_id) if callable(fn) else None
            if o is not None:
                try:
                    t.write(name, mask(", ".join("%s=%s" % (k, str(v)[:48]) for k, v in o.status().items() if k != "ffmpeg_err")))
                except Exception as exc:  # noqa: BLE001
                    t.write(name, "loi doc thong ke: %s" % exc)
        t.write("tai-nguyen", _proc_stats(t))


def _sampler(t):
    """Mỗi giây một mẫu cho tới SAU khi cuộc gọi kết thúc TAIL_AFTER_END giây; dừng sớm nếu chẳng bao giờ có transport (cuộc không vào được media)
    hoặc quá MAX_SAMPLER_SECONDS (không để luồng chạy mãi khi thiếu dòng kết thúc)."""
    tick = 0
    started = time.time()
    no_transport = 0
    while True:
        time.sleep(SAMPLE_SECONDS)
        if time.time() - started > MAX_SAMPLER_SECONDS or no_transport > 600:
            break
        try:
            sample_once(t, tick)
            tr = _providers.get("transport")
            no_transport = no_transport + 1 if not (callable(tr) and tr(t.call_id) is not None) else 0
        except Exception as exc:  # noqa: BLE001
            t.write("loi-bo-ghi", "%s: %s" % (type(exc).__name__, str(exc)[:80]))
        tick += 1
        if t.ended_at is not None and time.time() - t.ended_at > TAIL_AFTER_END:
            break
    why = None
    try:
        sample_once(t, 0)
        why = _verdict(t)
        t.write("ket-thuc-nhat-ky", "dong=%d byte=%d goi_rx=%d goi_tx=%d ket_luan=%s" % (t.lines, t.size, t.pkt_n.get("rx", 0), t.pkt_n.get("tx", 0), ("LOI:" + why) if why else "tot"), force=True)
    finally:
        t.close()
    try:
        if why:
            with open(_bad_path(t.path), "w", encoding="utf-8") as fh:
                fh.write(why + "\n")
        _prune()
    except OSError:
        pass


def _start_sampler(t):
    if SAMPLE_SECONDS <= 0:
        return
    th = threading.Thread(target=_sampler, args=(t,), daemon=True, name="calltrace-%s" % t.call_id)
    t.sampler = th
    th.start()


# ---------------------------------------------------------------- đọc ra (API)
def list_traces():
    """Danh sách cuộc có nhật ký: mới nhất trước. [{call_id, bytes, modified, lines?}]"""
    out = []
    if _dir is None:
        return out
    try:
        for f in os.listdir(_dir):
            if f.endswith(".log") and f[:-4].isdigit():
                p = os.path.join(_dir, f)
                st = os.stat(p)
                out.append({"call_id": f[:-4], "bytes": st.st_size, "modified": int(st.st_mtime), "_m": st.st_mtime, "loi": _read_reason(p), "live": f[:-4] in _traces and _traces[f[:-4]].ended_at is None})
    except OSError:
        pass
    out.sort(key=lambda r: r["_m"], reverse=True)            # so thuc: hai cuoc cach nhau vai chuc ms van dung thu tu
    for r in out:
        r.pop("_m")
    return out


def _read_reason(log_path):
    try:
        with open(_bad_path(log_path), encoding="utf-8") as fh:
            return fh.read().strip() or "loi"
    except OSError:
        return None


def read(call_id, max_bytes=None):
    """Nội dung nhật ký của một cuộc (chỉ nhận call_id toàn chữ số: không đường dẫn tuỳ ý). None nếu không có."""
    cid = str(call_id)
    if not cid.isdigit() or _dir is None:
        return None
    p = os.path.join(_dir, cid + ".log")
    try:
        with open(p, "rb") as f:
            data = f.read(max_bytes) if max_bytes else f.read()
        return data.decode("utf-8", "replace")
    except OSError:
        return None


def reset_for_tests():
    global _active
    with _lock:
        for t in _traces.values():
            t.ended_at = t.ended_at or time.time()
            t.close()
        _traces.clear()
        _active = None
