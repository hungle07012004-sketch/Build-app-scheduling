# Due Date Sensitivity — TEST ONLY

Không import production services, không chạy solver, không kết nối ghi vào database nguồn. Không đổi UI, settings hay dữ liệu App. Script chỉ dùng Python standard library; không cài dependency.

Từ project root:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s experiments/due_date_sensitivity -p test_analysis.py -v
.\.venv\Scripts\python.exe -B experiments/due_date_sensitivity/analyze.py --database backend/storage/scheduling.sqlite3 --output experiments/due_date_sensitivity/run-NEW
```

`--output` phải là thư mục con mới chưa tồn tại. Script từ chối ghi đè kết quả. Nếu database có WAL chưa checkpoint hoặc thay đổi lúc copy, script dừng; không tự checkpoint hay dừng App. Snapshot được copy byte vào thư mục experiment trước khi đọc SQLite immutable. Nếu baseline nào không khớp, không xuất kết luận scenario cho toàn bộ lượt thử.

Kết quả thực tế ngày 10/11/2026: xem [báo cáo đầy đủ](run-20261110-01/due_date_sensitivity_report.md), [KPI từng scenario](run-20261110-01/due_date_sensitivity_results.csv), [từng job](run-20261110-01/job_level_comparison.csv), [baseline validation](run-20261110-01/baseline_validation.csv) và [manifest/hashes](run-20261110-01/manifest.json).

## Kết luận từ lượt thử

51 lịch khả thi, gồm 36 SS Micro-GA Fast, 6 CP-SAT, 4 legacy MILP, 3 MLPSO (gồm legacy và V3), 2 Improved GA. Không gộp chúng thành một benchmark công bằng; có các run lặp và input khác nhau. 4 mục không có lịch khả thi không được gán KPI bằng 0.

| Run (đầu ID) | Thuật toán / jobs | Trễ gốc | +1 ngày | +3 ngày | +5 ngày | +7 ngày |
|---|---|---:|---:|---:|---:|---:|
| ae171b2d | MLPSO V3 / 12 | 3 | 0 | 0 | 0 | 0 |
| 182741f9 | SS Micro-GA Fast / 20 | 5 | 2 | 1 | 0 | 0 |
| 3c86a353 | SS Micro-GA Fast / 20 | 5 | 2 | 1 | 0 | 0 |
| 236e9e6f | SS Micro-GA Fast / 166 | 130 | 121 | 103 | 91 | 86 |

- MLPSO 12 jobs: +1 ngày đã đạt OTD 100%; các mốc dài hơn không có lợi thêm về OTD trên lịch này.
- GA 20 jobs: +1 ngày tăng OTD 75% → 90%; +3 ngày đạt 95%, còn 232,7 phút tổng trễ ở 1 job; +5 ngày đạt 100%. Trong các mốc thử, +5 là ngắn nhất hết trễ, nhưng phải cân nhắc việc kéo dài tất cả jobs chỉ để xử lý job còn lại. +7 không thêm lợi ích sau +5 trên lịch này.
- GA 166 jobs: +3 ngày là bước giảm số jobs trễ lớn nhất giữa hai mốc liên tiếp (121 → 103, giảm 18 jobs cho thêm 2 ngày). +7 ngày cho cải thiện tổng lớn nhất so baseline (giảm 44 jobs), nhưng OTD chỉ 48,19%, còn 86 jobs trễ. Không nên đề xuất +7 ngày như giải pháp đủ cho lịch này.
- Số lịch có OTD 100% là 13/51 ở baseline, 20/51 ở +1, 29/51 ở +3, 35/51 ở +5 và 37/51 ở +7. Đây là số lịch lịch sử, không phải xác suất thành công hay số đơn hàng độc lập.
- Tất cả 255 scenario rows có Cmax giữ nguyên. Mọi completion/release/calendar/processing đều giữ nguyên. 16.890 job-scenario rows ghi rõ jobs chuyển từ trễ sang đúng hạn.
- 5/5 test tính toán/mapping PASS; baseline 51/51 PASS; production source (110 files), input/Excel và database nguồn giữ nguyên hash trong lượt phân tích. Không chạy lại backend suites hoặc solver vì không sửa production.

**Đề xuất:** thảo luận +1 ngày cho lịch 12 jobs; dùng +3 ngày như mốc sensitivity hợp lý cho lịch 20 jobs nếu chấp nhận OTD 95%, hoặc +5 nếu chỉ xét các mốc thử và cần OTD 100%. Với 166 jobs, cần kiểm tra công suất/due-date policy và chất lượng lịch; gia hạn đồng đều +7 ngày vẫn chưa đủ. Đây không phải phê duyệt thay đổi hạn giao.

Chế độ Due Date Extension trên lịch cố định không cần sửa solver. Nếu muốn re-optimize thì cần phân tích riêng: due date được dùng ở EDD seeds, và legacy Improved GA còn dùng tardiness trong objective. Hiệu quả KPI do gia hạn không chứng minh thuật toán tốt hơn. Các file đề xuất triển khai có trong mục E của báo cáo; chưa triển khai, chờ người dùng phê duyệt.
