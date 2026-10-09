# Postmortem — DR Drill Lab 23

Đây là postmortem blameless cho game day: mục tiêu là cải thiện hệ thống và quy trình, không quy trách nhiệm cá nhân.

## 1. Timeline

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-10-09T05:19:18 | Outage Region A bắt đầu | `chaos/chaos-events.jsonl:3` |
| 2026-10-09T05:19:18 | User đầu tiên bị ảnh hưởng, edge timeout Region A | `reports/drill-2-withdr.jsonl:71` |
| 2026-10-09T05:19:32 | Health checker đánh dấu Region A UNHEALTHY | `reports/health-events.jsonl:2` |
| 2026-10-09T05:19:36 | Operator xác nhận incident và cho phép failover | `reports/runbook-run.jsonl:2` |
| 2026-10-09T05:19:43 | DNS cutover sang Region B hoàn tất | `reports/failover-events.jsonl:13` |
| 2026-10-09T05:19:48 | Request đầu tiên phục hồi từ Region B | `reports/drill-2-withdr.jsonl:86` |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s; đo được: 30.5s; gap: -269.5s, tức thấp hơn mục tiêu 269.5s.
- RPO mục tiêu: 300s; đo được: 4.01s (2 documents bị mất); gap: -295.99s, tức thấp hơn mục tiêu 295.99s.
- Bước tốn nhiều giây nhất: health-check detection, 14.3s. Đây là phần lớn nhất trong RTO vì cần 3 lỗi liên tiếp để chống flapping.

## 3. Root cause — 5 whys

1. Vì sao user thấy lỗi? Region A bị network block và edge vẫn route đến A cho tới khi cutover.
2. Vì sao edge chưa route sang B ngay? B không được xem là serving target trước khi restore state và warm GPU pool.
3. Vì sao cần restore? B là standby rỗng trước incident, không có weights và vector DB dùng được.
4. Vì sao failover không được thực hiện sau lỗi đầu tiên? Threshold 3 lần fail liên tiếp được thiết kế để không biến transient network lag thành cutover hai chiều.
5. Vì sao RTO vẫn có 30.5s? Hệ thống phải trả chi phí detection, xác nhận runbook/restore, warm-up GPU và DNS TTL cache để đổi lấy failover an toàn, có evidence.

## 4. Action items

| # | Action item | Owner | Deadline | Tác động dự kiến |
|---|---|---|---|---|
| 1 | Đánh giá interval 3s với threshold 3 bằng drill lặp để giảm detection mà vẫn chống flapping | platform engineer | 2026-10-16 | giảm tối đa khoảng 6s detection floor |
| 2 | Thử warm standby pool cho Region B và ghi lại chi phí idle capacity | infrastructure lead | 2026-10-23 | giảm khoảng 6.2s GPU warm-up |
| 3 | So sánh TTL edge 2s với 5s trong chaos drill | network engineer | 2026-10-16 | giảm tối đa khoảng 3s cache delay |

## 5. Ba câu hỏi bắt buộc trả lời

1. `interval × threshold` là 5s × 3 = 15s. Detection quan sát được là 14.3s, chiếm khoảng 46.9% RTO 30.5s.
2. Nếu hạ interval xuống 1s và giữ threshold 3, detection floor lý thuyết là 3s; có thể giảm tối đa khoảng 12s, đổi lại là nhiều probe hơn và nguy cơ false positive/flapping cao hơn.
3. Nếu outage kéo dài 6 giờ và Region A mất dữ liệu vĩnh viễn, `docs_lost: 2` nghĩa là hai document được ingest sau snapshot gần nhất sẽ không có trong Region B sau restore; khách hàng không thể truy vấn hoặc nhận câu trả lời dựa trên chúng cho tới khi dữ liệu được khôi phục từ nguồn gốc.
