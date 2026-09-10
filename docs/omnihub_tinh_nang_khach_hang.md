# OMNI HUB — Tổng hợp tính năng hệ thống

> Cập nhật: 09/09/2026

---

## 1. OMNI HUB là gì?

**OMNI HUB** là phần mềm chăm sóc khách hàng đa kênh cho doanh nghiệp:

- Chat Zalo OA, Website, và các kênh messaging khác trên **một màn hình**
- Kết hợp **ticket nội bộ**, **khách hàng**, **gọi điện**, **AI chatbot**, **đánh giá CSAT**
- Phân quyền rõ: quản trị hệ thống / quản trị doanh nghiệp / nhân viên
- Mỗi doanh nghiệp (tenant) có không gian làm việc riêng

---

## 2. Ai dùng hệ thống?

| Vai trò | Ai | Làm được gì (tóm tắt) |
|--------|-----|------------------------|
| **Quản trị nền tảng** | Đơn vị vận hành (CGV) | Tạo doanh nghiệp, quản trị toàn hệ thống |
| **Quản trị doanh nghiệp** | Admin khách hàng | Cấu hình kênh, nhân sự, bot, CSAT, báo cáo full trong công ty mình |
| **Nhân viên (Agent)** | CSKH / sale | Chat, ticket, khách hàng, gọi điện, báo cáo trong phạm vi được giao |

*(Menu trên giao diện hiện/ẩn theo quyền được gán cho từng vai trò.)*

---

## 3. Bảng tổng hợp theo nhóm tính năng

| STT | Nhóm | Tính năng chính trên hệ thống |
|-----|------|-------------------------------|
| 1 | Đăng nhập & tài khoản | Đăng nhập theo doanh nghiệp, hồ sơ, đổi giao diện, đăng xuất |
| 2 | Dashboard & báo cáo | Tổng quan hội thoại, hiệu suất, CSAT, biểu đồ theo thời gian |
| 3 | Trò chuyện đa kênh | Inbox, chat realtime, tạm ẩn, nhắc đến, gán nhân viên/nhãn/nhóm, lọc, gửi file |
| 4 | Ticket | Kanban/danh sách, luồng xử lý, tag, chi tiết & timeline |
| 5 | Khách hàng & Lead | CRM khách, lead tiềm năng, tag, gọi điện từ danh sách |
| 6 | Cuộc gọi | Softphone trên trình duyệt, lịch sử gọi, ghi âm, timeline |
| 7 | CSAT | Gửi link đánh giá, form khách chọn sao, báo cáo trên Dashboard |
| 8 | Nhân viên & phân quyền | User, vai trò, ma trận quyền, phòng ban/nhóm |
| 9 | Cài đặt vận hành | Chatbot, CSAT, kênh/inbox, nhãn, team, nhân viên hỗ trợ |
| 10 | Hệ thống AI Agent | Tài liệu, FAQ, crawl web, cấu hình agent, chat thử, báo cáo AI |
| 11 | Live chat website | Nhúng widget chat lên website doanh nghiệp |
| 12 | Thông báo realtime | Chuông tin chưa đọc, cập nhật hội thoại tức thì |
| 13 | Đa doanh nghiệp | Nhiều công ty trên một nền tảng, dữ liệu tách biệt |

---

## 4. Chi tiết từng nhóm tính năng

### 4.1 Đăng nhập & tài khoản

| Tính năng | Người dùng làm gì trên màn hình |
|-----------|----------------------------------|
| Đăng nhập | Nhập tài khoản / mật khẩu theo doanh nghiệp để vào hệ thống |
| Hồ sơ cá nhân | Xem thông tin tài khoản, vai trò, danh sách quyền |
| Đổi giao diện | Chọn sáng/tối, font chữ; tùy chỉnh mục hiện trên thanh bên |
| Tìm kiếm trang | Tìm nhanh menu/trang trên header (Ctrl / Cmd + K) |
| Chuyển workspace | **OMNI HUB** (vận hành CSKH) hoặc **Hệ thống A.I Agent** (nếu được cấp quyền AI) |
| Đăng xuất | Thoát phiên làm việc an toàn |

