# Lịch sử thay đổi

## Chưa phát hành

### Thêm

- Bộ cài Windows x64 offline có giao diện tiếng Việt và tiếng Anh, kèm FFmpeg/ffprobe và Whisper Small CPU. Khi cài lại, có thể giữ dữ liệu cũ hoặc sao lưu để cài như mới.
- Qwen và FireRed tự tìm môi trường Python riêng cạnh EXE; trong Cài đặt → Nhận dạng chỉ cần chọn model và thiết bị CPU/CUDA.
- Tab **Đã hoàn tất** có cột **Thời gian hoàn tất** bên trái **Video**. Video cũ chưa có mốc thời gian sẽ hiện dấu gạch ngang.
- **Cài đặt → Giọng đọc → Tốc độ đọc** chỉnh riêng giọng lồng tiếng và mẫu nghe thử từ 0,5× đến 2×. Mặc định 1× giữ tốc độ của bộ tạo giọng. Nghe bản thu gốc không đổi.

### Thay đổi

- Giao diện desktop chuyển sang PySide6/Qt Widgets với theme tối, sidebar công cụ, bảng hàng đợi cuộn được và cài đặt theo danh mục. File picker và thanh tiêu đề vẫn dùng giao diện Windows. Đợt đổi giao diện không thay đổi pipeline, nơi lưu credential hay định dạng lịch sử.
- Hàng đợi video có thanh tổng hợp trạng thái, bước và mốc pipeline, tiến độ Drive riêng, cùng panel chi tiết theo video để xem log phiên và mở kết quả. Sidebar thu gọn khi cửa sổ hẹp; form dài cuộn riêng để giữ nút lưu và đóng trong tầm truy cập.
- Video xử lý xong tự chuyển sang tab Đã hoàn tất, vẫn giữ trạng thái tải lên Drive. Thao tác Hiện trong Explorer chọn đúng file kết quả của video.
- Chi tiết video lỗi hiển thị nguyên nhân và nút mở cài đặt API hoặc log riêng. Cửa sổ hẹp vẫn hiện bước xử lý và trạng thái Drive dưới tên video; ở chiều cao thấp, mở chi tiết tạm ẩn bảng và log chung.
- Hành động chính chuyển theo trạng thái hàng đợi. Tab Đã hoàn tất có nút Mở video khi chọn một thành phẩm; Hiện trong Explorer vẫn là thao tác riêng. Nguồn YouTube hợp lệ hiển thị ID để phân biệt các video.
- Cấu hình dịch, nhận diện phụ đề cũ và khóa LLM nằm trong Cài đặt → API & dịch vụ. Video lỗi dịch mở thẳng đến khóa của dịch vụ đã dùng; Base URL chỉ hiện với OpenAI-compatible.
- Cài đặt API có nút **Tải model** và danh sách chọn model riêng cho dịch văn bản và nhận diện phụ đề cũ. Tải danh sách không tự đổi model đang chọn.
- Cài đặt có danh sách chọn ngôn ngữ nguồn, ngôn ngữ đích, giọng có sẵn và giọng từ bản thu. Giọng Edge khớp mã ngôn ngữ đích đứng đầu danh sách.
- Thư viện giọng nói có thẻ giọng, tìm kiếm không dấu, bộ lọc theo dữ liệu danh mục, mục yêu thích và nghe thử. Danh mục Edge đa ngôn ngữ và preset VieNeu được tải theo yêu cầu và dùng lại trong phiên. Chọn thử không đổi giọng mặc định cho đến khi bấm Sử dụng giọng này; không thể xóa bản thu đang là mặc định hoặc còn được video có thể xử lý lại tham chiếu. Chi tiết hàng đợi vẫn hiển thị giọng theo cấu hình đã lưu của từng video.
- Nghe thử giọng VieNeu dùng lại bộ tạo giọng đã khởi động khi tải preset trong cùng cửa sổ. Đóng thư viện sẽ giải phóng bộ tạo giọng.
- Lồng tiếng không tự ép tăng tốc hoặc dịch lại để rút gọn. Câu kế tiếp của cùng người nói chờ clip trước; video giữ khung hình cuối nếu lời còn tiếp tục. `final_speed` vẫn áp đồng bộ cho hình, tiếng và phụ đề.
- Prompt dịch dùng thời lượng câu nguồn làm gợi ý nhịp nói, không phải giới hạn. Request OpenAI-compatible gửi `stream: false` và yêu cầu phản hồi JSON hoàn chỉnh.

### Sửa lỗi

- Lỗi xác thực hoặc hạn mức khi dịch có nút mở thẳng cài đặt API.
- Danh sách model, ngôn ngữ và giọng trong **Cài đặt** có thanh cuộn khi dài và không tràn khỏi hộp thoại.

## Bản ban đầu

- Nhập video local hoặc URL; nhận dạng, dịch, tạo phụ đề, lồng tiếng và xuất video.
- Thêm thông tin trạng thái, tiến độ và snapshot cấu hình cho từng job; hỗ trợ xử lý tuần tự theo batch, hủy, thử lại và dùng lại intermediate đã xác thực.
- Bảo vệ API key bằng Windows DPAPI; xác thực kết quả nhận dạng và đối chiếu bản dịch theo ID.
- Chọn một hoặc nhiều job để xóa khỏi lịch sử hàng đợi sau khi xác nhận. Không xóa video nguồn hoặc kết quả, và không cho xóa khi đang xử lý hoặc tải lên.
