# Livechat Contact Capture — Full luồng tích hợp

Mục tiêu: hết **“Khách truy cập”** bằng cách ghi `name` / `email` / `phone_number` lên **Contact Chatwoot** (nguồn sự thật list chat OmniHub). Config **theo từng inbox Website**.

---

## 0. Nguyên tắc

1. List chat đọc **Contact messaging** — không fake label FE.
2. Config theo **inbox** (`website_token` / `inbox_id`), không theo tenant chung.
3. Nhiều cổng thu thập → **một pipeline upsert** (PATCH Contact).
4. `client_session_id` (`oh_…`) dùng chung cho: persona sticky + contact pending + `$chatwoot.setUser`.

---

## 1. Shape config `contact_capture`

```json
{
  "enabled": true,
  "mode": "pre_chat_or_bot",
  "message": "Vui lòng để lại thông tin để chúng tôi hỗ trợ bạn tốt hơn.",
  "fields": [
    { "key": "name",  "label": "Họ và tên",     "enabled": true, "required": true },
    { "key": "phone", "label": "Số điện thoại", "enabled": true, "required": true },
    { "key": "email", "label": "Email",         "enabled": true, "required": false }
  ]
}
```

| `mode` | Pre-chat Chatwoot native | Overlay / bot hỏi thêm |
|--------|--------------------------|-------------------------|
| `off` | Tắt | Tắt |
| `pre_chat` | Bật | Không bắt buộc |
| `bot` | Tắt | Overlay hoặc bot hỏi |
| `pre_chat_or_bot` | Bật | Thiếu field → overlay/bot bổ sung (**khuyến nghị**) |

BE map sang Chatwoot:

| OmniHub | Chatwoot |
|---------|----------|
| `mode` ∈ `pre_chat`, `pre_chat_or_bot` | `pre_chat_form_enabled=true` |
| `mode` ∈ `off`, `bot` | `pre_chat_form_enabled=false` |
| `message` | `pre_chat_form_options.pre_chat_message` |
| `fields[].key=name` | field `fullName` |
| `fields[].key=phone` | field `phoneNumber` |
| `fields[].key=email` | field `emailAddress` |
| full policy (kể cả `mode=bot`) | **DB** `messaging_inbox_bindings.contact_capture` (JSONB) — nguồn sự thật OmniHub; Redis cache phụ |

**Quan trọng:** Chatwoot chỉ nhận `pre_chat_form_*` / field website **trong `channel`**. OmniHub BE:
1. Map `contact_capture` → `channel.pre_chat_form_*`
2. Hoist field widget top-level → `channel` **chỉ khi payload giống Website** (trùng key: **channel thắng**)
3. Persist full policy vào DB binding (không phụ thuộc Redis flush)
4. GET / public personas: **DB → Redis → derive Chatwoot**

Regression: `PYTHONPATH=. python scripts/sim_livechat_contact_capture.py`

---

## 2. Luồng Admin — cấu hình theo inbox

```text
[OmniHub Settings → Kênh → Inbox Website]
        │
        │  GET /api/v1/messaging/tenants/{tid}/inboxes/{inbox_id}
        │  ← data.messaging.contact_capture  (đã enrich)
        │
        ▼
  FE form: bật/tắt, mode, message, field enabled/required
        │
        │  PATCH /api/v1/messaging/tenants/{tid}/inboxes/{inbox_id}
        │  body: { "contact_capture": { ... }, ...các field inbox khác }
        │  Quyền: edit_messaging_inbox
        ▼
  OmniHub BE
    1. contact_capture → pre_chat_form_enabled + pre_chat_form_options
    2. Forward PATCH Chatwoot inbox
    3. Cache Redis: livechat:contact_policy:{website_token}
    4. Response gắn lại contact_capture
        ▼
  Widget Chatwoot / public GET personas đọc đúng policy
```

**POST tạo inbox** cũng nhận `contact_capture` (cùng transform).

---

## 3. Ba cổng visitor (chọn theo mode)

```text
                    ┌─────────────────────────────┐
                    │  Inbox contact_capture      │
                    └──────────────┬──────────────┘
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
   A. Pre-chat native      B. Overlay OmniHub        C. Bot / Agent
   (Chatwoot widget)       (persona + form)          (trong hội thoại)
           │                       │                       │
           └───────────────────────┼───────────────────────┘
                                   ▼
                    PATCH Contact Chatwoot
                    (name / email / phone_number)
                                   ▼
                    List chat hiện tên thật
```

