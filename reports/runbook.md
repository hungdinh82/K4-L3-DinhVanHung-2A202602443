# Runbook — Region chính down

Áp dụng cho Region A là primary và Region B là standby. Chạy mọi lệnh tại thư mục gốc của lab. On-call không tự failover khi chưa có xác nhận incident; Incident Commander (IC) là người có quyền phê duyệt cutover và rollback.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python3 chaos/kill_region.py status` | Region A không `alive`/`ready` qua nhiều probe, Region B vẫn alive | on-call |
| 2 | Mở incident và bấm giờ | `python3 dr/runbook.py --primary a --target b --backend fs` | prompt hiển thị xác nhận và `reports/runbook-run.jsonl` có `thong_bao_incident` | on-call + IC |
| 3 | Restore state ở Region B | `python3 state/snapshot.py get --region b --backend fs` | output có manifest và weights/model.bin được tạo; RPO được ghi vào failover log | DR engineer |
| 4 | Scale GPU pool và đợi ready | `python3 dr/failover.py --target b --backend fs` | `reports/failover-events.jsonl` có `4_wait_ready` với `ok:true`; `curl -i localhost:8002/readyz` trả 200 | platform engineer |
| 5 | DNS/LB cutover | `python3 dr/failover.py --target b --backend fs` | `5_dns_cutover` có `ok:true` và `curl localhost:8080/edge/state` trả `active_region:b` | IC |
| 6 | Verify golden signals | `python3 dr/runbook.py --primary a --target b --backend fs --auto` | `verify_golden_signals` ghi 10 request, error rate 0 và p95 latency trong `reports/runbook-run.jsonl` | on-call |
| 7 | Đo RTO và ghi postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | JSON có `valid:true`, `rto_verdict:PASS`; điền evidence và postmortem | incident scribe |

Trong incident thật, dùng một lệnh runbook duy nhất sau khi IC duyệt để tránh gọi failover nhiều lần:

```bash
python3 dr/runbook.py --primary a --target b --backend fs
```

`--auto` chỉ dùng cho CI hoặc drill đã được duyệt.

## Rollback về Region A

Chỉ IC được quyền rollback. Điều kiện: Region A đã được khôi phục, `/readyz` trả 200 ổn định trong ít nhất 5 phút, state đã được đồng bộ/đánh giá không mất dữ liệu, và golden signals ở A đạt ngưỡng bình thường. Không rollback nếu B đang phục vụ ổn định nhưng replication chưa được xác minh.

Sau khi IC phê duyệt: khôi phục A bằng `python3 chaos/kill_region.py restore --region a --backend bare`, xác nhận readiness, đồng bộ state theo quy trình replication, rồi thực hiện failover có kiểm soát với target `a`. Quan sát edge và 10 request golden signals trước khi đóng incident.
