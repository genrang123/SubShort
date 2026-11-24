# Lịch sử thay đổi

## Chưa phát hành

### Thêm

- Bộ cài Windows x64 offline có giao diện tiếng Việt và tiếng Anh, kèm FFmpeg/ffprobe và Whisper Small CPU. Khi cài lại, có thể giữ dữ liệu cũ hoặc sao lưu để cài như mới.
- Qwen và FireRed tự tìm môi trường Python riêng cạnh EXE; trong Cài đặt → Nhận dạng chỉ cần chọn model và thiết bị CPU/CUDA.

### Thay đổi

- Giao diện desktop chuyển sang PySide6/Qt Widgets với theme tối, sidebar công cụ, bảng hàng đợi cuộn được và cài đặt theo danh mục. File picker và thanh tiêu đề vẫn dùng giao diện Windows. Đợt đổi giao diện không thay đổi pipeline, nơi lưu credential hay định dạng lịch sử.
- Hàng đợi video có thanh tổng hợp trạng thái, bước và mốc pipeline, tiến độ Drive riêng, cùng panel chi tiết theo video để xem log phiên và mở kết quả. Sidebar thu gọn khi cửa sổ hẹp; form dài cuộn riêng để giữ nút lưu và đóng trong tầm truy cập.
- Video xử lý xong tự chuyển sang tab Đã hoàn tất, vẫn giữ trạng thái tải lên Drive. Thao tác Hiện trong Explorer chọn đúng file kết quả của video.
- Thư viện giọng nói có thẻ giọng, tìm kiếm không dấu, bộ lọc theo dữ liệu danh mục, mục yêu thích và nghe thử. Danh mục Edge tiếng Việt và preset VieNeu được tải theo yêu cầu và dùng lại trong phiên. Chọn thử không đổi giọng mặc định cho đến khi bấm Sử dụng giọng này; không thể xóa bản thu đang là mặc định hoặc còn được video có thể xử lý lại tham chiếu. Chi tiết hàng đợi vẫn hiển thị giọng theo cấu hình đã lưu của từng video.
- Nghe thử giọng VieNeu dùng lại bộ tạo giọng đã khởi động khi tải preset trong cùng cửa sổ. Đóng thư viện sẽ giải phóng bộ tạo giọng.
- Lồng tiếng giữ nguyên clip TTS, không ép tăng tốc hoặc dịch lại để rút gọn. Câu kế tiếp của cùng người nói chờ clip trước; video giữ khung hình cuối nếu lời còn tiếp tục. `final_speed` vẫn áp đồng bộ cho hình, tiếng và phụ đề.
- Prompt dịch dùng thời lượng câu nguồn làm gợi ý nhịp nói, không phải giới hạn. Request OpenAI-compatible gửi `stream: false` và yêu cầu phản hồi JSON hoàn chỉnh.

### Sửa lỗi

- Nút **API & dịch vụ** mở lại hộp thoại cấu hình dịch và khóa API.

## Bản ban đầu

- Nhập video local hoặc URL; nhận dạng, dịch, tạo phụ đề, lồng tiếng và xuất video.
- Thêm thông tin trạng thái, tiến độ và snapshot cấu hình cho từng job; hỗ trợ xử lý tuần tự theo batch, hủy, thử lại và dùng lại intermediate đã xác thực.
- Bảo vệ API key bằng Windows DPAPI; xác thực kết quả nhận dạng và đối chiếu bản dịch theo ID.
- Chọn một hoặc nhiều job để xóa khỏi lịch sử hàng đợi sau khi xác nhận. Không xóa video nguồn hoặc kết quả, và không cho xóa khi đang xử lý hoặc tải lên.