---

### 4.2 Dashboard Overview — Báo cáo vận hành

*Menu: **Dashboard Overview***

| Tính năng | Người dùng làm gì / thấy gì |
|-----------|------------------------------|
| Chọn khoảng thời gian | Lọc báo cáo theo từ ngày – đến ngày |
| Báo cáo tổng quan | Số hội thoại, tin nhắn đến/đi, thời gian xử lý; so sánh với kỳ trước |
| Trạng thái realtime | Đang mở / Chưa xử lý / Chưa gán / Đang chờ |
| Điểm CSAT | Số lượt đánh giá, tỷ lệ 4–5 sao, đã gửi / chưa phản hồi / hết hạn |
| Phân bố điểm sao | Biểu đồ 1–5 sao (Xuất sắc → Rất kém) |
| Bảng chi tiết đánh giá | Lọc trạng thái, lọc kênh/inbox, phân trang, mở link đánh giá |
| Lưu lượng theo giờ | Heatmap hội thoại và giải quyết theo khung giờ |
| Biểu đồ hội thoại | Tab Hội thoại / Tin nhắn / Thời gian xử lý |
| Hiệu suất theo chiều | Tab **Agent / Nhãn / Inbox / Nhóm** — so sánh hiệu suất |

---

### 4.3 Trò chuyện đa kênh (Omnichannel)

*Menu: **Trò chuyện***

#### Danh sách hội thoại

| Tính năng | Mô tả dễ hiểu |
|-----------|----------------|
| Tab **Tất cả** | Các cuộc trong phạm vi kênh nhân viên được tham gia |
| Tab **Của tôi** | Chỉ cuộc đang gán cho mình |
| Tab **Chưa phân công** | Cuộc chưa có người nhận |
| Thanh bên chat | **Cuộc trò chuyện** / **Kênh** / **Theo nhãn** / **Bộ lọc** / **Nhóm tham gia** |
| Lọc nhanh (Cuộc trò chuyện) | **Tất cả** / **Nhắc đến** (có @mention) / **Không giám sát** (chưa được tiếp nhận đúng cách) |
| Lọc theo kênh / nhãn / nhóm | Thu hẹp danh sách theo nguồn hoặc phân loại |
| Bộ lọc nâng cao | Lọc trạng thái, người phụ trách, inbox, nhãn; **Áp dụng / Reset / Lưu / Cập nhật / Xóa** bộ lọc tùy chỉnh |
| Tìm kiếm | Tìm nhanh theo tên cuộc trò chuyện |
| Trạng thái cuộc | Đang mở / Chờ xử lý / Đã xử lý / **Tạm ẩn (snooze)** |
| Realtime | Danh sách & tin nhắn cập nhật ngay khi có sự kiện mới |

#### Trong một cuộc trò chuyện

| Tính năng | Mô tả dễ hiểu |
|-----------|----------------|
| Xem lịch sử chat | Tin nhắn theo ngày (Hôm nay / Hôm qua / …), tải thêm tin cũ |
| Soạn & gửi tin | Text, emoji, đính kèm ảnh/file/audio/video/pdf |
| Trả lời (reply) | Trích dẫn tin để trả lời |
| Đang gõ… | Hiển thị trạng thái đang soạn tin (typing) |
| Đổi trạng thái | Mở / Chờ xử lý / Đã xử lý |
| **Tạm ẩn** | Giấu tạm cuộc chat: **đến khi có tin mới** / **đến ngày mai** / **đến tuần sau** |
| Gán nhân viên | Giao cuộc cho nhân viên (tùy quyền: tự nhận hoặc gán người khác) |
| Gán nhóm | Giao theo nhóm xử lý |
| Gán nhãn | Gắn nhãn phân loại |
| Thành viên hội thoại | Xem số thành viên / đang trực tuyến trên header chat |
| Gửi đánh giá CSAT | Gửi link để khách chấm điểm |
| Xóa tin / xóa cuộc | Theo quyền được cấp (xóa cuộc từ menu chuột phải trên danh sách) |
| Mở tab mới | Mở cuộc chat trên tab trình duyệt mới |
| Sao chép liên kết | Chia sẻ đường dẫn cuộc trò chuyện |

