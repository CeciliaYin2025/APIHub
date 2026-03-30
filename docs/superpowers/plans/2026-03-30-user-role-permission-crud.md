# User / Role / Permission Full CRUD — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add UPDATE and DELETE endpoints for Users, Roles, and Permissions (API + Admin UI), completing the CRUD lifecycle.

**Architecture:** Inline in existing router (`app/api/v1/auth.py`), new Pydantic schemas for update payloads, and Admin UI changes in `app/admin/js/app.js`. Soft-delete for Users (set `is_active=False`), hard-delete for Roles/Permissions with referential integrity checks.

**Tech Stack:** FastAPI, SQLAlchemy ORM, Pydantic v2, Vanilla JS (Admin SPA)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `app/schemas/auth.py` | Modify | Add `UserUpdate`, `RoleUpdate`, `PermissionUpdate`, `PasswordReset` schemas |
| `app/api/v1/auth.py` | Modify | Add 7 new endpoints (PUT/DELETE users, roles, permissions + PATCH password) |
| `app/admin/js/app.js` | Modify | Add Edit/Delete buttons, edit modals, password reset UI |
| `tests/test_auth_crud.py` | Create | Integration tests for all new endpoints |

---

### Task 1: Add Update/Delete Pydantic Schemas

**Files:**
- Modify: `app/schemas/auth.py`

- [ ] **Step 1: Add the four new schema classes to `app/schemas/auth.py`**

Append after the existing `PermissionOut` class (after line 61):

```python
class UserUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None
    role_ids: list[int] | None = None


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[int] | None = None


class PermissionUpdate(BaseModel):
    resource: str | None = None
    action: str | None = None
    description: str | None = None


class PasswordReset(BaseModel):
    new_password: str
```

- [ ] **Step 2: Verify the file is valid Python**

Run: `python -c "from app.schemas.auth import UserUpdate, RoleUpdate, PermissionUpdate, PasswordReset; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/schemas/auth.py
git commit -m "feat: add UserUpdate, RoleUpdate, PermissionUpdate, PasswordReset schemas"
```

---

### Task 2: Add User Update, Delete, and Password Reset Endpoints

**Files:**
- Modify: `app/api/v1/auth.py`

- [ ] **Step 1: Update imports in `app/api/v1/auth.py`**

Update the schemas import to include the new types:

```python
from app.schemas.auth import (
    PasswordReset,
    PermissionCreate,
    PermissionOut,
    PermissionUpdate,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    Token,
    UserCreate,
    UserOut,
    UserUpdate,
)
```

- [ ] **Step 2: Add PUT `/auth/users/{user_id}` endpoint**

Add after the `list_users` function (after line 67):

```python
@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_active_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_active is not None:
        if not payload.is_active and user.id == admin.id:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        user.is_active = payload.is_active
    if payload.is_superuser is not None:
        user.is_superuser = payload.is_superuser
    if payload.role_ids is not None:
        roles = db.query(Role).filter(Role.id.in_(payload.role_ids)).all()
        user.roles = roles
    db.commit()
    db.refresh(user)
    return user
```

- [ ] **Step 3: Add DELETE `/auth/users/{user_id}` endpoint**

Add after the `update_user` function:

```python
@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_active_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user.is_active = False
    db.commit()
```

- [ ] **Step 4: Add PATCH `/auth/users/{user_id}/password` endpoint**

Add after the `delete_user` function:

```python
@router.patch("/users/{user_id}/password", status_code=204)
def reset_user_password(
    user_id: int,
    payload: PasswordReset,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.hashed_password = get_password_hash(payload.new_password)
    db.commit()
```

- [ ] **Step 5: Verify syntax**

Run: `python -c "from app.api.v1.auth import router; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/auth.py
git commit -m "feat: add user update, soft-delete, and password reset endpoints"
```

---

### Task 3: Add Role Update and Delete Endpoints

**Files:**
- Modify: `app/api/v1/auth.py`

