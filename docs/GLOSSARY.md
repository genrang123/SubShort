# SubShort

Ngôn ngữ dùng khi đối chiếu tính năng chuyển ngữ video và nghiệm thu kết quả của SubShort.

## Language

**Parity chức năng**:
Mức tương đương về hành vi và kết quả theo phạm vi nghiệm thu đã chốt; không đồng nghĩa cùng implementation hoặc chất lượng đã được chứng minh.
_Avoid_: Giống hoàn toàn DichVideo

**Đã triển khai**:
Tính năng đã có hành vi xử lý, nhưng chưa mặc nhiên có bằng chứng chạy tích hợp hoặc đạt chất lượng đầu ra.
_Avoid_: Đã nghiệm thu

**Đã kiểm chứng tích hợp**:
Tính năng đã chạy qua các thành phần thực trong phạm vi được ghi nhận bằng chứng; không mặc nhiên xác nhận chất lượng nội dung hoặc mọi môi trường.
_Avoid_: Đã nghiệm thu chất lượng

**Đã nghiệm thu chất lượng**:
Kết quả đã được đánh giá theo tiêu chí nội dung, timing, nghe hoặc nhìn tương ứng của tính năng, với bằng chứng và giới hạn được ghi rõ.
_Avoid_: Test xanh
