# APS frontend redesign — kết quả bàn giao

Ngày hoàn tất: **09/11/2026, Asia/Saigon**. Tiếp tục phần triển khai ngày 08/11; không khởi tạo lại App.

## Thay đổi thực tế

- Shell **Smart Production APS**: sidebar navy, navigation Input Data, Scheduling Studio, Schedule Results, Production Execution, Analytics & Reports và Settings; breadcrumb, layout responsive và trạng thái focus.
- Dashboard: bốn KPI chính, utilization theo kế hoạch đã accepted, lịch sử makespan, Job Status và bảng recent runs. Giữ phân biệt dataset hiện tại, accepted và applied. Thiếu metric hiển thị dấu gạch; không tạo giá trị giả.
- Scheduling Studio: hướng dẫn bốn bước, Job search/filter/sort, chọn toàn bộ danh sách đã lọc, clear selection và phân trang 20 Jobs. Planning Horizon và objective cố định được trình bày riêng. Validation, preview thuật toán và submission vẫn dùng API hiện có.
- Schedule Results: trang tìm lịch theo Run ID/algorithm/dataset, lọc trạng thái, phân trang, mở lại kết quả. Không phụ thuộc vào năm run gần nhất trên Analytics.
- Result header: bốn KPI, actions giữ nguyên, phần giải thích/KPI phụ trong Show Details. Summary đạt giới hạn 200px ở desktop trong test.
- Gantt: giữ 1/3/7 ngày, Fit Schedule, zoom, fullscreen, compact/Fit Height, calendar review và tooltips. Thêm nhóm theo Job và lọc resource thực tế từ assignments. Batch chung xuất hiện ở từng Job thành viên nhưng không nhân bản event trong dữ liệu hoặc export. Job view không vẽ downtime của một resource thành downtime chung; shading resource nằm ở Workstation view.
- Schedule Table: 50 dòng/trang, search, Job/event filter, sắp xếp, resource/event ID/product, CSV toàn bộ dòng đã lọc. CSV gọi guard export hiện có trước download. Bấm Job chuyển về Gantt và định vị Job.
- Comparison: thêm lower bound và solver gap từ metadata được lưu; thiếu dữ liệu để trống bằng dấu gạch. Không suy luận proven optimal chỉ từ status OPTIMAL.

## Files

Tạo mới:

- `src/aps.css`
- `src/components/aps.tsx`
- `src/components/RunHistory.tsx`
- `src/components/OperationTable.tsx`
- `src/pages/ResultsPage.tsx`
- `tests/production/aps-redesign.spec.ts`
- `tests/production/aps-comparison.spec.ts`
- `tests/production/aps-helpers.ts`
- Báo cáo này.

Chỉnh sửa trong đợt redesign:

- `src/App.tsx`, `src/components/Sidebar.tsx`, `src/components/InputDataPanel.tsx`
- `src/pages/DashboardPage.tsx`, `src/pages/ScheduleFlow.tsx`
- `src/pages/AnalyticsPage.tsx`: export component trend hiện có để Dashboard dùng cùng cách thể hiện.
- `src/components/GanttChart.tsx`, `src/components/ganttLayout.ts`: grouping/filter/navigation trình bày.
- Các test production hiện có và `tests/e2e/workflow.spec.ts`: cập nhật navigation, checkbox, bảng phân trang; mở fixture lịch qua trang Results. Screenshot chuyển sang output riêng của test, không ghi đè ảnh QA lịch sử.
- Production build trong `dist/`.

Danh sách hash thay đổi chi tiết: [.tmp-qa/aps-redesign-20261108/integrity.json](.tmp-qa/aps-redesign-20261108/integrity.json).

## Kết quả kiểm thử đã chạy

