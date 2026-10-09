"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl

CÂU HỎI PHẢI TRẢ LỜI TRƯỚC KHI VIẾT (ghi câu trả lời vào reports/postmortem.md):
  interval=5s, threshold=3 -> sớm nhất bạn có thể phát hiện outage là bao nhiêu giây?
  Con số đó nằm TRONG RTO của bạn. Muốn RTO 5 phút thì được phép chọn interval bao nhiêu?
"""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Return whether a region can serve traffic, with a useful failure reason."""
    try:
        response = httpx.get(f"{URL[region]}/readyz", timeout=timeout)
    except httpx.HTTPError as exc:
        return False, type(exc).__name__

    if response.status_code == 200:
        return True, "ready"

    try:
        detail = response.json().get("reasons")
    except (ValueError, AttributeError):
        detail = None
    return False, f"http_{response.status_code}" + (f":{detail}" if detail else "")


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """Poll both regions and emit JSONL only when their health state changes."""
    if interval <= 0:
        raise ValueError("interval must be positive")
    if threshold <= 0:
        raise ValueError("threshold must be positive")

    out.parent.mkdir(parents=True, exist_ok=True)
    states = {region: "HEALTHY" for region in URL}
    consecutive_fails = {region: 0 for region in URL}
    deadline = time.time() + duration

    def emit(region: str, to: str, reason: str):
        record = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "event": "state_change",
            "region": region,
            "to": to,
            "reason": reason,
            "consecutive_fails": consecutive_fails[region],
            "interval_s": interval,
            "threshold": threshold,
        }
        log.write(json.dumps(record) + "\n")
        log.flush()

    with out.open("a") as log:
        while time.time() < deadline:
            poll_started = time.time()
            for region in URL:
                ready, reason = probe(region, timeout)
                if ready:
                    consecutive_fails[region] = 0
                    if states[region] == "UNHEALTHY":
                        states[region] = "HEALTHY"
                        emit(region, "HEALTHY", reason)
                else:
                    consecutive_fails[region] += 1
                    if (states[region] == "HEALTHY"
                            and consecutive_fails[region] >= threshold):
                        states[region] = "UNHEALTHY"
                        emit(region, "UNHEALTHY", reason)

            time.sleep(max(0.0, interval - (time.time() - poll_started)))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))
