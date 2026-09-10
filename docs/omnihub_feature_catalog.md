# OmniHub — Danh mục chức năng hệ thống

> Tài liệu thống kê đầy đủ năng lực backend OmniHub để trình bày với khách hàng.  
> Nguồn: API `/api/v1`, RBAC seed, service modules, Socket.IO, tích hợp Chatwoot / KG / Telephony.  
> Cập nhật: 2026-09-09

---

## Mục lục

1. [Tổng quan sản phẩm](#1-tổng-quan-sản-phẩm)
2. [Mô hình vận hành & phân quyền](#2-mô-hình-vận-hành--phân-quyền)
3. [Đăng nhập & bảo mật](#3-đăng-nhập--bảo-mật)
4. [Đa doanh nghiệp (Multi-tenant)](#4-đa-doanh-nghiệp-multi-tenant)
5. [Người dùng & tổ chức nội bộ](#5-người-dùng--tổ-chức-nội-bộ)
6. [Quản lý ticket (Helpdesk)](#6-quản-lý-ticket-helpdesk)
7. [CRM khách hàng](#7-crm-khách-hàng)
8. [Trò chuyện đa kênh (Omnichannel)](#8-trò-chuyện-đa-kênh-omnichannel)
9. [Báo cáo & Dashboard messaging](#9-báo-cáo--dashboard-messaging)
10. [Chatbot AI / Knowledge Graph](#10-chatbot-ai--knowledge-graph)
11. [Live chat công khai (Widget)](#11-live-chat-công-khai-widget)
12. [CSAT / Đánh giá hội thoại](#12-csat--đánh-giá-hội-thoại)
13. [Thông báo & Realtime](#13-thông-báo--realtime)
14. [Tổng đài / Softphone / Call log](#14-tổng-đài--softphone--call-log)
15. [Nhật ký hệ thống (Audit)](#15-nhật-ký-hệ-thống-audit)
16. [Email & vận hành kỹ thuật](#16-email--vận-hành-kỹ-thuật)
17. [Bản đồ API theo prefix](#17-bản-đồ-api-theo-prefix)
18. [Ma trận quyền theo nhóm chức năng](#18-ma-trận-quyền-theo-nhóm-chức-năng)
19. [Slide tóm tắt 1 trang](#19-slide-tóm-tắt-1-trang)
20. [Lưu ý khi trình bày / hạn chế hiện tại](#20-lưu-ý-khi-trình-bày--hạn-chế-hiện-tại)

---

## 1. Tổng quan sản phẩm

**OmniHub** là lớp điều phối **omnichannel đa tenant**: một hệ thống đăng nhập/RBAC cho tổ chức; hội thoại đa kênh (proxy Chatwoot) + realtime; AI chatbot KG; CSAT; ticketing + workflow; CRM; softphone & nhật ký cuộc gọi; thông báo và audit.

| Thành phần | Vai trò |
|------------|---------|
| FastAPI backend | API nghiệp vụ `/api/v1` |
| JWT + Socket.IO | Xác thực & realtime |
| Chatwoot | Engine messaging đa kênh |
| KG Core | Trả lời chatbot AI |
| Telephony webhook | Sự kiện tổng đài / softphone |

---

## 2. Mô hình vận hành & phân quyền

### 2.1 Ba tầng vai trò

| Vai trò | Đối tượng | Phạm vi |
|--------|-----------|---------|
| **admin** (Admin Platform / CGV) | Vận hành hệ thống | Cross-tenant; catalog; provision messaging; `is_platform_admin` |
| **admin-partner** | Admin khách hàng / doanh nghiệp | Gần full trong **đúng 1 tenant** |
| **user** (Agent) | Nhân viên CSKH | Least privilege: hội thoại, ticket, khách, báo cáo theo quyền được gán |

### 2.2 Levels hệ thống

Catalog cấp bậc: **Admin → Manager → Staff → User**  
(Mutate level catalog chỉ dành cho platform.)

### 2.3 Quyền chỉ dành cho Platform (PLATFORM_ONLY)

- Tạo / sửa / xóa **permission** catalog  
- Tạo / sửa / xóa **tenant**  
- Tạo / sửa / xóa **level**  
- Tạo / sửa / xóa **messaging account**  
- Sync messaging integration  

Admin-partner **không** có các quyền trên.

---

## 3. Đăng nhập & bảo mật

**Prefix:** `/api/v1/auth` — public (không JWT)

| Chức năng | Mô tả |
|-----------|--------|
| Đăng nhập | Username + mật khẩu + **tên tenant**; chỉ user/tenant active |
| Phát token | Access token + refresh token |
| Làm mới phiên | Cấp lại access từ refresh |
| Đăng xuất | Thu hồi refresh |
| An toàn phiên | JWT gắn `user_id`, `tenant_id`, `token_version` (vô hiệu hóa phiên khi đổi version) |
| Phạm vi user | Username **unique trong từng tenant** |
| Phân quyền API | Check permission theo tên gắn role |
| Audit | Ghi action qua `@log_user_action` |
| CORS | Cấu hình theo môi trường |

Hầu hết API nghiệp vụ yêu cầu JWT (`Depends(verify_token)`).

---

## 4. Đa doanh nghiệp (Multi-tenant)

**Prefix:** `/api/v1/tenants` (JWT)

| Chức năng | Ai dùng | Mô tả |
|-----------|---------|--------|
| List / tạo / sửa / soft-delete tenant | Platform | Quản lý toàn hệ thống |
| Seed role mặc định | Hệ thống | Mỗi tenant mới: `admin-partner` + `user` |
| Cài đặt tenant hiện tại | Admin-partner | `GET/PATCH /tenants/me/settings` |
| Bật/tắt CSAT tự động | Admin-partner | `conversation_rating_enabled` |
| Bật/tắt chatbot | Admin-partner | `chatbot_enabled`, `default_responder` (`bot` \| `agent`) |
| Cấu hình AI bots messaging | Admin-partner | `messaging_bots[]` (không lộ raw token) |
| Webcall / softphone config | Theo tenant | Hotline, SIP domain, WS, credentials… |
| KG agents đa persona | Partner / platform | `GET/PUT …/kg-agents` |
| Provision messaging account | Platform | Liên kết tenant ↔ Chatwoot account |

---

## 5. Người dùng & tổ chức nội bộ

### 5.1 Người dùng — `/api/v1/user`

- Hồ sơ hiện tại (`current_user`)
- List / chi tiết / tạo / sửa / soft-delete (phân trang, search, sort)
- Sync user → Chatwoot agent
- Self: ensure Chatwoot API token (lưu meta, không lộ raw)
- Admin tenant: bulk sync Chatwoot API tokens
- Softphone: `GET /user/webcall` — cấu hình SIP/ws; gắn `sip_extension`

### 5.2 Role — `/api/v1/roles`

- List / tạo / sửa / xóa role  
- Partner được CRUD role **trong tenant**

### 5.3 Permission — `/api/v1/permissions`

- Catalog quyền (list / tạo / sửa / xóa — mutate chủ yếu platform)
- Matrix UI: `belong_to` + action (view / create / edit / delete / assign / …)

### 5.4 Role ↔ Permission — `/api/v1/role-permission`

- Xem quyền theo role; gán hàng loạt; gỡ từng quyền

### 5.5 Level — `/api/v1/levels`

- Xem danh sách / chi tiết level

### 5.6 Department — `/api/v1/departments`

- CRUD phòng ban + chi tiết quan hệ

### 5.7 Group — `/api/v1/groups`

- CRUD nhóm; chi tiết nhóm

### 5.8 User ↔ Group — `/api/v1/user_group`

- Gán nhiều user vào nhiều group; gỡ user khỏi group

### 5.9 Tag — `/api/v1/tags`

- CRUD tag; activate; thống kê  
- Phân loại: **ticket** | **customer**

---

## 6. Quản lý ticket (Helpdesk)

### 6.1 Ticket — `/api/v1/tickets`

| Chức năng | Chi tiết |
|-----------|----------|
| List / lọc / phân trang | Theo quyền & scope role |
| Chi tiết | Theo id hoặc mã `TKT-…` |
| Tạo | Title, mô tả, priority, template, flow, SLA, assignee, tags, extension_data |
| Sửa / soft-delete | Theo quyền |
| Gán nhân viên | `assign_ticket` |
| Đổi trạng thái | + ghi chú |

**Trạng thái:** pending, open, in_progress, on_hold, resolved, closed, cancelled  

**Độ ưu tiên:** low, medium, high, urgent, critical

### 6.2 Ticket Events / Timeline — `/api/v1/ticket-events`

- CRUD sự kiện (CREATED, UPDATED, ASSIGNED, COMMENTED, … + payload JSON)
- Timeline theo `ticket_id`

### 6.3 Ticket Template — `/api/v1/ticket-templates`

- CRUD mẫu: flow, SLA, **extension_schema** (form động), active

### 6.4 Ticket Context — `/api/v1/ticket-contexts`

- CRUD ngữ cảnh gắn ticket; list theo ticket

### 6.5 Ticket Extension — `/api/v1/ticket-extensions`

- Đọc / tạo / patch / xóa dữ liệu mở rộng theo schema template (1–1 với ticket)

### 6.6 Ticket Flow — `/api/v1/ticket-flows`

- CRUD định nghĩa quy trình xử lý

### 6.7 Ticket Flow Steps — `/api/v1/ticket-flow-steps`

- CRUD bước: thứ tự, gán **user** hoặc **group**

### 6.8 Ticket Flow Instances — `/api/v1/ticket-flow-instances`

- CRUD instance chạy trên ticket  
- Status: pending, running, paused, completed, failed, cancelled

---

## 7. CRM khách hàng

### 7.1 Customers — `/api/v1/customers`

- CRUD khách: name, phone, email, meta_data, active (tenant-scoped)
- Gắn / gỡ / xem **tags** (type = customer)
- Phone dùng match cuộc gọi inbound

### 7.2 Customer Provided Info — `/api/v1/customer-provided-info`

- CRUD thông tin khách cung cấp (name / email / phone / description) — lead / form bổ sung

### 7.3 Match SĐT với cuộc gọi

- Chuẩn hóa biến thể `0…` / `84…` / `+84…`
- Không đoán khi ambiguous (an toàn CRM)

---

## 8. Trò chuyện đa kênh (Omnichannel)

**Prefix authenticated:** `/api/v1/messaging`  
**Webhook public:** `/api/v1/chatwoot-webhooks`

### 8.1 Account messaging & vận hành

- Provision / get / update / delete messaging account theo tenant (platform tạo/xóa)
- Sync integration account-user
- Bulk actions (labels, fields…)
- Inbox members: list / thêm / sửa / gỡ agents
- Custom filters: list / tạo / sửa / xóa

### 8.2 Inbox, nhãn, hội thoại, tin nhắn

| Nhóm | Chức năng |
|------|-----------|
| Inbox | List / tạo / get / patch; sync bindings (website_token ↔ tenant; HMAC widget) |
| Labels | List / tạo / xóa |
| Conversations | List / filter / tạo / get / patch / xóa |
| Messages | List / gửi / xóa |
| Trạng thái hội thoại | Toggle open / resolved / pending / snoozed… |
| Tương tác | Typing, last seen, labels, custom attributes |
| Phân công | Tự nhận (`assign`); gán lại nhân viên/team (`reassign`); giao AI Bot |
| Đính kèm | List attachments |

### 8.3 Tìm kiếm messaging

- Search tổng hợp / contacts / conversations / messages / articles
- Shortcut contacts search & conversations search

### 8.4 Agents messaging

- List / tạo / cập nhật / xóa agent trên messaging

### 8.5 Teams

- CRUD team  
- List / thêm / gỡ / thay thế thành viên team

### 8.6 Agent bots

- Platform: list toàn bộ bot instance  
- Tenant: CRUD agent-bots  
- Account-agent-bots: gắn bot vào account (list / create / get / patch / delete)

### 8.7 Messaging users & SSO

- CRUD map user messaging  
- **SSO link** vào UI Chatwoot

### 8.8 Webhook messaging (public)

- Nhận event Chatwoot → map ID số → UUID nội bộ  
- Push Socket.IO `messaging_event` room `tenant:{id}`  
- Auto-assign AI bot; KG reply; persona picker; auto CSAT khi resolve

### 8.9 Chính sách phân quyền messaging (đã siết)

| Đối tượng | Hành vi |
|-----------|---------|
| Agent | Token **cá nhân** → Chatwoot enforce ACL **theo inbox** |
| Agent gán hội thoại | Chỉ **tự nhận** nếu không có `reassign_messaging_conversation` |
| Có `reassign_…` | Gán người khác / team theo logic Chatwoot |
| Admin-partner / platform | Full messaging trong phạm vi tenant (hiện có thể dùng admin env token — xem mục hạn chế) |
| Báo cáo agent | Phạm vi **cá nhân / inbox tham gia** |
| Báo cáo partner | Full tenant |

---

## 9. Báo cáo & Dashboard messaging

**Permission:** `view_messaging_reports`

| Báo cáo | Nội dung |
|---------|----------|
| Overview | Summary + realtime hội thoại + CSAT (1 request) |
| Timeseries | conversations, tin vào/ra, FRT, resolution, bot resolutions/handoffs, reply_time |
| Scope metric | account / agent / inbox / label / team |
| Summary kỳ | Tổng hợp kỳ hiện tại + kỳ trước |
| Conversation metrics | Account & theo agent (open / unattended / unassigned…) |
| Conversation traffic | Heatmap theo giờ/ngày |
| Grouped summary | Theo agent / team / label / channel |
| CSAT Chatwoot | Metrics + danh sách phản hồi (song song CSAT OmniHub) |

---

## 10. Chatbot AI / Knowledge Graph

| Chức năng | Mô tả |
|-----------|--------|
| Auto-assign bot | Hội thoại mới + chatbot bật + `default_responder=bot` |
| Agent người nhận | Bot **không** trả lời tiếp |
| Handback | `assign-bot` giao lại AI |
| Reply Gate | Chỉ trả lời khi assignee vẫn là bot của tenant |
| KG Core | Sinh câu trả lời; idempotency Redis |
| Đa persona | `kg_agents` theo tenant; sticky trên conversation |
| Live chat ≥ 2 persona | Menu chọn persona |
| Kênh khác | Dùng persona / bot mặc định |

---

## 11. Live chat công khai (Widget)

**Prefix:** `/api/v1/public/live-chat` — **không JWT**, có rate-limit

| Chức năng | Mô tả |
|-----------|--------|
| List personas | `GET /{website_token}/personas` theo inbox binding |
| Chọn persona | `POST /{website_token}/personas/select` trước khi inject Chatwoot |
| Sticky session | Redis + `client_session_id` (`oh_…`) cho widget |
| Fallback | Menu trong chat nếu Redis down / nhiều persona |
| CORS | Hỗ trợ widget đa domain |

---

## 12. CSAT / Đánh giá hội thoại

### 12.1 Public (khách) — `/api/v1/ratings`

- `GET /{token}` — mở form đánh giá  
- `POST /{token}` — gửi điểm **1–5** + comment  
- Rate-limit Redis  
- Status: pending | submitted | expired  

### 12.2 Nội bộ — `/api/v1/conversation-ratings`

- Metrics theo tenant (channel, inbox, agent, khoảng thời gian) + breakdown theo inbox  
- List responses / list ratings  
- **Gửi link CSAT thủ công** (assignee hoặc platform admin); cooldown / force_resend  
- **Tự gửi** khi conversation resolved nếu `conversation_rating_enabled`  
- Áp dụng mọi kênh messaging (Zalo OA, web widget, API, …)  
- Agent thường chỉ thấy CSAT trong **inbox mình là member**

---

## 13. Thông báo & Realtime

### 13.1 HTTP — `/api/v1/notifications` (JWT)

| Chức năng | Ai | Mô tả |
|-----------|-----|--------|
| Gửi tới user / tenant | Platform admin (send/broadcast) | Persist DB |
| Broadcast toàn hệ thống | Platform | |
| Online users / check online / WS status | Theo quyền | |
| History; mark read / read-all | User | |

**Loại thông báo:** info, success, warning, error, system, user_action, ticket_update, message

### 13.2 Socket.IO — mount `/socket.io`

- Handshake → `authenticate` bằng JWT → join room `tenant:{id}`
- Events: `connection_established`, `authenticated`, `authentication_error`, `messaging_event`, `missed_notifications_sent`, ping/pong, force_disconnect…
- Realtime hội thoại **không cần reload** trang
- `GET /ws/status` — tóm tắt số kết nối (public)

---

## 14. Tổng đài / Softphone / Call log

### 14.1 Call logs — `/api/v1/call-logs` (JWT)

- Tạo call outbound web (`sip_call_id` UUID)
- Cập nhật / list / get theo `sip_call_id`
- Timeline events + chi tiết event
- Liên kết customer, ticket, user
- Direction inbound / outbound; recording_url; duration / billsec
- Status: ringing, answered, ended, missed, …

### 14.2 Telephony webhook (public) — `/api/v1/telephony-webhooks`

- Event: ringing | answered | hangup | cdr | …
- Idempotent; map tenant (hotline / extension / config)
- Map agent theo SIP extension
- Match SĐT khách an toàn (không ambiguous)
- Lưu raw event + cập nhật snapshot call log

### 14.3 Softphone / Webcall

- Tenant `webcall_config`: enable_widget, sip_domain, hotlines, ws_server, credentials, webhook_secret…
- User: `sip_extension` + API cấu hình webcall

---

## 15. Nhật ký hệ thống (Audit)

**Prefix:** `/api/v1/logs` (JWT, `view_logs`)

- Danh sách audit phân trang; search; lọc ngày/tháng/năm
- Platform: lọc theo `tenant_id` (cross-tenant)
- Partner / agent: scoped trong tenant
- Ghi action quan trọng: ticket, tenant, messaging mutate, call, user…

---

## 16. Email & vận hành kỹ thuật

| Chức năng | Mô tả |
|-----------|--------|
| Gửi email HTML | SMTP + template |
| Test email | `POST /api/v1/test-email` — **chỉ platform admin** |
| Health | `GET /` welcome |
| WS status | `GET /ws/status` |

---

## 17. Bản đồ API theo prefix

| Prefix | Auth | Domain |
|--------|------|--------|
| `/api/v1/auth` | Public | Auth |
| `/api/v1/chatwoot-webhooks` | Public | Messaging webhook |
| `/api/v1/telephony-webhooks` | Public | Telephony |
| `/api/v1/ratings` | Public | CSAT khách |
| `/api/v1/public/live-chat` | Public | Widget personas |
| `/api/v1/user` | JWT | Users / softphone / Chatwoot sync |
| `/api/v1/roles` | JWT | Roles |
| `/api/v1/permissions` | JWT | Permission catalog |
| `/api/v1/role-permission` | JWT | Gán quyền |
| `/api/v1/levels` | JWT | Levels |
| `/api/v1/departments` | JWT | Departments |
| `/api/v1/groups` | JWT | Groups |
| `/api/v1/user_group` | JWT | User–group |
| `/api/v1/tags` | JWT | Tags |
| `/api/v1/tenants` | JWT | Tenants / settings / KG |
| `/api/v1/tickets` | JWT | Tickets |
| `/api/v1/ticket-events` | JWT | Ticket timeline |
| `/api/v1/ticket-templates` | JWT | Templates |
| `/api/v1/ticket-contexts` | JWT | Contexts |
| `/api/v1/ticket-extensions` | JWT | Extensions |
| `/api/v1/ticket-flows` | JWT | Flows |
| `/api/v1/ticket-flow-steps` | JWT | Flow steps |
| `/api/v1/ticket-flow-instances` | JWT | Flow runtime |
| `/api/v1/customers` | JWT | CRM |
| `/api/v1/customer-provided-info` | JWT | Provided info |
| `/api/v1/messaging/*` | JWT | Omnichannel Chatwoot |
| `/api/v1/conversation-ratings` | JWT | CSAT nội bộ |
| `/api/v1/notifications` | JWT | Notifications |
| `/api/v1/call-logs` | JWT | Call history |
| `/api/v1/logs` | JWT | Audit |
| `/api/v1/test-email` | JWT (platform) | Email test |
| `/socket.io` | JWT qua handshake | Realtime |

---

## 18. Ma trận quyền theo nhóm chức năng

### 18.1 User / RBAC / Org

`view_users`, `current_user`, `create_users`, `edit_users`, `delete_users`  
`view_roles`, `create_roles`, `edit_roles`, `delete_roles`  
`view_permissions`, `create_permissions`, `edit_permissions`, `delete_permissions`  
`assign_permissions_to_role`, `delete_permission_from_role`  
`view_logs`  
`view_levels`, `create_level`, `edit_level`, `delete_level`  
`view_departments`, `create_department`, `edit_department`, `delete_department`  
`view_groups`, `create_group`, `edit_group`, `delete_group`, `assign_user_to_group`  
`view_tags`, `create_tag`, `edit_tag`, `delete_tag`

### 18.2 Ticket

`view/create/edit/delete_tickets`, `assign_ticket`  
`view/create/edit/delete_ticket_events`  
`view/create/edit/delete_ticket_templates`  
`view/create/edit/delete_ticket_contexts`  
`view/create/edit/delete_ticket_extensions`  
`view/create/edit/delete_ticket_flows`  
`view/create/edit/delete_ticket_flow_instances`  
`view/create/edit/delete_ticket_flow_steps`

### 18.3 CRM

`view/create/edit/delete_customers`  
`view/create/edit/delete_customer_provided_info`

### 18.4 Tenant

`view/create/edit/delete_tenants`  
`view_own_tenant_settings`, `edit_own_tenant_settings`

### 18.5 Messaging

`view/create/edit/delete_messaging_accounts`, `sync_messaging_integration`  
`view/create/edit/delete_messaging_conversations`  
`send_messaging_message`, `delete_messaging_message`  
`assign_messaging_conversation`, `reassign_messaging_conversation`  
`view/create/edit_messaging_inboxes`, `manage_messaging_inbox_members`  
`view/create/delete_messaging_labels`, `bulk_messaging_actions`  
`view/create/edit/delete_messaging_custom_filters`  
`view/create/edit/delete_messaging_agents`  
`view/create/edit/delete_messaging_teams`, `manage_messaging_team_members`  
`view/create/edit/delete_messaging_agent_bots`  
`view/create/edit/delete_messaging_users`  
`view_messaging_reports`

### 18.6 Call

`view_call_logs`, `create_call_log`, `edit_call_log`, `view_call_log_events`

### 18.7 Agent mặc định (role `user`) — tóm tắt

Được: xem/tạo/sửa hội thoại & ticket trong phạm vi; gửi tin; tự gán; xem báo cáo (scope cá nhân); CSAT; call log; xem agents/teams/inboxes…  

Không (mặc định): CRUD template/flow admin đầy đủ; xóa ticket/customer; quản lý agent-bot / inbox members đầy đủ; `reassign` gán người khác; quyền PLATFORM_ONLY.

---

## 19. Slide tóm tắt 1 trang

| # | Nhóm chức năng | Giá trị với khách hàng |
|---|----------------|------------------------|
| 1 | Đăng nhập & phân quyền 3 tầng | An toàn, đúng vai trò |
| 2 | Đa doanh nghiệp + cài đặt | Mỗi khách một không gian riêng |
| 3 | User / phòng ban / nhóm / tag | Tổ chức nội bộ |
| 4 | Ticket + workflow | Helpdesk chuẩn quy trình |
| 5 | CRM khách hàng | Hồ sơ & match cuộc gọi |
| 6 | Chat đa kênh (Zalo, web…) | Omnichannel tập trung |
| 7 | Báo cáo / dashboard | Điều hành CSKH |
| 8 | AI chatbot + KG | Tự động hóa trả lời |
| 9 | Widget live chat | Chăm sóc trên website |
| 10 | CSAT | Đo chất lượng phục vụ |
| 11 | Realtime & thông báo | Làm việc tức thì |
| 12 | Softphone & call log | Gom thoại + chat một chỗ |
| 13 | Audit log | Tuân thủ / truy vết |

**Một câu định vị:**  
*OmniHub là nền tảng điều phối CSKH đa kênh, đa doanh nghiệp — hội thoại, ticket, CRM, AI, thoại và báo cáo trên một đăng nhập.*

---

## 20. Lưu ý khi trình bày / hạn chế hiện tại

Các điểm đang hoàn thiện / vận hành:

1. **Admin-partner ↔ Chatwoot `administrator` + token cá nhân** — đã triển khai map role khi create/update/sync user; one-shot migrate: `POST /api/v1/user/sync-chatwoot-account-roles` hoặc `scripts/sync_chatwoot_account_roles.py`. Chi tiết: `docs/messaging_role_provisioning_plan.md`. Partner thiếu personal token vẫn fallback env (transition).
2. Permission **`reassign_messaging_conversation`** đã có trong seed code — môi trường thật cần **chạy seed / gán quyền** vào role.
3. FE tab **“Tất cả”** hội thoại có thể gộp open + pending; Chatwoot UI All mặc định thường chỉ **open** — số đếm có thể khác nhau dù ACL inbox đúng.
4. Seed RBAC startup trong `main.py` có thể đang tắt — triển khai production cần quy trình seed/migrate quyền rõ ràng.

---

## Phụ lục A — Service modules chính

| Module | Vai trò |
|--------|---------|
| `handle_auth` | Login / logout / refresh |
| `handle_user` | User CRUD, webcall, Chatwoot token sync |
| `handle_role` / `handle_permissions` / `handle_role_permission` | RBAC |
| `handle_level` / `handle_department` / `handle_group` / `handle_user_group` | Org |
| `handle_tenant` / `handle_tenant_kg_agent` | Multi-tenant, settings, KG personas |
| `handle_tag` | Tags ticket / customer |
| `handle_ticket*` / flow / event / template / context / extension | Ticketing |
| `handle_customer` / `handle_customer_provided_info` | CRM |
| `handle_chatwoot/*` | Account, conversations, agents, teams, bots, reports, webhook, chatbot |
| `handle_messaging_inbox_binding` | website_token ↔ inbox |
| `handle_conversation_rating` | CSAT OmniHub |
| `handle_live_chat_public` | Public personas |
| `handle_call_log` / `handle_telephony_webhook` | Telephony |
| `handle_notification` | Persist + Socket push |
| `handle_log` | Audit read |

---

## Phụ lục B — Tài liệu kỹ thuật liên quan trong repo

- `docs/frontend_integration.md` — Socket.IO realtime messaging  
- `docs/ai_bot_assign_reply_flow.md` — Bot assign / handback / KG / live-chat  
- `docs/call_customer_phone_match.md` — Match SĐT gọi vào CRM  

---

*Hết tài liệu. Có thể dùng nguyên văn cho deck khách hàng hoặc rút gọn theo mục 19.*