---

## 4. Luồng A — Pre-chat Chatwoot native (`pre_chat` / `pre_chat_or_bot`)

```text
Visitor mở bubble widget
        │
        ▼
Chatwoot hiện pre-chat form (theo inbox đã PATCH)
  → Khách nhập fullName / phoneNumber / emailAddress
        │
        ▼
Chatwoot tạo/cập nhật Contact + Conversation
  → Contact đã có name/email/phone ngay từ đầu
        │
        ▼
Webhook conversation_created → OmniHub (bot assign / persona như cũ)
List OmniHub: hiện tên thật (không cần POST …/contact)
```

**FE Settings:** chỉ cần lưu `contact_capture` đúng mode.  
**FE Overlay:** có thể bỏ bước form liên hệ nếu `mode=pre_chat` và native form đủ.

---

## 5. Luồng B — Overlay (persona + liên hệ) — full tích hợp khuyến nghị khi dùng overlay

Thứ tự **bắt buộc** để không tách contact “Khách truy cập” vs `oh_…`:

```text
1) Chuẩn bị session
   FE sinh client_session_id (uuid) → sau canonicalize = oh_…

2) GET /api/v1/public/live-chat/{website_token}/personas
   ← selection_mode, personas[], contact_capture, client_session_prefix, ttl

3) (Nếu contact_capture.enabled && mode ∈ bot | pre_chat_or_bot | pre_chat)
   Hiện form theo contact_capture.fields / message / required
        │
        │  POST /api/v1/public/live-chat/{website_token}/contact
        │  {
        │    "client_session_id": "<raw hoặc oh_…>",
        │    "name": "...",
        │    "email": "...",
        │    "phone": "..."
        │  }
        ▼
   BE validate theo policy → Redis
   key: livechat:contact:{website_token}:{oh_…}
   ← data.client_session_id (dùng id này cho mọi bước sau)

4) (Nếu selection_mode = picker) chọn persona
        │  POST …/personas/select
        │  { persona_id, client_session_id: cùng oh_… }
        ▼
   Redis persona sticky (như cũ)

5) Inject / sẵn sàng Chatwoot widget
        │
        │  $chatwoot.setUser(client_session_id, {
        │    name: "...",          // nên truyền
        │    email: "...",         // nếu có
        │    avatar_url: "..."     // optional
        │  })
        │  // Phone: Chatwoot setUser thường không nhận phone
        │  // → phone chủ yếu từ Redis pending / upsert
        ▼
6) Mở / tạo hội thoại (cùng contact đã setUser)
        │
        ▼
7) Chatwoot webhook → OmniHub
   conversation_created (và retry ở message_created tin khách):
     - Match identifier = oh_… / Redis persona → sticky KG
     - Consume Redis contact pending → PATCH Contact
       (name, email, phone_number + additional_attributes)
        ▼
8) List chat / socket: tên thật (hết “Khách truy cập”)
```

### Sequence (overlay)

```text
Visitor          Overlay FE              OmniHub BE           Redis         Chatwoot
   │                 │                      │                  │              │
   │── open ────────▶│                      │                  │              │
   │                 │── GET personas ─────▶│                  │              │
   │                 │◀─ contact_capture ───│                  │              │
   │                 │── POST /contact ────▶│── SET pending ──▶│              │
   │                 │── POST select ──────▶│── SET persona ──▶│              │
   │                 │── setUser(oh_…) ─────────────────────────────────────▶│
   │                 │── open chat ─────────────────────────────────────────▶│
   │                 │                      │◀── webhook conv_created ───────│
   │                 │                      │── GET pending ──▶│              │
   │                 │                      │── PATCH contact ───────────────▶│
   │                 │                      │── sticky persona / assign bot ──▶│
```

### Lỗi hay gặp (đã gặp trước đây)

| Triệu chứng | Nguyên nhân | Cách tránh |
|-------------|-------------|------------|
| Vẫn “Khách truy cập” | `setUser` tạo contact `oh_…` **khác** contact đang chat | `setUser` **trước** khi tạo/mở conversation; cùng session |
| Persona menu `input_select` | Redis persona không khớp identifier | Cùng `client_session_id` select + setUser |
| Có name trên Redis nhưng list không đổi | Webhook miss / hết TTL | Retry ở tin khách đầu; TTL = persona TTL |

---

## 6. Luồng C — Bot / Agent trong hội thoại