> **Lưu ý nghiệp vụ:** Nhân viên chỉ thấy hội thoại thuộc **kênh (inbox) mình được gán**. Trong cùng một kênh, có thể thấy cuộc của đồng nghiệp (giống cách Chatwoot vận hành).

---

### 4.4 Quản lý ticket

*Menu: **Quản lý ticket***

#### Danh sách ticket

| Tính năng | Mô tả |
|-----------|--------|
| Xem Kanban | Cột theo trạng thái; kéo thả đổi trạng thái |
| Xem danh sách | Bảng có lọc, ẩn/hiện cột, phân trang |
| Tạo ticket | Tiêu đề, mô tả, ưu tiên, người xử lý, template, tag, gắn luồng; thêm **trường mở rộng** (key–value) khi cần |
| Lọc | Theo mã, trạng thái, ưu tiên, template, luồng, người tạo/xử lý, tag |
| Ẩn / hiện cột | Tùy chỉnh cột hiển thị trên bảng danh sách |
| Sửa / Xóa | Cập nhật hoặc xóa ticket (theo quyền) |

**Trạng thái ticket:** Đang chờ → Đang mở → Đang xử lý → Tạm dừng → Đã giải quyết → Đã đóng / Đã hủy  

**Độ ưu tiên:** Thấp → Trung bình → Cao → Khẩn cấp → Nghiêm trọng

#### Chi tiết ticket

| Tính năng | Mô tả |
|-----------|--------|
| Luồng hoạt động | Theo dõi / chuyển bước xử lý |
| Bối cảnh | Ngữ cảnh / hội thoại liên quan |
| Timeline sự kiện | Lịch sử: tạo, cập nhật, gán, **bình luận**, đổi trạng thái… |

#### Luồng ticket

| Tính năng | Mô tả |
|-----------|--------|
| Quản lý luồng | Tạo / sửa / xóa quy trình xử lý |
| Cấu hình bước | Thêm bước, kéo đổi thứ tự, giao **cá nhân** hoặc **nhóm** |

#### Tag ticket

| Tính năng | Mô tả |
|-----------|--------|
| Quản lý tag | Tạo / sửa / xóa nhãn dùng cho ticket (tên, màu, mô tả) |

#### Mẫu ticket (Template)

| Tính năng | Mô tả |
|-----------|--------|
| Template ticket | Tạo mẫu ticket sẵn (ưu tiên, luồng, form mở rộng…) — dùng khi tạo ticket mới; hiện trên hệ thống theo quyền cấu hình |

---

### 4.5 Khách hàng & Lead

*Menu: **Quản lý khách hàng** / **Danh sách Lead***

| Tính năng | Mô tả |
|-----------|--------|
| Danh sách khách hàng | Xem tên, email, SĐT, trạng thái, tag |
| Thêm / sửa / xóa khách | Quản lý hồ sơ khách hàng |
| Tìm / lọc / phân trang | Làm việc nhanh trên danh sách lớn; chọn nhiều dòng |
| Gán tag khách | Phân loại khách |
| Gọi điện từ danh sách | Bấm gọi qua softphone trên trình duyệt |
| Khách hàng tiềm năng (Leads) | CRUD lead; tìm theo tên/email/SĐT/mô tả; sắp xếp A–Z hoặc mới/cũ nhất |
| Tag khách hàng | Quản lý bộ tag riêng cho CRM |
| Lead gắn AI Agent | Theo dõi giai đoạn (Mới / Đã liên hệ / Đã đóng), nguồn (Webchat, Zalo, Facebook, Telegram, Email…); **Kết nối / Đóng kết nối**; gọi điện; lọc theo agent / trạng thái |

---

### 4.6 Cuộc gọi & Softphone

*Menu: **Lịch sử cuộc gọi** + widget gọi trên header*

