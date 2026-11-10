# Industrial Cinematic APS — báo cáo giao diện

Ngày 09/11/2026, giờ Việt Nam.

## Phạm vi thực hiện

- Lưu ba ảnh nền WebP trong `src/assets/industrial/`, dùng trực tiếp trong ứng dụng: hero Dashboard, nền sidebar và nền workspace/sign-in. Tổng dung lượng khoảng 698 KB.
- Áp dụng design tokens navy/steel, cam công nghiệp, vàng KPI; panel tối, viền sáng nhẹ, biểu đồ và bảng có độ tương phản phù hợp.
- Dashboard có hero, New Scheduling Run và View Recent Results hoạt động; giữ nguồn KPI hiện có. Thanh tiến độ thực thi chỉ xuất hiện khi API có số completed/scheduled hợp lệ, ghi rõ phạm vi các lịch đã Apply.
- Scheduling Studio bố trí các panel Jobs, Horizon/Objective, Validation và Scheduling Method theo grid desktop. Duy trì bước kiểm tra và cấu hình solver hiện có; không chuyển mục tiêu tardiness thành lựa chọn tối ưu.
- Results/Gantt sử dụng hàng tối xen kẽ, grid/shift markers rõ, tooltip tối, viền chọn cam và highlight các thanh cùng Job khi hover. Giữ nguyên cách ánh xạ thời gian, grouping, clipping, viewport, calendar shading, filters, zoom, Fit Schedule và các action export/accept/apply.
- Sidebar trở thành drawer ở màn hình dưới 1024px; có đóng bằng Escape, giới hạn Tab trong menu khi mở và trả focus về nút mở. Background parallax/spotlight không cập nhật React state theo từng lần di chuột; tôn trọng reduced motion.

## Ảnh và mức độ bám tham chiếu

Ba ảnh là **ảnh tái tạo bằng imagegen theo các ảnh nhà máy người dùng gửi**, không phải bản sao nhị phân của ảnh gốc. File gốc đính kèm trong chat chưa có đường dẫn filesystem có thể truy xuất. Không dùng ảnh screenshot UI làm nền hay dựng biểu đồ giả.

Xem [nguồn ảnh và mô tả prompt](src/assets/industrial/ASSET_PROVENANCE.md). Các PNG đầu ra được chuyển sang WebP quality 0.86, giữ nguyên kích thước/bố cục, và lưu vào project.

Lượt này có ba ảnh nhà máy độc lập. Hai screenshot UI đầy đủ mang tên `01_Production_Dashboard.png` / `02_Scheduling_Studio_and_Gantt.png` được nhắc trong prompt không có file tham chiếu tương ứng để đối chiếu pixel. Giao diện được thực hiện theo cấu trúc/palette/quy tắc trong prompt; không tuyên bố khớp từng pixel với hai screenshot chưa có đó.

## File giao diện

Tạo mới:

- `src/industrial.css`: tokens, theme dùng chung, grid, responsive và reduced motion.
- `src/components/IndustrialBackdrop.tsx`: background parallax và card spotlight.
- `src/assets/industrial/factory-hero.webp`.
- `src/assets/industrial/factory-panorama.webp`.
- `src/assets/industrial/factory-studio.webp`.
- `src/assets/industrial/ASSET_PROVENANCE.md`.
- `tests/production/industrial-ui.spec.ts`: hai test điều hướng/responsive và Gantt lớn.
- Báo cáo này; scripts/logs/ảnh QA trong `.tmp-qa/cinematic-20261109/`.

Chỉnh sửa:

- `src/App.tsx`: tích hợp theme/background/menu và action mở Results từ Dashboard.
- `src/components/Sidebar.tsx`: nhận trạng thái drawer, hỗ trợ focus/keyboard, branding.
- `src/pages/DashboardPage.tsx`: hero, thanh utilization cam và progress từ số thực thi của API.
- `src/pages/LoginPage.tsx`: sử dụng background/theme dùng chung; giữ nguyên đăng nhập.
- `src/pages/AnalyticsPage.tsx`: class trình bày màu Makespan Trend.
- `src/components/GanttChart.tsx`: màu hàng qua CSS variables và trạng thái hover trình bày; không đổi dữ liệu/calculations.
- `tests/production/analytics-execution.spec.ts`: cập nhật assertion từ UUID sang nhãn lịch thân thiện đã có; mở drawer trước điều hướng trên viewport nhỏ. Vẫn kiểm tra ID gốc trong tooltip và điều hướng đúng ID.

## Bảo toàn

Hash baseline của 274 file được lưu trước chỉnh sửa. [Integrity](.tmp-qa/cinematic-20261109/integrity.json) xác nhận không thay đổi file backend, solver, decoder, calendar, database/storage, data types, algorithm settings hoặc các helper `ganttCalendar.ts` / `ganttLayout.ts`.

Không thêm dependency. Không chạy benchmark dài hạn. Kiểm thử dùng database QA sao chép/riêng; không thay đổi lịch sản xuất đã lưu. Cảnh báo lịch lịch sử vẫn hiển thị theo dữ liệu gốc, không bị xóa để làm UI đẹp hơn.

