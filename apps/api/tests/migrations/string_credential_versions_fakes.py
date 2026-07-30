from collections.abc import Sequence
from types import SimpleNamespace
from typing import Literal, Protocol, final

import pytest

ConstraintSemantic = Literal["positive", "nonblank"]


class CatalogCheckConstraintLike(Protocol):
    name: str
    expression: str
    columns: frozenset[str]


class CatalogCheckConstraintFactory(Protocol):
    def __call__(
        self,
        *,
        name: str,
        expression: str,
        columns: frozenset[str],
    ) -> CatalogCheckConstraintLike: ...


class MigrationModule(Protocol):
    op: object
    CatalogCheckConstraint: CatalogCheckConstraintFactory
    AmbiguousVersionConstraintError: type[RuntimeError]

    def resolve_version_check_constraint(
        self,
        constraints: Sequence[CatalogCheckConstraintLike],
        *,
        semantic: ConstraintSemantic,
    ) -> str | None: ...

    def upgrade(self) -> None: ...

    def downgrade(self) -> None: ...


@final
class OperationRecorder:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        migration: MigrationModule,
    ) -> None:
        self._monkeypatch: pytest.MonkeyPatch = monkeypatch
        self._migration: MigrationModule = migration
        self.calls: list[tuple[object, ...]] = []

    def configure_online(
        self,
        *,
        column_type: object,
        constraints: Sequence[CatalogCheckConstraintLike],
    ) -> None:
        bind = SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql"),
        )

        def get_context() -> SimpleNamespace:
            return SimpleNamespace(as_sql=False)

        def get_bind() -> object:
            return bind

        def version_column_type(_: object) -> object:
            return column_type

        def catalog_check_constraints(
            _: object,
        ) -> tuple[CatalogCheckConstraintLike, ...]:
            return tuple(constraints)

        def formatted_name(name: str) -> str:
            return name

        def drop_constraint(
            name: object,
            table: str,
            type_: str,
        ) -> None:
            self.calls.append(("drop", name, table, type_))

        def alter_column(
            table: str,
            column: str,
            **kwargs: object,
        ) -> None:
            self.calls.append(("alter", table, column, kwargs))

        def create_check_constraint(
            name: object,
            table: str,
            expression: str,
        ) -> None:
            self.calls.append(("create", name, table, expression))

        self._monkeypatch.setattr(
            self._migration.op,
            "get_context",
            get_context,
        )
        self._monkeypatch.setattr(
            self._migration.op,
            "get_bind",
            get_bind,
        )
        self._monkeypatch.setattr(
            self._migration,
            "_version_column_type",
            version_column_type,
        )
        self._monkeypatch.setattr(
            self._migration,
            "_catalog_check_constraints",
            catalog_check_constraints,
        )
        self._monkeypatch.setattr(
            self._migration.op,
            "f",
            formatted_name,
        )
        self._monkeypatch.setattr(
            self._migration.op,
            "drop_constraint",
            drop_constraint,
        )
        self._monkeypatch.setattr(
            self._migration.op,
            "alter_column",
            alter_column,
        )
        self._monkeypatch.setattr(
            self._migration.op,
            "create_check_constraint",
            create_check_constraint,
        )
