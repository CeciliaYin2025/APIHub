# User / Role / Permission — Full CRUD Design

## Summary

Add UPDATE and DELETE operations for Users, Roles, and Permissions. Currently only CREATE and READ exist. This completes the CRUD lifecycle across both the API layer and Admin UI.

## Design Decisions

- **Soft delete for Users** — set `is_active=False` (preserves audit history)
- **Hard delete for Roles & Permissions** — remove from DB, but only if not currently assigned
- **Inline in router** — follows existing pattern, no service layer extraction
- **Password reset endpoint** — superadmin can reset any user's password

## Safety Rules

1. Cannot delete yourself (prevent admin lockout)
2. Cannot deactivate yourself
3. Cannot delete a Role that is still assigned to any User (409 Conflict)
4. Cannot delete a Permission that is still assigned to any Role (409 Conflict)
5. All endpoints require superuser access (consistent with existing CREATE endpoints)

## API Endpoints

### Users

| Method | Path | Description |
|--------|------|-------------|
| `PUT` | `/api/v1/auth/users/{user_id}` | Update user (full_name, is_active, is_superuser, role_ids) |
| `DELETE` | `/api/v1/auth/users/{user_id}` | Soft-delete user (set is_active=False) |
| `PATCH` | `/api/v1/auth/users/{user_id}/password` | Reset user password |

### Roles

| Method | Path | Description |
|--------|------|-------------|
| `PUT` | `/api/v1/auth/roles/{role_id}` | Update role (name, description, permission_ids) |
| `DELETE` | `/api/v1/auth/roles/{role_id}` | Hard-delete role (fails if assigned to users) |

### Permissions

| Method | Path | Description |
|--------|------|-------------|
| `PUT` | `/api/v1/auth/permissions/{permission_id}` | Update permission (resource, action, description) |
| `DELETE` | `/api/v1/auth/permissions/{permission_id}` | Hard-delete permission (fails if assigned to roles) |

## Schemas (new in `app/schemas/auth.py`)

### UserUpdate

```python
class UserUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None
    role_ids: list[int] | None = None
```

### RoleUpdate

```python
class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[int] | None = None
```

### PermissionUpdate

```python
class PermissionUpdate(BaseModel):
    resource: str | None = None
    action: str | None = None
    description: str | None = None
```

### PasswordReset

```python
class PasswordReset(BaseModel):
    new_password: str
```

## Router Changes (`app/api/v1/auth.py`)

Add 7 new endpoints, all guarded by `get_current_active_superuser`:

- **User PUT**: Fetch user by ID (404 if not found), update provided fields, sync `role_ids` via relationship, commit and return `UserOut`
- **User DELETE**: Fetch user (404), check not self (400), set `is_active=False`, commit
- **User PATCH password**: Fetch user (404), hash new password, commit
- **Role PUT**: Fetch role (404), update fields, sync `permission_ids` via relationship, commit and return `RoleOut`
- **Role DELETE**: Fetch role (404), check no users assigned (409), delete, commit
- **Permission PUT**: Fetch permission (404), update fields, commit and return `PermissionOut`
- **Permission DELETE**: Fetch permission (404), check no roles assigned (409), delete, commit

## Admin UI Changes (`app/admin/js/app.js`)

For each entity (Users, Roles, Permissions):

1. **Actions column** in table — add Edit and Delete buttons per row
2. **Edit modal** — reuse existing create modal pattern, pre-populate fields, call PUT endpoint
3. **Delete confirmation** — simple `confirm()` dialog before calling DELETE
4. **Password reset** — button on user edit modal, calls PATCH endpoint
5. **Table refresh** — reload table data after successful update/delete

## Error Responses

| Status | Condition |
|--------|-----------|
| 400 | Attempting to delete/deactivate yourself |
| 404 | Entity not found |
| 409 | Attempting to delete a role/permission still in use |

## Testing

- Test all 7 new endpoints (happy path + error cases)
- Test safety rules (self-delete prevention, in-use checks)
- Test Admin UI edit/delete flows manually
