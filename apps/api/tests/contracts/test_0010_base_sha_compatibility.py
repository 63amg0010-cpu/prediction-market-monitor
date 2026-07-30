from pathlib import Path

from app.db.post_models import PostVersion

API_ROOT = Path(__file__).parents[2]
MIGRATION = API_ROOT / "migrations" / "versions" / (
    "20260727_0010_manifold_search_compatibility.py"
)


def test_0010_does_not_seed_rows_that_require_new_application_enums() -> None:
    # Given: compatibility depends on old readers seeing no newly valued rows.
    migration_source = MIGRATION.read_text(encoding="utf-8")

    # When/Then: 0010 changes enum/schema capabilities without seeding source rows.
    assert "INSERT INTO community_sources" not in migration_source
    assert "INSERT INTO collection_runs" not in migration_source


def test_current_post_model_maps_search_as_server_generated() -> None:
    # Given: PostgreSQL owns the generated search column added by 0010.
    # When/Then: current readers can select it without backdating table metadata.
    assert PostVersion.search_text.key == "search_text"
    assert "search_text" not in PostVersion.__table__.c


def test_post_model_remains_author_free() -> None:
    # Given: the complete immutable post-version persistence shape.
    column_names = set(PostVersion.__table__.c.keys())

    # When/Then: compatibility search adds no provider identity or raw-author fields.
    assert "search_text" not in column_names
    assert not column_names.intersection(
        {"author", "author_id", "profile", "profile_url", "raw", "raw_json"}
    )
