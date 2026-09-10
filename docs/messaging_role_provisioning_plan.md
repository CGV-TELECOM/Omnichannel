# Quy hoạch: OmniHub role ↔ Chatwoot account role & token

> Mục tiêu: **admin-partner** quản trị messaging bằng **token cá nhân** + role Chatwoot **`administrator`**, không phụ thuộc `CHATWOOT_USER_API_TOKEN` (user tích hợp) cho thao tác UI hàng ngày.

**Trạng thái: ĐÃ TRIỂN KHAI** (create/update/sync + token resolve + one-shot migrate).

---

## 1. Vấn đề đã xử lý

| Trước | Sau |
|-------|-----|
| Provision mặc định `role="agent"` cho mọi user | Map từ OmniHub role: **admin-partner → `administrator`**, còn lại → `agent` |
| Admin-partner luôn dùng env admin token | Ưu tiên **token cá nhân**; env chỉ fallback khi thiếu token / platform admin |
| Partner cũ vẫn agent trên Chatwoot | One-shot `POST /api/v1/user/sync-chatwoot-account-roles` hoặc CLI script |

**Phạm vi:** `administrator` chỉ trên **account messaging của đúng 1 tenant** — không cross-tenant.

---

## 2. Ma trận vai trò

| OmniHub role | Chatwoot `account_users.role` | Token UI |
|--------------|-------------------------------|----------|
| **admin-partner** | **`administrator`** | Token cá nhân (fallback env nếu chưa có) |
| **user** (agent) | **`agent`** | Token cá nhân + ACL inbox |
| **admin** (platform) | Không map 1–1 | `CHATWOOT_USER_API_TOKEN` |
| Bot gửi tin khách | N/A | `messaging_bots[]` / `CHATWOOT_BOT_API_TOKEN` |

Env admin token vẫn dùng cho: platform ops, webhook/job, bootstrap, reports của **agent** (API Chatwoot cần Administrator + OmniHub clamp).

---

## 3. Code đã đụng

| Thành phần | Việc |
|------------|------|
| `user_tokens.resolve_chatwoot_account_role*` | Helper map role |
| `user_tokens.patch_chatwoot_agent_account_role` | PATCH agents / Platform account_users |
| `user_tokens.resolve_agent_scoped_access_token` | Partner → personal trước |
| `user_tokens.resolve_reports_access_token` | Partner → personal; agent → env + clamp |
| `handle_user` create/update/sync | Không hardcode agent; đổi `role_id` → sync CW role |
| `POST /api/v1/user/sync-chatwoot-account-roles` | Migrate 1 tenant |
| `scripts/sync_chatwoot_account_roles.py` | CLI migrate |

---

## 4. Ops — chạy migrate sau deploy

```bash
# API (JWT user có edit_users, trong tenant hoặc platform + ?tenant_id=)
POST /api/v1/user/sync-chatwoot-account-roles

# CLI
python scripts/sync_chatwoot_account_roles.py --tenant-id <UUID>
python scripts/sync_chatwoot_account_roles.py --all-tenants
```

Nên chạy **một lần** trên mỗi tenant production trước khi kỳ vọng partner không còn phụ thuộc env token.

---

## 5. Acceptance criteria

- [x] Tạo user **admin-partner** → Chatwoot account role **administrator**
- [x] Tạo user **user** → **agent**
- [x] Partner resolve token: personal trước; platform vẫn env
- [x] Reports partner: personal; agent: env + clamp
- [x] Đổi OmniHub role partner ↔ user → sync Chatwoot role
- [x] One-shot migrate + unit test map role
- [ ] FE ẩn nút reassign khi thiếu `reassign_messaging_conversation` (tuỳ FE repo)

---

## 6. Tài liệu liên quan

- `docs/omnihub_feature_catalog.md` §2, §20
- `.cursor/rules/messaging-role-provisioning.mdc`
- `tests/test_messaging_role_provisioning.py`