- [ ] **Step 1: Add PUT `/auth/roles/{role_id}` endpoint**

Add after the `list_roles` function:

```python
@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    if payload.permission_ids is not None:
        perms = db.query(Permission).filter(Permission.id.in_(payload.permission_ids)).all()
        role.permissions = perms
    db.commit()
    db.refresh(role)
    return role
```

- [ ] **Step 2: Add DELETE `/auth/roles/{role_id}` endpoint**

Add after the `update_role` function:

```python
@router.delete("/roles/{role_id}", status_code=204)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.users:
        raise HTTPException(status_code=409, detail="Cannot delete role that is still assigned to users")
    db.delete(role)
    db.commit()
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "from app.api.v1.auth import router; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/api/v1/auth.py
git commit -m "feat: add role update and delete endpoints"
```

---

### Task 4: Add Permission Update and Delete Endpoints

**Files:**
- Modify: `app/api/v1/auth.py`

- [ ] **Step 1: Add PUT `/auth/permissions/{permission_id}` endpoint**

Add after the `list_permissions` function:

```python
@router.put("/permissions/{permission_id}", response_model=PermissionOut)
def update_permission(
    permission_id: int,
    payload: PermissionUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    perm = db.query(Permission).filter(Permission.id == permission_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    if payload.resource is not None:
        perm.resource = payload.resource
    if payload.action is not None:
        perm.action = payload.action
    if payload.description is not None:
        perm.description = payload.description
    db.commit()
    db.refresh(perm)
    return perm
```

- [ ] **Step 2: Add DELETE `/auth/permissions/{permission_id}` endpoint**

Add after the `update_permission` function:

```python
@router.delete("/permissions/{permission_id}", status_code=204)
def delete_permission(
    permission_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_active_superuser),
):
    perm = db.query(Permission).filter(Permission.id == permission_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    if perm.roles:
        raise HTTPException(status_code=409, detail="Cannot delete permission that is still assigned to roles")
    db.delete(perm)
    db.commit()
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "from app.api.v1.auth import router; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/api/v1/auth.py
git commit -m "feat: add permission update and delete endpoints"
```

---

### Task 5: Add Actions Column and Edit/Delete to Users Table in Admin UI

**Files:**
- Modify: `app/admin/js/app.js`

- [ ] **Step 1: Add Actions column to Users table header**

In `renderUsers()`, change the table header (line 332):

```javascript
// Old:
<th>ID</th><th>Username</th><th>Full Name</th><th>Roles</th><th>Status</th><th>Superuser</th>

// New:
<th>ID</th><th>Username</th><th>Full Name</th><th>Roles</th><th>Status</th><th>Superuser</th><th>Actions</th>
```

- [ ] **Step 2: Add Edit/Delete buttons to each user row**

In `renderUsers()`, change the table body rows (the `users.map` template). Replace the existing `</tr>` closing with an Actions cell before it:

```javascript
<td>${u.is_superuser ? '<span class="badge badge-blue">Yes</span>' : '—'}</td>
<td>
  <button class="btn btn-xs btn-secondary" onclick="openEditUserModal(${u.id})">Edit</button>
  <button class="btn btn-xs btn-danger" onclick="deleteUser(${u.id}, '${esc(u.username)}')">Delete</button>
</td>
</tr>
```

- [ ] **Step 3: Add `openEditUserModal` function**

Add after the existing `window.openUserModal` function (after line 386):

