"""Authentication service: user lookup, token validation, permission checks."""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_password
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/token")


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def get_current_active_superuser(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser privileges required")
    return current_user


class PermissionChecker:
    """Dependency that checks if the current user has a specific permission.

    Usage:
        @router.post("/query", dependencies=[Depends(PermissionChecker("query:employees", "read"))])
    """

    def __init__(self, resource: str, action: str):
        self.resource = resource
        self.action = action

    def __call__(self, current_user: User = Depends(get_current_user)):
        if current_user.is_superuser:
            return current_user

        for role in current_user.roles:
            for perm in role.permissions:
                if perm.resource == self.resource and perm.action == self.action:
                    return current_user
                # Wildcard: "query:*" + "read" matches any query resource
                if perm.resource.endswith(":*"):
                    prefix = perm.resource[:-1]  # "query:"
                    if self.resource.startswith(prefix) and perm.action == self.action:
                        return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {self.action} on {self.resource}",
        )