Khi `mode=bot` hoặc khách bỏ qua pre-chat / thiếu field:

### C1. FE/Bot gọi upsert (ưu tiên)

```text
Bot / Agent UI thu đủ name|phone|email
        │
        │  POST /api/v1/messaging/tenants/{tid}/contacts/upsert
        │  JWT + quyền edit_messaging_conversation
        │  {
        │    "conversation_id": 456,   // hoặc contact_id
        │    "name": "...",
        │    "email": "...",
        │    "phone": "...",
        │    "source": "bot"           // hoặc agent_manual
        │  }
        ▼
OmniHub resolve contact_id (nếu chỉ có conversation_id)
  → PATCH Chatwoot /contacts/{id}
  → List đổi tên realtime (webhook contact_updated / reload list)
```

### C2. Ghi `custom_attributes` (optional)

Trên conversation (hoặc contact) set một trong:

- `contact_name`, `contact_email`, `contact_phone`
- hoặc `omnihub_contact: { "name", "email", "phone" }`

Webhook `message_created` (tin khách) → BE `maybe_upsert_contact_from_conversation_attrs` → PATCH Contact.

### C3. AI Agent `required_contact_fields` (KG)

KG hỏi trong chat theo memory config. Khi đủ field, **FE/backend tích hợp** phải gọi **C1 upsert** (KG không tự PATCH Chatwoot). Doc Settings agent vẫn độc lập; bridge = upsert API.

---

## 7. API cheat-sheet

| Ai | Method | Path | Auth |
|----|--------|------|------|
| Admin FE | GET | `/api/v1/messaging/tenants/{tid}/inboxes/{id}` | JWT + view inbox → có `contact_capture` |
| Admin FE | PATCH | `/api/v1/messaging/tenants/{tid}/inboxes/{id}` | JWT + edit inbox → body `contact_capture` |
| Overlay | GET | `/api/v1/public/live-chat/{website_token}/personas` | Public → `contact_capture` |
| Overlay | POST | `/api/v1/public/live-chat/{website_token}/contact` | Public |
| Overlay | POST | `/api/v1/public/live-chat/{website_token}/personas/select` | Public (persona) |
| Bot/Agent | POST | `/api/v1/messaging/tenants/{tid}/contacts/upsert` | JWT + edit conversation |
| Chatwoot | POST | `/api/v1/chatwoot-webhooks` | Webhook (pending + attrs sync) |

---

## 8. Checklist tích hợp FE

### Settings (inbox Website)

- [ ] Load GET inbox → bind form từ `messaging.contact_capture`
- [ ] Save PATCH kèm `contact_capture` (không chỉ `enable_email_collect`)
- [ ] Mode `bot` → UI vẫn cho chọn field (pre-chat native tắt phía BE)

### Overlay / host site

- [ ] Một `client_session_id` xuyên suốt contact → select → setUser
- [ ] Nếu `contact_capture.enabled`: form theo `fields` / `required` / `message`
- [ ] `POST …/contact` rồi mới `setUser` + mở chat
- [ ] `setUser(identifier, { name, email })` với đúng `data.client_session_id`
- [ ] Không mở conversation anonymous rồi mới setUser

### Inbox agent / bot

- [ ] Khi có đủ SĐT/email: `POST …/contacts/upsert` với `conversation_id`
- [ ] Không chỉ cập nhật state FE local

---

## 9. File BE

| File | Vai trò |
|------|---------|
| `app/services/v1/handle_chatwoot/contact_capture.py` | Policy, pre_chat map, Redis, upsert |
| `app/services/v1/handle_chatwoot/conversations.py` | Inbox GET/PATCH/POST + upsert API |
| `app/services/v1/handle_chatwoot/webhook.py` | Apply pending + attrs |
| `app/services/v1/handle_live_chat_public.py` | Public personas + submit contact |
| `app/api/v1/endpoints/live_chat_public.py` | Route public |
| `app/api/v1/endpoints/chatwoot/conversations.py` | Route upsert |

---

## 10. Quyết định product nhanh

| Tình huống | Mode gợi ý | Cổng chính |
|------------|------------|------------|
| Muốn tên ngay từ tin đầu, ít code overlay | `pre_chat` | A native |
| Đã có overlay persona | `pre_chat_or_bot` hoặc `bot` | B overlay (+ A nếu muốn) |
| LINE / không pre-chat | `bot` | C upsert |
| Site đã login | bất kỳ | Host `setUser` name/email sẵn + optional upsert phone |
