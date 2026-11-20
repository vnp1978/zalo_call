"""Danh tính kiểu PC: khai zpw_type 24 / zpw_ver 697 và User-Agent «ZaloPC-win32-24v697» như Zalo PC 26.11.12 thật. Nguồn: main.js của Zalo PC, module getClientType()=24 (win32) và
getClientVersion(): 695 ở 26.11.10, 697 ở 26.11.12 (đọc từ bộ cài 18/11/2026). Trước đó app khai 693 theo nhật ký mạng 13/11 (bản PC cũ hơn).
Chỉ dữ liệu của kiểu này — sửa kiểu pc ở ĐÂY, không đụng kiểu web."""

NAME = "pc"
TYPE = 24
VERSION = 697
USER_AGENT = "ZaloPC-win32-%dv%d" % (TYPE, VERSION)
