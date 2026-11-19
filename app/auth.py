from dataclasses import dataclass
from typing import Optional
from fastapi import Header, HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import User, Organization, OrgMembership

@dataclass
class AuthContext:
    user_id: str
    org_id: str
    role: str
    user: User
    organization: Organization

async def get_current_auth(
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_role: Optional[str] = Header(None, alias="X-Role"),
    db: AsyncSession = Depends(get_db)
) -> AuthContext:
    if not x_user_id or not x_org_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTHENTICATION_REQUIRED",
                "message": "Missing required authentication headers: X-User-Id and X-Org-Id"
            }
        )

    # Verify user exists
    user_stmt = select(User).where(User.id == x_user_id)
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "USER_NOT_FOUND",
                "message": f"User '{x_user_id}' does not exist"
            }
        )

    # Verify organization exists
    org_stmt = select(Organization).where(Organization.id == x_org_id)
    org_res = await db.execute(org_stmt)
    org = org_res.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ORGANIZATION_NOT_FOUND",
                "message": f"Organization '{x_org_id}' does not exist"
            }
        )

    # Verify membership and role
    mem_stmt = select(OrgMembership).where(
        OrgMembership.org_id == x_org_id,
        OrgMembership.user_id == x_user_id
    )
    mem_res = await db.execute(mem_stmt)
    membership = mem_res.scalar_one_or_none()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": f"User '{x_user_id}' is not a member of organization '{x_org_id}'"
            }
        )

    # If X-Role header provided, ensure it matches DB or user is allowed to act as it
    effective_role = membership.role
    if x_role and x_role.upper() != membership.role:
        # Cannot escalate privileges
        if x_role.upper() == "ORGANIZER" and membership.role != "ORGANIZER":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ROLE_ESCALATION_DENIED",
                    "message": "Cannot assume ORGANIZER role without organization membership authorization"
                }
            )

    return AuthContext(
        user_id=user.id,
        org_id=org.id,
        role=effective_role,
        user=user,
        organization=org
    )

async def require_organizer(
    auth: AuthContext = Depends(get_current_auth)
) -> AuthContext:
    if auth.role != "ORGANIZER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ORGANIZER_REQUIRED",
                "message": "This operation requires ORGANIZER privileges"
            }
        )
    return auth

async def require_participant(
    auth: AuthContext = Depends(get_current_auth)
) -> AuthContext:
    # Both PARTICIPANT and ORGANIZER can act as participants
    return auth