| Tính năng | Mô tả |
|-----------|--------|
| Softphone trình duyệt | Gọi điện ngay trên web; kéo thả widget góc màn hình (khi user được bật webphone) |
| Lịch sử cuộc gọi | Xem: chiều gọi (vào/ra), hotline, nguồn gọi, doanh nghiệp, người thực hiện, trạng thái, thời lượng, thời điểm bắt đầu/kết thúc |
| Ghi âm | Nghe / mở file ghi âm (nếu có) |
| Chi tiết cuộc gọi | Thông tin cuộc gọi, khách, ticket liên quan, thông tin kết nối thoại, **timeline sự kiện** cuộc gọi |
| Lọc & tìm | Theo khách, ticket, số điện thoại |
| Đồng bộ tổng đài | Hệ thống nhận sự kiện gọi (đổ chuông, nhấc máy, cúp máy…) và gắn với khách hàng khi khớp SĐT |

---

### 4.7 Đánh giá chất lượng (CSAT)

| Tính năng | Ai dùng | Mô tả |
|-----------|---------|--------|
| Gửi link đánh giá | Nhân viên trong chat | Gửi khảo sát sau hỗ trợ |
| Form công khai | Khách hàng | Chọn 1–5 sao + gửi (không cần đăng nhập OMNI HUB) |
| Báo cáo CSAT | Quản lý / agent (theo quyền) | Xem trên Dashboard |
| Bật/tắt CSAT | Admin doanh nghiệp | Trong **Cài đặt → Trạng thái doanh nghiệp** |
| Tự gửi khi kết thúc hội thoại | Hệ thống | Có thể bật để tự gửi khi cuộc được xử lý xong |

---

### 4.8 Người dùng, vai trò & phân quyền

*Menu: **Quản lý người dùng** / **Quyền hạn***

| Tính năng | Mô tả |
|-----------|--------|
| Quản lý người dùng | Thêm / sửa / xóa; gán **vai trò** + **cấp bậc**; đặt/đổi mật khẩu; bật/tắt hoạt động; **Bật webphone** (softphone) |
| Thống kê nhanh | Thẻ tổng số người dùng trên trang quản lý |
| Quản lý doanh nghiệp | Chỉ quản trị nền tảng — tạo/sửa doanh nghiệp trên hệ thống |
| Vai trò | Tạo / sửa / xóa vai trò (Admin doanh nghiệp, Agent…); tìm / lọc theo quyền |
| Phân quyền (ma trận) | Chọn doanh nghiệp + vai trò → bật/tắt từng quyền trên bảng; tìm kiếm tên quyền |
| Phòng ban | Quản lý phòng ban; vào chi tiết để quản lý **nhóm** trong phòng ban |
| Thành viên nhóm | Thêm / gỡ thành viên thuộc từng nhóm trong phòng ban |

---

### 4.9 Cài đặt vận hành doanh nghiệp

*Menu: **Cài đặt** (Settings)*

#### Tài khoản

| Tính năng | Mô tả |
|-----------|--------|
| Hồ sơ cá nhân | Xem username, email, họ tên, vai trò, danh sách quyền |

#### Trạng thái doanh nghiệp

| Tính năng | Mô tả |
|-----------|--------|
| Thông tin doanh nghiệp | Tên, mô tả |
| Ưu tiên phản hồi | **Bot** hoặc **Nhân viên** nhận trước |
| Bật/tắt Chatbot | Điều khiển chatbot tự động |
| Bật/tắt CSAT | Điều khiển khảo sát đánh giá |
| Lưu cài đặt | Áp dụng thay đổi vận hành cho cả doanh nghiệp |

#### Nhân viên hỗ trợ / Nhãn / Đội nhóm

| Tính năng | Mô tả |
|-----------|--------|
| Nhân viên hỗ trợ | Danh sách agent messaging; tìm kiếm; thêm / sửa / xóa; đồng bộ danh sách |
| Quản lý nhãn | Nhãn dùng trong chat (tên, màu, mô tả); tùy chọn **Hiển thị ở sidebar** Trò chuyện |
| Quản lý đội nhóm | Tạo team, thêm thành viên, sửa / xóa |

