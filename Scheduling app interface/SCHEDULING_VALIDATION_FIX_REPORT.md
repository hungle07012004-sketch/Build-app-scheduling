# AL13 V5 — QA-01 đến QA-05: kết quả sửa và kiểm thử

Ngày: **25/10/2026, Asia/Saigon**.

**Cả 5 lỗi validation đã được sửa và có regression tests PASS.** Đây là báo cáo bổ sung cho [audit ban đầu](SCHEDULING_TEST_REPORT.md); không thay thế hoặc sửa kết quả audit lịch sử.

## 1. Xác nhận từng lỗi

| Lỗi | Thay đổi | Kết quả |
|---|---|---|
| QA-01 | Bắt buộc đủ và duy nhất 7 cạnh precedence cho mỗi job; kiểm tra job/operation, relation type và lag hữu hạn, không âm. Báo lỗi theo field/index và Job ID. | PASS |
| QA-02 | Chỉ chấp nhận cạnh thuộc tuyến V5 cố định; từ chối cạnh bổ sung, cạnh tự nối và cạnh ngược tạo chu trình. PRE→FINAL phải là `SS_FIRST_BELT`, các cạnh còn lại là `FS`. Kiểm tra lại mọi cạnh được chấp nhận khi xác nhận output. | PASS |
| QA-03 | Kiểm tra coverage ma trận setup theo sản phẩm; từ chối số âm, NaN/Infinity, boolean, chuỗi và giá trị thiếu. Cho phép setup bằng 0. | PASS |
| QA-04 | `batching.processing_min_per_batch` phải là số hữu hạn, lớn hơn 0. | PASS |
| QA-05 | Đối chiếu `Station_ID` / `Resource_Units` và `Resources` với mapping cho JOB_OP, SETUP, OIL_CYCLE và 2C_BATCH; bắt assignment sai, thiếu, trùng hoặc mâu thuẫn giữa các field. Giữ nguyên phân biệt OIL load/unload. | PASS |

Input sai bị chặn trước khi tạo dataset/run trong database hoặc đưa solver vào queue. Kiểm tra lại nguồn input trước khi chọn subset để tránh lọc mất cạnh có Job ID không hợp lệ.

Raw output sai được trả về `feasible=false`, không có danh sách operations để sử dụng như lịch hợp lệ. Accept, Excel và JSON export đều có kiểm tra lại dựa trên frozen snapshot; lỗi trả HTTP 409 trước khi ghi `accepted_at` hoặc tạo file tải xuống. Việc mở xem lịch cũ vẫn hoạt động; không ghi đè trạng thái hay sửa dữ liệu lịch sử.

JSON sử dụng endpoint result hiện có với query tùy chọn `validate_export=true`. Request result thông thường và response format giữ nguyên. Thay đổi frontend chỉ ở handler nút JSON để chờ validation và hiển thị lỗi qua cơ chế sẵn có; không đổi layout, Gantt hoặc navigation.

## 2. Kết quả kiểm thử thực tế

| Bộ kiểm tra | Kết quả | Chi tiết |
|---|---|---|
| Targeted backend, lượt đầu | PASS | 84/84, 32,12 giây |
| Toàn bộ backend trên bản cuối | PASS | **142/142**, 66,59 giây; gồm 70 test cũ và 72 regression cases mới; không skip |
| TypeScript | PASS | `tsc --noEmit` |
| Production build | PASS | Vite build vào thư mục QA riêng; không thay bản deploy |
| Frontend production suite | PASS sau sửa fixture | **34/34 test duy nhất**: 33 PASS trong lượt chạy đầy đủ, 1 test mock thiếu response validation JSON được cập nhật và chạy lại PASS; không skip |
| JSON export mới | PASS | Tải kết quả đã xác thực thành công; HTTP 409 không tạo download và không làm mất dữ liệu đang xem |
| Luồng V5 CP-SAT / SS Micro-GA | PASS | Các test backend/browser hiện có chạy solver nhỏ, accept/apply, Excel, mở lại lịch và snapshot |
| Hai lịch sản xuất 180 jobs gần nhất | PASS | Mỗi lịch 11.287 operations, input và saved-result guard đều hợp lệ; chỉ đọc, không chạy lại solver |
| Kết quả raw đã lưu từ audit trước | PASS | 12/12 kết quả có nghiệm, bao gồm CP-SAT, V5 GA và Micro-GA, được kiểm tra lại bằng validator mới và independent checker; không đổi raw data |
| Cached CP-SAT 20 jobs | NOT TESTED về output | Kết quả cũ không có nghiệm, không có events để kiểm tra; không chạy lại optimization |
| E2E legacy `tests/e2e/workflow.spec.ts` | **FAIL** | Test vẫn giả định automatic 10 jobs chọn MILP, rồi chờ `milp_time_limit`; app hiện chọn GA nên field này không tồn tại. Test dừng tại dòng 20 trước khi chạy solver. Không sửa thuật toán/UI để ép test cũ PASS. |
| Bảo toàn source/data bị cấm sửa | PASS | Hash 208 file baseline; 110 file solver/calendar/dữ liệu được bảo vệ: **0 thay đổi** |

72 regression cases mới gồm: 32 ca input sai kiểm tra upload/validate/run/output; 24 ca assignment corruption cho 4 loại event; 1 ca setup bằng 0 và hai định dạng assignment hợp lệ; 7 ca kiểm tra lại từng cạnh; 8 ca Accept/Excel/JSON với snapshot/result hợp lệ hoặc bị cố ý làm sai trong database test.

