from uuid import UUID

import pytest
from app.services.dashboard.sql_dashboard_statements import DASHBOARD_METRICS
from app.services.dashboard.sql_read_statements import POST_COUNT, POST_PAGE
from app.services.dashboard.sql_rows import CountRow, MetricRow
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.dashboard_search_postgres_fixture import (
    INDEX_NAME,
    PostgresExplainRow,
    search_database_url,
    search_parameters,
    seed_search_database,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search", "expected"),
    [
        pytest.param("eNgLiSh TiTlE", 1, id="ascii-title"),
        pytest.param("english body", 1, id="ascii-body"),
        pytest.param("예측시장", 1, id="korean-title"),
        pytest.param("한국어", 1, id="korean-body"),
        pytest.param("Café TITLE", 1, id="nfc-title"),
        pytest.param("Café BODY", 1, id="nfc-body"),
        pytest.param("Astral TITLE😀", 1, id="astral-title"),
        pytest.param("Astral BODY😀", 1, id="astral-body"),
        pytest.param("ÉtItLe", 1, id="upper-accent-title"),
        pytest.param("étItLe", 1, id="lower-accent-title"),
        pytest.param("ÉbOdY", 1, id="upper-accent-body"),
        pytest.param("ébOdY", 1, id="lower-accent-body"),
        pytest.param("%_\\", 1, id="literal-wildcards-body"),
    ],
)
async def test_postgres_search_has_count_page_and_metric_parity(
    search: str, expected: int
) -> None:
    # Given: PG17 stores title/body text through the exact 0010 generated column.
    engine = create_async_engine(search_database_url())
    try:
        async with engine.begin() as connection:
            await seed_search_database(connection)
            parameters = search_parameters(search)

            # When: all three production SQL surfaces receive one bound pattern.
            count = CountRow.model_validate(
                (await connection.execute(POST_COUNT, parameters)).mappings().one()
            )
            page = (await connection.execute(POST_PAGE, parameters)).mappings().all()
            metrics = MetricRow.model_validate(
                (
                    await connection.execute(DASHBOARD_METRICS, parameters)
                ).mappings().one()
            )

            # Then: title/body literal results agree without predicate drift.
            assert count.total_items == expected
            assert len(page) == expected
            assert metrics.current_count == expected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_search_and_keyword_are_anded_with_stable_pages() -> None:
    # Given: 55 indexed rows and three independently reviewed keyword matches.
    engine = create_async_engine(search_database_url())
    try:
        async with engine.begin() as connection:
            await seed_search_database(connection)
            first_parameters = search_parameters(
                "indexed market", keyword="target-rule"
            )
            first_parameters["page_size"] = 2
            second_parameters = {**first_parameters, "page_offset": 2}

            # When: the combined filter is read over two deterministic pages.
            total = CountRow.model_validate(
                (
                    await connection.execute(POST_COUNT, first_parameters)
                ).mappings().one()
            )
            first = (
                await connection.execute(POST_PAGE, first_parameters)
            ).mappings().all()
            second = (
                await connection.execute(POST_PAGE, second_parameters)
            ).mappings().all()
            metrics = MetricRow.model_validate(
                (
                    await connection.execute(DASHBOARD_METRICS, first_parameters)
                ).mappings().one()
            )

            # Then: AND parity holds and page membership never overlaps.
            assert total.total_items == 3
            assert metrics.current_count == 3
            assert {row["id"] for row in first}.isdisjoint(
                {row["id"] for row in second}
            )
            assert len(first) == 2
            assert len(second) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_keyword_alone_keeps_existing_results() -> None:
    # Given: the existing keyword rule matches the first three seeded versions.
    engine = create_async_engine(search_database_url())
    try:
        async with engine.begin() as connection:
            await seed_search_database(connection)
            parameters = search_parameters(None, keyword="target-rule")

            # When: keyword filtering runs without the new general-search filter.
            total = CountRow.model_validate(
                (await connection.execute(POST_COUNT, parameters)).mappings().one()
            )
            page = (await connection.execute(POST_PAGE, parameters)).mappings().all()
            metrics = MetricRow.model_validate(
                (
                    await connection.execute(DASHBOARD_METRICS, parameters)
                ).mappings().one()
            )

            # Then: the pre-search result identities and counts remain unchanged.
            assert tuple(row["id"] for row in page) == (
                UUID(int=1),
                UUID(int=2),
                UUID(int=3),
            )
            assert total.total_items == 3
            assert metrics.current_count == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_search_uses_the_0010_trigram_index() -> None:
    # Given: the exact generated column and GIN expression index are populated.
    engine = create_async_engine(search_database_url())
    try:
        async with engine.begin() as connection:
            await seed_search_database(connection)
            _ = await connection.execute(text("SET LOCAL enable_seqscan = off"))

            # When: PostgreSQL plans the same indexed literal predicate.
            plan = PostgresExplainRow.model_validate(
                (
                    await connection.execute(
                        text(
                            """
                            EXPLAIN (FORMAT JSON)
                            SELECT id FROM post_versions pv
                            WHERE pv.search_text COLLATE "C"
                                LIKE :search_pattern ESCAPE '\\'
                            """
                        ),
                        search_parameters("indexed market"),
                    )
                )
                .mappings()
                .one()
            )

            # Then: PG17 names the compatibility migration's trigram index.
            assert plan.plan[0].plan.plans[0].index_name == INDEX_NAME
    finally:
        await engine.dispose()
