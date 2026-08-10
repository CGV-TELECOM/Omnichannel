# Cơ chế match số điện thoại khách hàng (Call / Telephony)

Áp dụng khi webhook tổng đài tự gắn `customer_id` vào `call_logs`.

**Code:** `app/services/v1/handle_telephony_webhook.py`  
**Hàm chính:** `_find_customer_by_phone`

---

## Mục tiêu

- Ghép đúng khách theo SĐT dù format khác nhau (`0901…`, `84901…`, `+84901…`).
- **Không đoán** khi nhiều khách trùng đuôi số → trả `None`, để `customer_id` trống.

---

## Pipeline

```
phone từ webhook (đã normalize nhẹ)
        │
        ▼
1) Lấy digits-only          _phone_digits()
        │
        ▼
2) Sinh biến thể VN         _phone_match_variants()
        │
        ▼
3) Exact match              Customer.phone IN variants
        │
        ├─ đúng 1 khách  → return customer_id
        ├─ > 1 khách     → return None (ambiguous)
        └─ 0 khách       → bước 4
        │
        ▼
4) Suffix 9 số (unique)     right(digits(phone), 9) == suffix
        │
        ├─ đúng 1 khách  → return customer_id
        ├─ > 1 khách     → return None + log warning
        └─ 0 khách       → return None
```

Phạm vi luôn lọc: `tenant_id` + `is_active == 1`.

---

## Chi tiết từng bước

### 1. Digits-only

Chỉ giữ `0-9` để so khớp ổn định (bỏ dấu cách, `-`, v.v. khi so đuôi).

### 2. Biến thể exact-match (VN)

| Input digits     | Variants được thử                          |
|------------------|--------------------------------------------|
| `0901234567`     | `0901234567`, `84901234567`, `+84901234567` |
| `84901234567`    | `84901234567`, `0901234567`, `+84901234567` |
| `+84901234567`   | (sau digits) giống hàng trên               |

So khớp **exact** với cột `customers.phone` (giá trị lưu trong DB phải nằm trong tập variants).

### 3. Fallback đuôi 9 số

Chỉ chạy khi exact không ra kết quả và số có **≥ 9 chữ số**.

- Chuẩn hóa phone trong DB bằng SQL: `regexp_replace(phone, '[^0-9]', '', 'g')`
- So: `right(..., 9) == 9_số_cuối_của_input`
- **Bắt buộc unique** trong tenant: nhiều match → không gắn khách

Lý do: tránh bug cũ `ILIKE '%last9%'` + `LIMIT 1` (gắn nhầm / không ổn định).

---

## Khi nào được gọi

- Webhook tạo call **inbound** lần đầu (map `from` → customer).
- Webhook cập nhật call inbound chưa có `customer_id`.

Outbound web thường đã có `customer_id` từ FE; webhook không ép match lại nếu đã có.

---

## Hành vi khi không match / ambiguous

| Tình huống              | Kết quả                         |
|-------------------------|---------------------------------|
| Không tìm thấy          | `customer_id = null`            |
| Nhiều khách cùng đuôi   | `customer_id = null` + warning log |
| Exact đúng 1            | Gắn `customer_id`               |
| Suffix đúng 1           | Gắn `customer_id`               |

Call log + event **vẫn được lưu**; chỉ thiếu map khách.

---

## Gợi ý vận hành

- Lưu `customers.phone` thống nhất (ưu tiên local `0…` hoặc E.164 `+84…`).
- Tránh nhiều khách active cùng 9 số cuối trong một tenant.
- Muốn chắc chắn: FE/API truyền sẵn `customer_id` khi tạo call outbound.