```javascript
window.openEditUserModal = async function(userId) {
  const roles = window._cachedRoles || [];
  let user;
  try {
    const users = await api('GET', '/auth/users');
    user = users.find(u => u.id === userId);
    if (!user) { toast('User not found', 'error'); return; }
  } catch (err) { toast(err.message, 'error'); return; }

  const userRoleIds = (user.roles || []).map(r => r.id);
  const body = `
    <form id="editUserForm">
      <input type="hidden" name="user_id" value="${user.id}">
      <div class="form-group">
        <label>Username</label>
        <input type="text" value="${esc(user.username)}" disabled>
      </div>
      <div class="form-group">
        <label>Full Name</label>
        <input type="text" name="full_name" value="${esc(user.full_name || '')}">
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Active</label>
          <select name="is_active">
            <option value="true" ${user.is_active ? 'selected' : ''}>Yes</option>
            <option value="false" ${!user.is_active ? 'selected' : ''}>No</option>
          </select>
        </div>
        <div class="form-group">
          <label>Superuser</label>
          <select name="is_superuser">
            <option value="false" ${!user.is_superuser ? 'selected' : ''}>No</option>
            <option value="true" ${user.is_superuser ? 'selected' : ''}>Yes</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Roles</label>
        <div class="checkbox-list">
          ${roles.map(r => `<div class="checkbox-item">
            <input type="checkbox" id="erole_${r.id}" name="role_ids" value="${r.id}" ${userRoleIds.includes(r.id) ? 'checked' : ''}>
            <label for="erole_${r.id}">${esc(r.name)}</label>
          </div>`).join('')}
        </div>
      </div>
      <hr>
      <div class="form-group">
        <label>Reset Password (leave blank to keep current)</label>
        <input type="password" name="new_password" placeholder="New password">
      </div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitEditUser()">Save Changes</button>`;
  openModal('Edit User', body, footer);
};
```

- [ ] **Step 4: Add `submitEditUser` function**

Add immediately after `openEditUserModal`:

```javascript
window.submitEditUser = async function() {
  const form = document.getElementById('editUserForm');
  const userId = form.user_id.value;
  const data = {
    full_name: form.full_name.value || null,
    is_active: form.is_active.value === 'true',
    is_superuser: form.is_superuser.value === 'true',
    role_ids: [...form.querySelectorAll('input[name="role_ids"]:checked')].map(c => parseInt(c.value)),
  };
  try {
    await api('PUT', `/auth/users/${userId}`, data);
    const newPw = form.new_password.value;
    if (newPw) {
      await api('PATCH', `/auth/users/${userId}/password`, { new_password: newPw });
    }
    closeModal();
    toast('User updated', 'success');
    renderUsers();
  } catch (err) {
    toast(err.message, 'error');
  }
};
```

- [ ] **Step 5: Add `deleteUser` function**

Add immediately after `submitEditUser`:

```javascript
window.deleteUser = async function(userId, username) {
  if (!confirm(`Deactivate user "${username}"? The user will no longer be able to log in.`)) return;
  try {
    await api('DELETE', `/auth/users/${userId}`);
    toast('User deactivated', 'success');
    renderUsers();
  } catch (err) {
    toast(err.message, 'error');
  }
};
```

- [ ] **Step 6: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat: add edit/delete UI for users in admin panel"
```

---

### Task 6: Add Actions Column and Edit/Delete to Roles Table in Admin UI

**Files:**
- Modify: `app/admin/js/app.js`

- [ ] **Step 1: Add Actions column to Roles table header**

In `renderRoles()`, change the table header (line 427):

```javascript
// Old:
<th>ID</th><th>Name</th><th>Description</th><th>Permissions</th>

// New:
<th>ID</th><th>Name</th><th>Description</th><th>Permissions</th><th>Actions</th>
```

- [ ] **Step 2: Add Edit/Delete buttons to each role row**

In `renderRoles()`, change the table body rows. Add an Actions cell before `</tr>`:

```javascript
<td>${(r.permissions || []).map(p => `<span class="tag">${esc(p.resource)}:${esc(p.action)}</span>`).join(' ') || '—'}</td>
<td>
  <button class="btn btn-xs btn-secondary" onclick="openEditRoleModal(${r.id})">Edit</button>
  <button class="btn btn-xs btn-danger" onclick="deleteRole(${r.id}, '${esc(r.name)}')">Delete</button>
</td>
</tr>
```

- [ ] **Step 3: Add `openEditRoleModal` function**

Add after the existing `window.submitRole` function (after line 480):

