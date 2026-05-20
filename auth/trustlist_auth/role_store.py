# Copyright 2026 The TrustList Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Persistence of RBAC role assignments — the ``user_role_assignment`` table.

:mod:`trustlist_auth.rbac` is the pure RBAC *framework* — permissions, roles,
catalogues. This module is its *persistence* half: it reads and writes
``user_role_assignment`` rows, the canonical record of who holds which role.

The split matters. The framework answers "do these roles grant permission X?"
with no I/O and is trivially unit-testable. This store answers "which roles
does this user currently hold?" against a real database. :class:`AuthService`
joins the two.

Scoping discipline mirrors the database CHECK constraint on
``user_role_assignment``: a brand-customer grant *must* carry a ``customer_id``;
a non-brand-customer grant must *not*. :meth:`RoleStore.grant_role` enforces
this in application code before the INSERT (belt-and-braces per PRD §7a), so a
violation is a clean :class:`RoleScopeError` rather than an opaque
``IntegrityError`` from Postgres.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from trustlist_auth.errors import RoleScopeError, UnknownRole
from trustlist_auth.rbac import Archetype, catalogue_for


@dataclass(frozen=True)
class RoleAssignment:
    """One row of ``user_role_assignment`` — a role grant.

    :param user_id: the account the role is granted to.
    :param account_archetype: the account's archetype (denormalised onto the
        row so the database CHECK can consult it — see the model docstring).
    :param role: the role identifier; validated against the archetype's
        catalogue.
    :param granted_at: when the grant was made.
    :param granted_by_user_id: who made the grant, or ``None`` for a
        system/bootstrap grant.
    :param revoked_at: when the grant was revoked, or ``None`` if it is live.
    :param customer_id: the per-customer scope for brand-customer grants;
        ``None`` for the other archetypes.
    """

    user_id: str
    account_archetype: Archetype
    role: str
    granted_at: datetime.datetime
    granted_by_user_id: str | None
    revoked_at: datetime.datetime | None
    customer_id: str | None

    @property
    def is_live(self) -> bool:
        """Return ``True`` iff the grant has not been revoked."""
        return self.revoked_at is None


class RoleStore:
    """Reads and writes ``user_role_assignment`` rows.

    Stateless beyond the table it targets; each method takes the SQLAlchemy
    connection of the surrounding unit of work, so a grant or revoke commits in
    the same transaction as its audit event.
    """

    def grant_role(
        self,
        connection: Connection,
        *,
        user_id: str,
        archetype: Archetype,
        role: str,
        granted_by_user_id: str | None,
        customer_id: str | None = None,
        granted_at: datetime.datetime | None = None,
    ) -> RoleAssignment:
        """Grant ``role`` to ``user_id`` and return the assignment row.

        The role is validated against the archetype's catalogue, and the
        customer-id scoping rule is enforced before the INSERT.

        :raises UnknownRole: if ``role`` is not in the archetype's catalogue.
        :raises RoleScopeError: if a brand-customer grant has no ``customer_id``
            or a non-brand-customer grant carries one.
        """
        catalogue = catalogue_for(archetype)
        if not catalogue.has(role):
            raise UnknownRole(
                f"{role!r} is not a role in the {archetype} catalogue"
            )
        self._check_customer_scope(archetype, customer_id)

        moment = granted_at or datetime.datetime.now(tz=datetime.UTC)
        connection.execute(
            text(
                "INSERT INTO user_role_assignment "
                "(user_id, account_archetype, role, granted_at, "
                " granted_by_user_id, customer_id) "
                "VALUES (:user_id, :archetype, :role, :granted_at, "
                "        :granted_by, :customer_id)"
            ),
            {
                "user_id": user_id,
                "archetype": archetype.value,
                "role": role,
                "granted_at": moment,
                "granted_by": granted_by_user_id,
                "customer_id": customer_id,
            },
        )
        return RoleAssignment(
            user_id=user_id,
            account_archetype=archetype,
            role=role,
            granted_at=moment,
            granted_by_user_id=granted_by_user_id,
            revoked_at=None,
            customer_id=customer_id,
        )

    def revoke_role(
        self,
        connection: Connection,
        *,
        user_id: str,
        role: str,
        granted_at: datetime.datetime,
        revoked_at: datetime.datetime | None = None,
    ) -> bool:
        """Revoke a specific live role grant.

        A grant is identified by its full primary key — ``(user_id, role,
        granted_at)`` — because a user may hold and lose the same role more
        than once, each grant a distinct row.

        :returns: ``True`` if a live grant was revoked, ``False`` if none
            matched (already revoked, or no such grant).
        """
        moment = revoked_at or datetime.datetime.now(tz=datetime.UTC)
        result = connection.execute(
            text(
                "UPDATE user_role_assignment SET revoked_at = :revoked_at "
                "WHERE user_id = :user_id AND role = :role "
                "AND granted_at = :granted_at AND revoked_at IS NULL"
            ),
            {
                "revoked_at": moment,
                "user_id": user_id,
                "role": role,
                "granted_at": granted_at,
            },
        )
        return result.rowcount == 1

    def live_assignments(
        self,
        connection: Connection,
        user_id: str,
        *,
        customer_id: str | None = None,
    ) -> tuple[RoleAssignment, ...]:
        """Return every live (un-revoked) role assignment for ``user_id``.

        :param customer_id: when supplied, restricts the result to grants
            scoped to that customer — the per-customer isolation filter for the
            brand-customer archetype. When ``None``, every live grant is
            returned.
        """
        sql = (
            "SELECT user_id, account_archetype, role, granted_at, "
            "       granted_by_user_id, revoked_at, customer_id "
            "FROM user_role_assignment "
            "WHERE user_id = :user_id AND revoked_at IS NULL"
        )
        params: dict[str, object] = {"user_id": user_id}
        if customer_id is not None:
            sql += " AND customer_id = :customer_id"
            params["customer_id"] = customer_id

        rows = connection.execute(text(sql), params).all()
        return tuple(
            RoleAssignment(
                user_id=str(row.user_id),
                account_archetype=Archetype(row.account_archetype),
                role=row.role,
                granted_at=row.granted_at,
                granted_by_user_id=(
                    str(row.granted_by_user_id)
                    if row.granted_by_user_id is not None
                    else None
                ),
                revoked_at=row.revoked_at,
                customer_id=(
                    str(row.customer_id) if row.customer_id is not None else None
                ),
            )
            for row in rows
        )

    def live_role_identifiers(
        self,
        connection: Connection,
        user_id: str,
        *,
        customer_id: str | None = None,
    ) -> frozenset[str]:
        """Return the identifiers of every live role ``user_id`` holds."""
        return frozenset(
            assignment.role
            for assignment in self.live_assignments(
                connection, user_id, customer_id=customer_id
            )
        )

    @staticmethod
    def _check_customer_scope(
        archetype: Archetype, customer_id: str | None
    ) -> None:
        """Enforce the customer-id scoping rule before an INSERT.

        Mirrors the ``user_role_assignment`` CHECK constraint so a violation
        surfaces as a clean :class:`RoleScopeError` rather than a database
        ``IntegrityError``.
        """
        if archetype is Archetype.BRAND_CUSTOMER and customer_id is None:
            raise RoleScopeError(
                "a brand-customer role grant requires a customer_id"
            )
        if archetype is not Archetype.BRAND_CUSTOMER and customer_id is not None:
            raise RoleScopeError(
                f"a {archetype} role grant must not carry a customer_id"
            )
