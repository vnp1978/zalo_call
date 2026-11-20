"""Bộ chia sự kiện cuộc gọi từ kênh ctrl (act request/answer/end_call/audio2video/onoffcamera…) tới các luồng đến/đi/video. Tách từ server.py 14/11/2026."""
import json
from .. import event_dedup as event_dedup_mod

from ._srv import srv


class CallEvents:
    """Phương thức của AppState thuộc trục này (gộp vào AppState bằng kế thừa)."""

    def _on_ctrl_call_event(self, event):
        """Callback call_event tu CtrlPoller (99101): su kien goi
        thoai theo flow zca-js (ZaloRtcSession: call_event act
        "request" -> ringring 11302 -> answer 11303 -> answerack
        11304 o phia caller; tu choi bang cancel 11305 + endcall
        11306). Chi log dang event + id, khong log noi dung."""
        try:
            act_raw = (event or {}).get("act", "")
            # Zalo gui ten co dau gach duoi ("end_call", "ring_ring",
            # "answer_ack", "cancel_call"...): chuan hoa roi moi so khop,
            # neu khong cuoc goi den bi bo qua khi ben goi cup may.
            act = str(act_raw).replace("_", "").replace("-", "").lower()
            call_id = (event or {}).get("call_id")
            if call_id is None:
                return
            _d, _ts = (event or {}).get("data"), None
            if isinstance(_d, dict):
                _p = _d.get("params")
                _ts = _d.get("ts") or (_p.get("ts") if isinstance(_p, dict) else None)
            # Khoa = act + call_id + TOAN BO noi dung data (ts, params...): hai su kien cung ts nhung khac noi dung (tat roi bat camera) la hai su kien.
            # Khong co ts thi KHONG loc (khong the phan biet hai lan bam giong het nhau).
            if _ts and self.event_dedup.seen("call:%s:%s:%s" % (act, call_id, event_dedup_mod.EventDedup.digest(_d))):
                return                              # cung mot su kien den hai nguon (WebSocket + poll): chi xu ly mot lan
            if act != "request":      # «request» da co dong «incoming offer» + «offer-params»; cac su kien con lai ghi day du de xem Zalo bao gi khi thiet bi KHAC nghe may / tu choi
                try:
                    def _clean(o, k=""):
                        if isinstance(o, dict):
                            # khối cấu hình rất dài (zrtcConfig…) làm dòng log bị cắt ở 5000 ký tự, mất các trường cuối (mã nhóm…): chỉ ghi số mục
                            return {a: ("<cau hinh %d muc>" % len(b) if a in ("zrtcConfig", "zrtc_config") and isinstance(b, (dict, list)) else _clean(b, a))
                                    for a, b in o.items()}
                        if isinstance(o, list):
                            return [_clean(b, k) for b in o[:8]]
                        if isinstance(o, str) and any(w in k.lower() for w in ("sess", "key", "token")):
                            return "<%d ky tu>" % len(o)
                        if isinstance(o, str):
                            try:
                                j = json.loads(o)
                                if isinstance(j, (dict, list)):
                                    return _clean(j, k)
                            except ValueError:
                                pass
                            return o if len(o) <= 120 else o[:120] + "..."
                        return o
                    srv()._log_call("call_event raw act=%s call_id=%s %s" % (act_raw, call_id, json.dumps(_clean(event), ensure_ascii=False, sort_keys=True)[:5000]))
                except Exception:  # noqa: BLE001 - chan doan
                    pass
            if act == "request":
                self._on_incoming_call_request(event)
            elif act in ("cancel", "end", "endcall", "cancelcall"):
                self._on_incoming_call_ended(event, act)
            elif act == "answer":
                # Peer answered OUR outgoing call (sync path):
                # mark answered, source "sync" — _mark_answered
                # sends the caller's answerack (11304) itself.
                # NHUNG: Zalo cung gui act "answer" khi ben kia TU
                # CHOI — phan biet bang status (0 = nghe that,
                # 3 = tu choi; log 19/11: 2 cuoc Ba nghe that co
                # status 0 + rtp_rx > 0, 2 cuoc Ba tu choi co
                # status 3 + rtp_rx = 0). Status != 0 thi ket
                # thuc cuoc goi nhu peer_declined, KHONG danh dau
                # answered (neu khong cuoc goi ket dinh o
                # "answered" mai, automation tuong Ba dang nghe).
                _st = None
                _d = (event or {}).get("data")
                if isinstance(_d, dict):
                    _st = _d.get("status")
                    if _st is None:
                        _p = _d.get("params")
                        if isinstance(_p, dict):
                            _st = _p.get("status")
                try:
                    _st = int(str(_st).strip())
                except (TypeError, ValueError):
                    _st = 0
                if _st != 0:
                    self._on_incoming_call_ended(event, "cancel")
                    srv()._log_call("call_event answer call_id=%s "
                              "rejected status=%s" % (call_id, _st))
                else:
                    entry = srv()._mark_answered(call_id, "sync")
                    if entry is not None:
                        srv()._log_call("call_event answer call_id=%s "
                                  "answered" % call_id)
            elif act in ("audiotovideo", "requestaudio2video"):
                self._on_video_request(event)
            elif act == "onoffcamera":
                # «Xin bat camera» cua ben kia = xin chuyen cuoc thoai sang VIDEO (Zalo PC: ZaloCall.exe nhan ra `onoff_camera`). KHONG nhan act "1":
                # 12/11 18:53 act "1" den dung luc bam LOA NGOAI tren dien thoai, chua xac nhan la nut camera.
                self._on_camera_toggle(event)
            elif act in ("ringring", "answerack"):
                srv()._log_call("call_event %s call_id=%s" % (act, call_id))
            elif act == "grouprequest":
                self._on_group_call_request(event)          # chưa nghe được nhưng PHẢI báo ra WebUI/HA (trước đây bỏ qua im lặng)
            elif act in ("groupcancel", "groupcancelcall", "groupend", "groupendcall"):
                self._on_group_call_ended(event, act)
            else:
                srv()._log_call("call_event act=%s call_id=%s ignored"
                          % (act_raw, call_id))
        except Exception:
            pass