## Chạy ứng dụng

Giữ các lệnh hiện có trong README:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
.\run-frontend.cmd
```

Frontend: `http://localhost:8443`. Đăng nhập demo hiện có: `planner@factory.com` / `planner-demo`.

## Kết quả kiểm thử thực tế

| Kiểm tra | Kết quả | Bằng chứng |
|---|---|---|
| Frontend production suite cuối | **PASS 52/52**, 2,2 phút | [JSON cuối](.tmp-qa/cinematic-20261109/final-results.json) |
| Backend đầy đủ | **PASS 272/272**, 88,52 giây | [JUnit](.tmp-qa/cinematic-20261109/backend.xml) |
| E2E workflow hiện có | **PASS 1/1**, 15 giây cả harness | [JSON](.tmp-qa/cinematic-20261109/e2e-results.json) |
| TypeScript | **PASS** | `run-frontend.cmd check` |
| Production build | **PASS** | `run-frontend.cmd build`; cùng JS/CSS/ảnh với build QA cuối |
| Gantt lớn | **PASS** | Suite kiểm tra 11.290 / 22.580 events và 20.000 events tổng hợp, giữ identity/time/processing và table/export |
| Responsive / keyboard / reduced motion | **PASS** | Test mới và regression Analytics; viewport nhỏ, drawer, focus, Escape, selection/date không mất |
| Bảo toàn backend/data | **PASS** | `protected_changed: []` trong integrity |

Hai test UI mới nằm trong tổng 52, không cộng các lượt chạy lặp. Lượt đầy đủ đầu có 50 PASS / 2 FAIL do assertion nhãn cũ và thao tác menu 768px. Sau cập nhật test theo hành vi UI hiện hành và chỉnh bố cục desktop, chạy lại toàn bộ suite sạch: 52 PASS, không skip. [Kết quả lượt đầu](.tmp-qa/cinematic-20261109/first-results.json) được giữ để đối chiếu.

Backend có một cảnh báo Starlette/httpx deprecation hiện có. Không chỉnh dependency trong lượt giao diện này.

E2E dùng database QA mới, thực hiện workflow legacy GA/MLPSO. Production suite có luồng V5 nhỏ với CP-SAT/GA, validation, export JSON/Excel, Accept/Apply và mở lại lịch. Đây là regression giao diện/tích hợp, không phải benchmark chất lượng nghiệm hoặc chạy endurance dài hạn.

Lệnh kiểm thử dùng cấu hình riêng trong `.tmp-qa/cinematic-20261109/`; server QA chạy ở 8029/8030, không dùng database sản xuất.

## Ảnh chụp đã xem để kiểm tra trực quan

- [Dashboard 1648×928](.tmp-qa/cinematic-20261109/browser-final/industrial-ui-Industrial-d-f80e6-keyboard-and-reduced-motion/dashboard-1648.png).
- [Dashboard 1440×900](.tmp-qa/cinematic-20261109/browser-final/industrial-ui-Industrial-d-f80e6-keyboard-and-reduced-motion/dashboard-1440.png).
- [Scheduling Studio 1648×928](.tmp-qa/cinematic-20261109/browser-final/industrial-ui-Industrial-d-f80e6-keyboard-and-reduced-motion/studio-1648.png).
- [Results/Gantt 1648×928](.tmp-qa/cinematic-20261109/browser-final/industrial-ui-Industrial-r-36acc-vive-themed-layout-and-zoom/results-1648.png).
- [Studio 980px](.tmp-qa/cinematic-20261109/visual-final/industrial-ui-Industrial-d-f80e6-keyboard-and-reduced-motion/studio-980.png), [Studio 390px](.tmp-qa/cinematic-20261109/visual-final/industrial-ui-Industrial-d-f80e6-keyboard-and-reduced-motion/studio-390.png).

Ảnh tablet được chụp lại sau khi chờ drawer đóng hoàn toàn để tránh bắt trúng frame chuyển động. Lượt kiểm tra trực quan cuối **2/2 PASS**, ghi riêng trong [visual-results.json](.tmp-qa/cinematic-20261109/visual-results.json); không cộng lặp vào 52 cases của suite.

Ảnh chụp sử dụng fixture/database QA. Fixture Studio nhỏ có horizon một ngày; đây không phải thay đổi horizon của App. Regression calendar/horizon 60 ngày và dữ liệu Gantt lớn được kiểm tra riêng trong các suite. Ảnh Results lớn vẫn hiển thị cảnh báo lịch lịch sử và thiếu job context của fixture khi có; không che hoặc tự sửa những cảnh báo đó.

Giới hạn còn lại: chưa có hai screenshot UI đầy đủ để kiểm tra pixel; chưa kiểm tra mọi thiết bị/browser vật lý hoặc hiệu năng GPU trên máy cấu hình thấp. Lượt này không xử lý các vấn đề nghiên cứu/calendar trong pilot benchmark cũ.
