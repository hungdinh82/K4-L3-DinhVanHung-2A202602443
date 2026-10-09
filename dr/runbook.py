"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr.health_checker import probe  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Append one timestamped runbook step and return the recorded event."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "step": n,
        "name": name,
        **kw,
    }
    with LOG.open("a") as log:
        log.write(json.dumps(record) + "\n")
    print(json.dumps(record))
    return record


def confirm(auto: bool, msg: str) -> bool:
    """Require an explicit operator confirmation outside CI/grading mode."""
    if auto:
        return True
    return input(f"{msg} [y/N]: ").strip().lower() == "y"


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Execute the seven-step, operator-confirmed incident response runbook."""
    started = time.time()

    # 1. Confirm a primary outage across several independent probes.
    primary_probes = [probe(primary, 1.0) for _ in range(3)]
    target_probes = [probe(target, 1.0) for _ in range(3)]
    outage_confirmed = all(not ready for ready, _ in primary_probes)
    step(1, "xac_nhan_outage", primary=primary, target=target,
         primary_probes=primary_probes, target_probes=target_probes,
         outage_confirmed=outage_confirmed)
    if not outage_confirmed:
        step(7, "post_incident", ok=False, elapsed_s=round(time.time() - started, 2),
             reason="primary_not_confirmed_down")
        return {"ok": False, "reason": "primary_not_confirmed_down"}

    # 2. The log timestamp is the point the human operator is informed.
    approved = confirm(auto, f"Region {primary} is down. Fail over to {target}?")
    step(2, "thong_bao_incident", primary=primary, target=target, auto=auto,
         operator_confirmed=approved)
    if not approved:
        step(7, "post_incident", ok=False, elapsed_s=round(time.time() - started, 2),
             reason="operator_declined_failover")
        return {"ok": False, "reason": "operator_declined_failover"}

    # 3. This is intentionally the only failover invocation in the runbook.
    result = fo.failover(target, backend, wait=60)
    step(3, "scale_gpu_pool", ok=result.get("ok", False), failover=result)

    # 4-5. Read the single failover result; never invoke failover again.
    replica = {
        "rpo_seconds": result.get("rpo_seconds"),
        "docs_lost": result.get("docs_lost"),
        "embed_model_version": result.get("embed_model_version"),
        "target_state": result.get("target_state"),
    }
    step(4, "verify_state_replica", ok=result.get("ok", False), **replica)
    active = pathlib.Path("edge/active_region")
    cutover_ok = result.get("ok", False) and active.exists() and active.read_text().strip() == target
    step(5, "dns_cutover", ok=cutover_ok, active_region=active.read_text().strip()
         if active.exists() else None)

    # 6. Measure the golden signals against the promoted region, not an edge cache.
    samples = []
    if cutover_ok:
        for _ in range(10):
            request_started = time.time()
            try:
                response = httpx.get(f"{URL[target]}/v1/infer", timeout=3.0)
                ok = response.status_code == 200
            except httpx.HTTPError:
                ok = False
            samples.append({"ok": ok, "latency_ms": round((time.time() - request_started) * 1000, 1)})
        latencies = sorted(sample["latency_ms"] for sample in samples)
        p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]
        errors = sum(not sample["ok"] for sample in samples)
        golden = {"requests": len(samples), "p95_latency_ms": p95,
                  "error_rate": round(errors / len(samples), 3)}
    else:
        golden = {"requests": 0, "p95_latency_ms": None, "error_rate": None}
    step(6, "verify_golden_signals", ok=cutover_ok and golden["error_rate"] == 0,
         **golden)

    # 7. Leave the operator an exact command for the evidence measurement.
    overall_ok = cutover_ok and golden["error_rate"] == 0
    step(7, "post_incident", ok=overall_ok,
         elapsed_s=round(time.time() - started, 2),
         measure_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl")
    return {"ok": overall_ok, "failover": result, "replica": replica,
            "golden_signals": golden}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