```javascript
window.openEditRoleModal = async function(roleId) {
  const allPerms = window._cachedPerms || [];
  let role;
  try {
    const roles = await api('GET', '/auth/roles');
    role = roles.find(r => r.id === roleId);
    if (!role) { toast('Role not found', 'error'); return; }
  } catch (err) { toast(err.message, 'error'); return; }

  const rolePermIds = (role.permissions || []).map(p => p.id);
  const body = `
    <form id="editRoleForm">
      <input type="hidden" name="role_id" value="${role.id}">
      <div class="form-group"><label>Role Name</label><input type="text" name="name" value="${esc(role.name)}" required></div>
      <div class="form-group"><label>Description</label><textarea name="description" rows="2">${esc(role.description || '')}</textarea></div>
      <div class="form-group">
        <label>Permissions</label>
        <div class="checkbox-list">
          ${allPerms.map(p => `<div class="checkbox-item">
            <input type="checkbox" id="eperm_${p.id}" name="perm_ids" value="${p.id}" ${rolePermIds.includes(p.id) ? 'checked' : ''}>
            <label for="eperm_${p.id}">${esc(p.resource)}:${esc(p.action)}</label>
          </div>`).join('')}
        </div>
      </div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitEditRole()">Save Changes</button>`;
  openModal('Edit Role', body, footer);
};
```

- [ ] **Step 4: Add `submitEditRole` and `deleteRole` functions**

Add immediately after `openEditRoleModal`:

```javascript
window.submitEditRole = async function() {
  const form = document.getElementById('editRoleForm');
  const roleId = form.role_id.value;
  const data = {
    name: form.name.value,
    description: form.description.value || null,
    permission_ids: [...form.querySelectorAll('input[name="perm_ids"]:checked')].map(c => parseInt(c.value)),
  };
  try {
    await api('PUT', `/auth/roles/${roleId}`, data);
    closeModal();
    toast('Role updated', 'success');
    renderRoles();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.deleteRole = async function(roleId, roleName) {
  if (!confirm(`Delete role "${roleName}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `/auth/roles/${roleId}`);
    toast('Role deleted', 'success');
    renderRoles();
  } catch (err) {
    toast(err.message, 'error');
  }
};
```

- [ ] **Step 5: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat: add edit/delete UI for roles in admin panel"
```

---

### Task 7: Add Actions Column and Edit/Delete to Permissions Table in Admin UI

**Files:**
- Modify: `app/admin/js/app.js`

- [ ] **Step 1: Add Actions column to Permissions table header**

In `renderPermissions()`, change the table header (line 497):

```javascript
// Old:
<th>ID</th><th>Resource</th><th>Action</th><th>Description</th>

// New:
<th>ID</th><th>Resource</th><th>Action</th><th>Description</th><th>Actions</th>
```

- [ ] **Step 2: Add Edit/Delete buttons to each permission row**

In `renderPermissions()`, change the table body rows. Add an Actions cell before `</tr>`:

```javascript
<td>${esc(p.description || '—')}</td>
<td>
  <button class="btn btn-xs btn-secondary" onclick="openEditPermModal(${p.id})">Edit</button>
  <button class="btn btn-xs btn-danger" onclick="deletePerm(${p.id}, '${esc(p.resource)}')">Delete</button>
</td>
</tr>
```

- [ ] **Step 3: Add `openEditPermModal`, `submitEditPerm`, and `deletePerm` functions**

Add after the existing `window.submitPerm` function (after line 551):

```javascript
window.openEditPermModal = async function(permId) {
  let perm;
  try {
    const perms = await api('GET', '/auth/permissions');
    perm = perms.find(p => p.id === permId);
    if (!perm) { toast('Permission not found', 'error'); return; }
  } catch (err) { toast(err.message, 'error'); return; }

  const body = `
    <form id="editPermForm">
      <input type="hidden" name="perm_id" value="${perm.id}">
      <div class="form-group">
        <label>Resource</label>
        <input type="text" name="resource" value="${esc(perm.resource)}" required>
        <div class="hint">Format: type:name (supports * wildcard)</div>
      </div>
      <div class="form-group">
        <label>Action</label>
        <select name="action">
          <option value="read" ${perm.action === 'read' ? 'selected' : ''}>read</option>
          <option value="execute" ${perm.action === 'execute' ? 'selected' : ''}>execute</option>
          <option value="write" ${perm.action === 'write' ? 'selected' : ''}>write</option>
        </select>
      </div>
      <div class="form-group"><label>Description</label><textarea name="description" rows="2">${esc(perm.description || '')}</textarea></div>
    </form>
  `;
  const footer = `<button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                   <button class="btn btn-primary" onclick="submitEditPerm()">Save Changes</button>`;
  openModal('Edit Permission', body, footer);
};

window.submitEditPerm = async function() {
  const form = document.getElementById('editPermForm');
  const permId = form.perm_id.value;
  const data = {
    resource: form.resource.value,
    action: form.action.value,
    description: form.description.value || null,
  };
  try {
    await api('PUT', `/auth/permissions/${permId}`, data);
    closeModal();
    toast('Permission updated', 'success');
    renderPermissions();
  } catch (err) {
    toast(err.message, 'error');
  }
};

window.deletePerm = async function(permId, resource) {
  if (!confirm(`Delete permission "${resource}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `/auth/permissions/${permId}`);
    toast('Permission deleted', 'success');
    renderPermissions();
  } catch (err) {
    toast(err.message, 'error');
  }
};
```

- [ ] **Step 4: Commit**

```bash
git add app/admin/js/app.js
git commit -m "feat: add edit/delete UI for permissions in admin panel"
```

---

### Task 8: Add `btn-xs` and `btn-danger` CSS Styles

**Files:**
- Modify: `app/admin/css/style.css` (or inline in `app/admin/index.html` if styles are there)

- [ ] **Step 1: Check where CSS styles live**

Look for existing `.btn` styles in the admin CSS. The new buttons need `btn-xs` (small size) and `btn-danger` (red for delete).

- [ ] **Step 2: Add the missing button styles**

Add to the existing CSS file, near the other `.btn` styles:

```css
.btn-xs {
  padding: 2px 8px;
  font-size: 0.75rem;
}

.btn-danger {
  background: #dc3545;
  color: #fff;
  border: none;
}

.btn-danger:hover {
  background: #c82333;
}
```

- [ ] **Step 3: Commit**

```bash
git add app/admin/css/style.css
git commit -m "feat: add btn-xs and btn-danger button styles for admin UI"
```

---

### Task 9: Verify All Endpoints via Manual Smoke Test

- [ ] **Step 1: Start the application**

Run: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

- [ ] **Step 2: Verify new endpoints appear in OpenAPI docs**

Open `http://localhost:8000/docs` and confirm these endpoints exist:
- `PUT /api/v1/auth/users/{user_id}`
- `DELETE /api/v1/auth/users/{user_id}`
- `PATCH /api/v1/auth/users/{user_id}/password`
- `PUT /api/v1/auth/roles/{role_id}`
- `DELETE /api/v1/auth/roles/{role_id}`
- `PUT /api/v1/auth/permissions/{permission_id}`
- `DELETE /api/v1/auth/permissions/{permission_id}`

- [ ] **Step 3: Verify Admin UI shows Edit/Delete buttons**

Open `http://localhost:8000/admin` and verify:
- Users table has Actions column with Edit/Delete buttons
- Roles table has Actions column with Edit/Delete buttons
- Permissions table has Actions column with Edit/Delete buttons
- Edit modals open with pre-populated data
- Delete shows confirmation dialog

- [ ] **Step 4: Run lint**

Run: `ruff check app/api/v1/auth.py app/schemas/auth.py`
Expected: no errors

- [ ] **Step 5: Run existing tests to ensure no regressions**

Run: `pytest -v`
Expected: all existing tests pass
