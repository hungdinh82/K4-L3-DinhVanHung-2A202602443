# RTO/RPO Evidence — Lab 23

Các số dưới đây được lấy từ drill thực tế; mỗi evidence trỏ đến đúng file và dòng log.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | 2026-10-09T05:11:29 | chaos kill Region A | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | +0.4s | request `ok:false` đầu tiên sau outage | `reports/drill-1-nodr.jsonl:45` |
| Request thành công sau đó | Không có | các request còn lại đều timeout | `reports/drill-1-nodr.jsonl:53` |
| RTO | NO_RECOVERY | không có request phục hồi trong cửa sổ loadgen | `reports/drill-1-nodr.jsonl:45` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---:|---|---|
| t_outage (mốc 0) | 0s | `action:kill` Region A | `chaos/chaos-events.jsonl:3` |
| User thấy lỗi đầu tiên | +0.1s | request `ok:false` đầu tiên | `reports/drill-2-withdr.jsonl:71` |
| Health check phát hiện | +14.3s | Region A đổi sang `UNHEALTHY` | `reports/health-events.jsonl:2` |
| Snapshot restore xong | +18.6s | `2_restore_snapshot`, RPO 4.01s / 2 docs | `reports/failover-events.jsonl:10` |
| Region phụ ready | +24.8s | `4_wait_ready` hoàn tất | `reports/failover-events.jsonl:12` |
| DNS cutover | +24.8s | `5_dns_cutover` sang Region B | `reports/failover-events.jsonl:13` |
| **RTO đo được** | **+30.5s** | request `ok:true` đầu tiên từ Region B | `reports/drill-2-withdr.jsonl:86` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---:|---:|---|
| RTO — Inference API | 30.5s | 300s | PASS |
| RPO — Vector DB | 4.01s / 2 docs | 300s | PASS |

## 3. RTO của tôi gồm những gì

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---:|---|---|
| Health-check detection | 14.3s | Region A có 3 lỗi liên tiếp; cấu hình 5.0s × 3 | giảm interval có kiểm soát, vẫn giữ threshold |
| Snapshot restore + xác nhận runbook | 4.3s | từ detect +14.3s đến restore +18.6s | tự động hoá alert-to-runbook, tối ưu copy snapshot |
| GPU pool warm-up | 6.2s | `waited_s: 6.21` của readiness | warm standby hoặc pre-warm pool |
| DNS/LB TTL cache | 5.7s | từ cutover +24.8s đến success +30.5s | TTL thấp hơn hoặc global LB nhanh hơn |
| **Tổng RTO** | **30.5s** | 14.3 + 4.3 + 6.2 + 5.7 | khớp RTO đo từ loadgen |