Frontend production gồm 27 browser tests và 7 calendar helper tests. Các test lớn tiếp tục kiểm tra 11.290/22.580 events, 20.000 events tổng hợp, day navigation, zoom, layout và mở lại lịch. Không chạy mới bài tối ưu 180/300 jobs.

Lượt browser đầu có 32 assertions/tests PASS nhưng lỗi teardown server test trong môi trường Windows sandbox, nên không coi đó là một lượt suite thành công hoàn chỉnh. Lượt cuối quản lý server test riêng và kết thúc bình thường. Lỗi fixture JSON trong lượt cuối được ghi giữ nguyên trong log, rồi kiểm tra lại đúng test bị ảnh hưởng sau khi sửa fixture; không cộng các lần chạy lặp vào số test duy nhất.

Backend có một cảnh báo Starlette/httpx deprecation đã tồn tại từ trước; không có test backend thất bại.

## 3. File thay đổi

### Tạo mới

- [backend/services/v5_validation.py](backend/services/v5_validation.py): validation input, raw assignment, precedence output và guard cho saved results.
- [backend/tests/test_v5_validation.py](backend/tests/test_v5_validation.py): 72 regression cases.
- [tests/production/validation-export.spec.ts](tests/production/validation-export.spec.ts): 2 browser tests cho JSON export thành công/thất bại.
- [SCHEDULING_VALIDATION_FIX_REPORT.md](SCHEDULING_VALIDATION_FIX_REPORT.md): báo cáo này.

### Chỉnh sửa

- [backend/services/v5_service.py](backend/services/v5_service.py): nối validation vào input, freeze và raw-result standardization.
- [backend/services/schedule_service.py](backend/services/schedule_service.py): guard chỉ đọc trước Accept.
- [backend/services/excel_service.py](backend/services/excel_service.py): gọi guard trước tạo workbook.
- [backend/api/scheduling.py](backend/api/scheduling.py): kiểm tra export theo query tùy chọn trên endpoint result hiện có.
- [src/pages/ScheduleFlow.tsx](src/pages/ScheduleFlow.tsx): handler JSON chờ kết quả validation trước download.
- [tests/production/compact-layout.spec.ts](tests/production/compact-layout.spec.ts): bổ sung mock validation tương ứng cho run replay không có trong database test.

Log, build và database tạm đặt trong `.tmp-qa/2026-10-25-fixes/`; fixture mở lại lịch dùng bản sao riêng trong `.tmp-verification/`. Không thêm dependency, migration hoặc thay đổi database sản xuất.

## 4. Evidence và giới hạn

- [Backend cuối — JUnit](.tmp-qa/2026-10-25-fixes/backend-final.xml), [log](.tmp-qa/2026-10-25-fixes/backend-final.log).
- [Frontend lượt đầy đủ](.tmp-qa/2026-10-25-fixes/browser-final-results.json), [test fixture chạy lại](.tmp-qa/2026-10-25-fixes/browser-recheck-results.json), [log chạy lại](.tmp-qa/2026-10-25-fixes/browser-recheck.log).
- [E2E legacy thất bại](.tmp-qa/2026-10-25-fixes/e2e.log).
- [TypeScript](.tmp-qa/2026-10-25-fixes/typecheck-final.log), [build](.tmp-qa/2026-10-25-fixes/build-final.log).
- [Hai saved runs 180 jobs](.tmp-qa/2026-10-25-fixes/historical.json), [cached raw results + independent checker](.tmp-qa/2026-10-25-fixes/cached-results.json).
- [Kiểm tra hash](.tmp-qa/2026-10-25-fixes/integrity.json).

Lệnh backend đã chạy:

```powershell
.venv/Scripts/python.exe -B -m pytest backend/tests -q --basetemp=.tmp-qa/2026-10-25-fixes/backend-final-temp -o cache_dir=.tmp-qa/2026-10-25-fixes/cache --junitxml=.tmp-qa/2026-10-25-fixes/backend-final.xml
.\run-frontend.cmd check
& '.\.tools\node-v22.23.3-win-x64\node.exe' node_modules/vite/bin/vite.js build --outDir .tmp-qa/2026-10-25-fixes/dist
```

Browser chạy trên server QA `127.0.0.1:8015` với database sao chép, theo `browser-final.config.ts`; test fixture được chạy lại theo `browser-recheck.config.ts` với grep `Compact summary and large Gantt layout`. Server QA được đóng sau kiểm thử. Không khởi động lại backend sản xuất.

Guard saved-result kiểm tra các constraint/assignment còn được lưu trong định dạng standardized. Định dạng cũ không giữ toàn bộ raw OIL phase/batch composition, nên không tuyên bố guard này tái kiểm chứng toàn bộ vật lý của mọi lịch lịch sử. Lịch mới vẫn đi qua full raw physical validation trước khi được đánh dấu Feasible. Không decode lại hay sửa lịch cũ khi Accept/Export.

CP-SAT, GA, SS-Micro-GA, V5 Decoder, `MIN_CMAX_FINAL_2C`, processing/batching/setup logic, Working Calendar và horizon 60 ngày giữ nguyên. Không có thay đổi Gantt hay UI layout. Các giới hạn audit cũ về long-duration endurance, soak và production-representative 300+ jobs vẫn chưa được giải quyết trong bản vá validation này.

**Kết luận:** QA-01…QA-05 đã được khắc phục trong phạm vi xác nhận. Không tuyên bố toàn bộ mọi suite đều PASS vì E2E legacy còn lỗi giả định chọn thuật toán; cần cập nhật test đó riêng theo workflow hiện hành.