| Kiểm tra | Kết quả |
|---|---|
| TypeScript, bản cuối | **PASS** — `tsc --noEmit` |
| Production build, bản cuối | **PASS** — Vite; build riêng QA và build `dist/` |
| Frontend production suite | **PASS 46/46**, khoảng 2 phút |
| Regression bổ sung comparison/bound/gap sau thay đổi cuối | **PASS 1/1** |
| Legacy E2E với database QA trống | **PASS 1/1**, 11,9 giây |
| Upload → V5 CP-SAT → Excel → Accept/Apply → Execution → Reopen | **PASS**, trong production suite |
| SS Micro-GA Fast qua API thực, budget nhỏ và Excel | **PASS**, trong production suite |
| Legacy automatic GA 10 Jobs và MLPSO 50 Jobs | **PASS**, trong E2E |
| Preset 60/120/300/600/900 đến API/payload | **PASS**; không chạy solver đủ các budget dài này |
| Gantt 11.290 / 22.580 events, fixture 20.000 events | **PASS** |
| Resource shading, cross-midnight, Sunday, shift, zoom, filters, compact/fullscreen | **PASS** |
| Persisted reopening, frozen snapshot không đổi | **PASS** |
| Responsive Studio 390/768/1440/1920px và kiểm tra ảnh Dashboard/Studio/Results desktop | **PASS trong phạm vi test** |
| Backend/source solver/calendar/storage so với baseline | **PASS — 0 file được bảo vệ thay đổi** |
| Full backend unit suite | **KHÔNG CHẠY LẠI**; không sửa backend. Các integration tests trên dùng API thật và dữ liệu QA riêng |
| Lint | **KHÔNG CẤU HÌNH** trong package.json; không coi typecheck là lint |

Tổng: **47 production cases + 1 E2E PASS**, không cộng các lần chạy lặp.

Các lỗi trong lượt chạy đầu đã được ghi nhận và xử lý: summary cao hơn 200px; test screenshot rewrite nhầm chuỗi đường dẫn trong assertion; test tìm lịch cũ trong năm run gần nhất; legacy E2E giả định database trống. Lượt production hoàn chỉnh sau xử lý đạt 46/46. E2E được chạy lại trong database QA trống đúng contract và PASS. Không thay thuật toán để làm test đạt.

Evidence:

- [Production suite JSON](.tmp-qa/aps-redesign-20261108/.tmp-qa/aps-redesign-20261108/browser-results.json)
- [Comparison regression](.tmp-qa/aps-redesign-20261108/comparison-results.json)
- [Legacy E2E](.tmp-qa/aps-redesign-20261108/e2e-results.json)
- [Ảnh và traces](.tmp-qa/aps-redesign-20261108/browser-output/)
- [Bảo toàn file](.tmp-qa/aps-redesign-20261108/integrity.json)

## Chạy ứng dụng

Tại thư mục project, hai terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
.\run-frontend.cmd
```

Mở `http://localhost:8443`. Đăng nhập demo: `planner@factory.com / planner-demo`.

Production cùng origin:

```powershell
.\run-frontend.cmd build
.\run-production.cmd
```

Mở `http://127.0.0.1:8000`. Build mới đã có trong `dist/`; refresh trình duyệt để tải assets mới.

## Khác biệt có chủ ý và giới hạn

- Không sao chép số liệu mẫu, ảnh nhà máy hoặc “Guaranteed optimality” từ hình tham khảo.
- Không thêm template/configuration persistence, solver cancellation, notification hoặc profile giả khi backend không cung cấp.
- Không bật MLPSO cho V5; lựa chọn không hỗ trợ tiếp tục disabled. Các solver legacy giữ nguyên.
- Không cho chọn tardiness làm objective. Không sửa mô hình vật lý, decoder, horizon hay availability.
- Không gắn nhãn PASS riêng từng nhóm validation nếu API chưa cung cấp kết quả từng nhóm.
- Không tính benchmark gap giữa các historical run khác input/calendar/config. Solver gap hiển thị chỉ từ metadata đã lưu, nhân 100 để chuyển ratio sang phần trăm.
- Gantt, table và export giữ cùng nguồn standardized operations. Không rerun solver khi export.
- Calendar conflicts trong lịch cũ vẫn được báo trung thực. Redesign không sửa các vấn đề calendar/benchmark của pilot trước.
- Kiểm thử này xác nhận tích hợp và giao diện trong các case nêu trên; không thay thế benchmark chất lượng nghiệm hoặc endurance production.