#### Quản lý kênh (Inbox)

| Tính năng | Mô tả |
|-----------|--------|
| Danh sách kênh | Website, API (Zalo OA…), Email, WhatsApp, Telegram, LINE, Instagram, SMS… |
| Tìm kênh / trạng thái | Xem kênh đang hoạt động; đồng bộ dữ liệu kênh |
| Tạo kênh mới | Chọn loại kênh và cấu hình |
| Sửa kênh | Tab **Cài đặt** / **Cộng tác viên** / **Cấu hình** |
| Phân công cộng tác viên | Gán nhân viên được làm việc trên từng kênh |
| Phân công tự động | Bật/tắt auto-assign trên kênh |
| Giờ làm việc | Bật/tắt & cấu hình giờ làm việc kênh |
| Web Widget | Màu thương hiệu, lời chào, tiêu đề/tagline, thời gian phản hồi, thu thập email, đính kèm, emoji, kết thúc hội thoại, avatar, domain cho phép, **sao chép mã nhúng**, xem trước khung chat |
| Thử nghiệm widget | Xem trước / trang test widget công khai |

#### Giao diện

| Tính năng | Mô tả |
|-----------|--------|
| Giao diện | Theme Sáng / Tối; chọn font chữ |
| Hiển thị | Chọn mục nào hiện trên thanh menu bên |

---

### 4.10 Hệ thống A.I Agent (workspace riêng)

*Chỉ hiện khi tài khoản được cấp quyền AI (chuyển workspace **Hệ thống Agent**).*

| Tính năng | Mô tả |
|-----------|--------|
| Báo cáo AI | Số khách, khách online, phiên hội thoại, token đã dùng; biểu đồ token / phiên chat; phân bố phản hồi Bot / Người / Chưa xử lý; thống kê theo nhóm người dùng; chủ đề ưa thích |
| Quản lý tài liệu | Upload tri thức (PDF, TXT, MD, Word, Excel, CSV, HTML, nguồn web…); theo dõi trạng thái xử lý & chất lượng; xem chi tiết / xóa / làm mới tiến trình |
| Quản lý FAQ | Câu hỏi – câu trả lời – biến thể câu hỏi; CRUD |
| Dữ liệu web (Crawl) | Cấu hình quét website; theo dõi trang (Phát hiện / Chấp nhận / Từ chối / Bỏ qua / Lỗi) |
| Quản lý Agent | Bật/tắt agent; cấu hình chi tiết: mô hình AI, tri thức, **prompt**, phong cách trả lời, viết lại câu hỏi, hành vi, truy hồi, phạm vi nghiệp vụ, FAQ, bộ nhớ hội thoại (tư vấn / thu thập liên hệ…) |
| Thử nghiệm Agent | Chọn agent; chat thử (gửi tin, đính kèm, emoji, làm mới hội thoại) trước khi đưa vào vận hành |
| Lead AI | Theo dõi lead phát sinh từ chatbot |

---

### 4.11 Live chat trên website

| Tính năng | Mô tả |
|-----------|--------|
| Nhúng widget | Lấy script từ cấu hình kênh Website gắn lên site doanh nghiệp |
| Nhiều “persona” AI | Khách có thể chọn hướng hỗ trợ (nếu doanh nghiệp cấu hình nhiều agent AI) |
| Chat realtime | Khách chat → hiện trên **Trò chuyện** của nhân viên |

---

### 4.12 Thông báo realtime

| Tính năng | Mô tả |
|-----------|--------|
| Chuông tin chưa đọc | Xem nhanh hội thoại chưa đọc trên header, bấm mở màn **Trò chuyện** |
| Cập nhật tức thì | Tin nhắn / trạng thái / phân công cập nhật **không cần tải lại trang** |
| Thông báo hệ thống trong chat | Các ghi chú / sự kiện hệ thống hiện xen trong luồng hội thoại (ví dụ tiếp nhận, bot tạm dừng…) |

---

### 4.13 Đa doanh nghiệp trên một nền tảng

