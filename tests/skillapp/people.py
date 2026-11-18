from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.models import User
from django.test import RequestFactory

from tests.skillapp.models import Member, Tenant, TenantSkill


def member(username: str, tenant: Tenant, *, admin: bool = False) -> User:
    user = User.objects.create_user(username)
    Member.objects.create(user=user, tenant=tenant, is_admin=admin)
    return user


def skill(tenant: Tenant, name: str, owner: User | None = None, **fields: Any) -> TenantSkill:
    fields.setdefault("description", f"When {name} applies.")
    fields.setdefault("body", f"The secret steps of {name}.")
    return TenantSkill.objects.create(tenant=tenant, owner=owner, name=name, **fields)


def request_as(user: User, method: str = "get", body: Any = None, path: str = "/s/") -> Any:
    data = {} if body is None else {"data": json.dumps(body), "content_type": "application/json"}
    request = getattr(RequestFactory(), method)(path, **data)
    request.user = user
    return request