| Tính năng | Mô tả |
|-----------|--------|
| Mỗi doanh nghiệp một không gian | Dữ liệu hội thoại, ticket, khách hàng tách theo công ty |
| Quản trị nền tảng tạo doanh nghiệp | Thêm công ty khách hàng vào hệ thống |
| Admin từng doanh nghiệp tự cấu hình | Kênh, nhân sự, bot, CSAT trong phạm vi công ty mình |
| Nhân viên chỉ thấy việc của mình | Theo quyền và kênh được giao |

---

## 5. Luồng nghiệp vụ điển hình (dễ demo khách)

### 5.1 Chăm sóc khách qua Zalo / Web

1. Khách nhắn trên Zalo OA hoặc widget website  
2. Cuộc xuất hiện trong **Trò chuyện**  
3. Nhân viên trả lời, gắn nhãn, đổi trạng thái  
4. (Tuỳ cấu hình) Bot trả lời trước, nhân viên nhận lại khi cần  
5. Gửi link **CSAT** hoặc hệ thống tự gửi khi xử lý xong  

### 5.2 Ticket nội bộ

1. Tạo ticket từ form (ưu tiên, người xử lý, luồng)  
2. Theo dõi trên Kanban / danh sách  
3. Chạy theo bước luồng đã cấu hình  
4. Xem timeline đầy đủ  

### 5.3 Gọi điện kèm CRM

1. Mở khách hàng / lead → bấm gọi (softphone)  
2. Cuộc gọi ghi vào **Lịch sử cuộc gọi**  
3. Có thể gắn với khách / ticket; nghe lại ghi âm  

### 5.4 Triển khai AI

1. Nạp tài liệu / FAQ / crawl web  
2. Cấu hình Agent → chat thử  
3. Bật chatbot trên doanh nghiệp; gắn kênh  
4. Theo dõi báo cáo AI và Lead  

---

## 6. Menu đang bật trên giao diện (checklist demo)

1. Dashboard Overview  
2. Quản lý ticket → Ticket / Luồng ticket / Tag ticket  
3. Quản lý khách hàng → Danh sách / Tiềm năng / Tag KH  
4. Quản lý người dùng → Người dùng / Doanh nghiệp *(chỉ quản trị nền tảng)*  
5. Quản lý phòng ban  
6. Danh sách Lead  
7. Trò chuyện  
8. Lịch sử cuộc gọi  
9. Quyền hạn → Vai trò / Phân quyền  
10. Cài đặt → Hồ sơ / Doanh nghiệp / Agent / Nhãn / Team / Kênh / Giao diện / Hiển thị  
11. (Workspace) Hệ thống A.I Agent → Báo cáo thống kê / Tài liệu / FAQ / Dữ liệu web / Agent / Thử nghiệm  

---

## 7. Một trang — giá trị mang lại cho khách hàng

| Nhu cầu doanh nghiệp | OMNI HUB đáp ứng bằng |
|----------------------|------------------------|
| Chat nhiều kênh một chỗ | Module **Trò chuyện** đa kênh |
| Đo chất lượng CSKH | **CSAT** + Dashboard |
| Quy trình nội bộ rõ ràng | **Ticket + Luồng** |
| Biết khách là ai | **CRM + Lead + Tag** |
| Vừa chat vừa gọi | **Softphone + Lịch sử cuộc gọi** |
| Giảm tải nhân sự | **AI Agent + Chatbot** |
| Phân quyền chặt | **Vai trò + Ma trận quyền** |
| Nhiều công ty / chi nhánh | **Đa doanh nghiệp (tenant)** |

---

## 8. Ghi chú khi trình bày (ngắn)

- Tài liệu này mô tả **tính năng người dùng nhìn thấy và dùng được**.  
- Một số trang demo cũ (Mail, Calendar, Pricing, Đăng ký/Quên mật khẩu…) hoặc mục **Template ticket** có thể còn trong mã nguồn nhưng **đã tắt / ẩn trên menu** hoặc chưa nối API thật — không đưa vào pitch chính.

---

*Hết — sẵn sàng dùng cho buổi trình bày khách hàng.*
