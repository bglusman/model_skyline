from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from model_skyline import publisher
from model_skyline.io import load_frontier_history, load_publication_manifest
from model_skyline.models import ObservationCatalog, ProjectConfig, PublishedFile
from model_skyline.publisher import PublicationError, publication_hash, publish_project
from model_skyline.resolver import DynamicResolver

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)
RSS_NAMESPACE = "urn:model-skyline:rss:1.0"


def _root(tmp_path: Path) -> Path:
    # macOS exposes its temporary tree through /var -> /private/var. The
    # publisher deliberately rejects symlink path components, so tests pass the
    # canonical temporary parent just as a hardened caller should.
    return tmp_path.resolve() / "site"


def _publish(
    root: Path,
    config: ProjectConfig,
    catalog: ObservationCatalog,
    *,
    at: datetime = NOW,
):
    return publish_project(
        config,
        [catalog],
        root,
        project_id="coding-demo",
        generated_at=at,
        base_url="https://example.test/model-skyline",
    )


def _references(result: object) -> tuple[PublishedFile, ...]:
    manifest = result.manifest  # type: ignore[attr-defined]
    files: list[PublishedFile] = []
    for frontier in manifest.frontiers:
        files.extend(
            (
                frontier.snapshot,
                frontier.csv,
                frontier.table,
                frontier.history,
                frontier.feed,
            )
        )
    for selection in manifest.selections:
        files.append(selection.snapshot)
    return tuple(files)


def test_publishes_multi_frontier_site_and_resolvable_selection(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)

    result = _publish(root, example_config, example_catalog)

    assert result.changed
    assert [entry.frontier_id for entry in result.manifest.frontiers] == [
        "coding-responsiveness",
        "coding-value",
    ]
    assert [entry.selection_id for entry in result.manifest.selections] == ["coding-agent-defaults"]
    loaded = load_publication_manifest(root / "latest.json")
    assert loaded == result.manifest
    assert publication_hash(loaded) == loaded.publication_id
    assert (root / "latest.json").read_bytes() == (
        root / "publications" / f"{loaded.publication_id}.json"
    ).read_bytes()
    for reference in _references(result):
        payload = (root / reference.path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == reference.sha256

    resolver = DynamicResolver(
        root / "selections" / "coding-agent-defaults" / "latest.json",
        expected_selection_id="coding-agent-defaults",
        expected_frontier_id="coding-value",
        expected_workload_id="coding-session-v1",
        expected_workload_version="1.0.0",
        allow_local_file=True,
        clock=lambda: NOW,
    )
    selection = resolver.resolve()
    assert selection.frontier_snapshot_id == next(
        item.snapshot_id for item in loaded.frontiers if item.frontier_id == "coding-value"
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
def test_new_publication_defaults_to_owner_only_permissions(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)

    result = _publish(root, example_config, example_catalog)

    assert stat.S_IMODE(root.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE((root / "latest.json").stat().st_mode) & 0o077 == 0
    snapshot = root / result.manifest.frontiers[0].snapshot.path
    assert stat.S_IMODE(snapshot.stat().st_mode) & 0o077 == 0


def test_fixed_clock_rerun_is_idempotent_and_repairs_mutable_aliases(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    first = _publish(root, example_config, example_catalog)
    table = root / "frontiers" / "coding-value" / "table.txt"
    table.write_text("damaged but regular\n", encoding="utf-8")

    second = _publish(root, example_config, example_catalog)

    assert not second.changed
    assert second.manifest.publication_id == first.manifest.publication_id
    assert "damaged" not in table.read_text(encoding="utf-8")
    assert len(list((root / "publications").glob("*.json"))) == 1


def test_new_snapshot_without_view_change_extends_history_but_not_feed(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    first = _publish(root, example_config, example_catalog)

    second = _publish(root, example_config, example_catalog, at=NOW + timedelta(minutes=10))

    assert second.changed
    assert second.manifest.previous_publication_id == first.manifest.publication_id
    for reference in _references(first):
        assert hashlib.sha256((root / reference.path).read_bytes()).hexdigest() == (
            reference.sha256
        )
    history = load_frontier_history(root / "frontiers" / "coding-value" / "history.json")
    assert len(history.entries) == 2
    feed = ET.parse(root / "feeds" / "coding-value.xml")
    assert len(feed.findall("./channel/item")) == 1


def test_feed_reports_value_change_and_policy_baseline_reset(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    _publish(root, example_config, example_catalog)
    changed_catalog = example_catalog.model_copy(deep=True)
    first = changed_catalog.offerings[0]
    changed_signal = first.signals["success_rate"].model_copy(update={"value": Decimal("0.63")})
    changed_catalog.offerings[0] = first.model_copy(
        update={"signals": {**first.signals, "success_rate": changed_signal}}
    )
    _publish(root, example_config, changed_catalog, at=NOW + timedelta(minutes=10))

    feed = ET.parse(root / "feeds" / "coding-value.xml")
    items = feed.findall("./channel/item")
    assert len(items) == 2
    assert items[0].findtext(f"{{{RSS_NAMESPACE}}}baselineReset") == "false"
    assert items[0].findtext(f"{{{RSS_NAMESPACE}}}valueChange") == (
        "fastcloud/quick-small@us-standard"
    )

    changed_config = example_config.model_copy(deep=True)
    changed_config.metrics["coding_session_success"].description = "policy revision"
    _publish(root, changed_config, changed_catalog, at=NOW + timedelta(minutes=20))
    feed = ET.parse(root / "feeds" / "coding-value.xml")
    newest = feed.find("./channel/item")
    assert newest is not None
    assert newest.findtext(f"{{{RSS_NAMESPACE}}}baselineReset") == "true"


def test_public_mode_requires_explicit_source_license_authorization(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    with pytest.raises(PublicationError, match="not authorized"):
        publish_project(
            example_config,
            [example_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW,
            public=True,
            base_url="https://example.test/model-skyline",
        )

    result = publish_project(
        example_config,
        [example_catalog],
        root,
        project_id="coding-demo",
        generated_at=NOW,
        public=True,
        base_url="https://example.test/model-skyline",
        allowed_licenses=["CC0-1.0"],
    )
    assert result.manifest.policy.public
    assert result.manifest.policy.allowed_licenses == ("CC0-1.0",)


def test_public_transition_authorizes_entire_retained_history(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    _publish(root, example_config, example_catalog)
    apache_catalog = example_catalog.model_copy(deep=True)
    for index, offering in enumerate(apache_catalog.offerings):
        assert offering.default_source is not None
        apache_catalog.offerings[index] = offering.model_copy(
            update={
                "default_source": offering.default_source.model_copy(
                    update={
                        "id": "operator-authorized-current-source",
                        "license": "Apache-2.0",
                    }
                )
            }
        )

    with pytest.raises(PublicationError, match="model-skyline-synthetic-coding-fixture"):
        publish_project(
            example_config,
            [apache_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW + timedelta(minutes=10),
            public=True,
            base_url="https://example.test/model-skyline",
            allowed_licenses=["Apache-2.0"],
        )

    result = publish_project(
        example_config,
        [apache_catalog],
        root,
        project_id="coding-demo",
        generated_at=NOW + timedelta(minutes=10),
        public=True,
        base_url="https://example.test/model-skyline",
        allowed_licenses=["Apache-2.0", "CC0-1.0"],
    )
    assert result.manifest.policy.public


def test_public_license_gate_checks_every_ancestor_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    _publish(root, example_config, example_catalog)
    apache_catalog = example_catalog.model_copy(deep=True)
    for index, offering in enumerate(apache_catalog.offerings):
        assert offering.default_source is not None
        apache_catalog.offerings[index] = offering.model_copy(
            update={
                "default_source": offering.default_source.model_copy(
                    update={
                        "id": "operator-authorized-current-source",
                        "license": "Apache-2.0",
                    }
                )
            }
        )
    _publish(root, example_config, apache_catalog, at=NOW + timedelta(minutes=10))

    # Simulate a corrupt/migrated current history that no longer exposes the
    # first publication. The immutable manifest chain remains authoritative.
    monkeypatch.setattr(publisher, "_load_committed_histories", lambda _root, _manifest: {})
    with pytest.raises(PublicationError, match="model-skyline-synthetic-coding-fixture"):
        publish_project(
            example_config,
            [apache_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW + timedelta(minutes=20),
            public=True,
            base_url="https://example.test/model-skyline",
            allowed_licenses=["Apache-2.0"],
        )


def test_rejects_unsafe_ids_symlinks_unmanaged_files_and_duplicate_catalogs(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    with pytest.raises(PublicationError, match="portable lowercase"):
        publish_project(
            example_config,
            [example_catalog],
            root,
            project_id="coding-demo",
            frontier_ids=["../../escape"],
            generated_at=NOW,
        )
    assert not (tmp_path.resolve() / "escape").exists()

    with pytest.raises(PublicationError, match="multiple catalogs"):
        publish_project(
            example_config,
            [example_catalog, example_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW,
        )

    real_parent = tmp_path.resolve() / "real"
    real_parent.mkdir()
    linked_parent = tmp_path.resolve() / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(PublicationError, match="symbolic link"):
        _publish(linked_parent / "site", example_config, example_catalog)

    root.mkdir()
    (root / "operator-notes.txt").write_text("keep\n", encoding="utf-8")
    coincidental_temp = root / ".model-skyline-operator.tmp"
    coincidental_temp.write_text("also keep\n", encoding="utf-8")
    with pytest.raises(PublicationError, match="unmanaged file"):
        _publish(root, example_config, example_catalog)
    assert (root / "operator-notes.txt").read_text(encoding="utf-8") == "keep\n"
    assert coincidental_temp.read_text(encoding="utf-8") == "also keep\n"


def test_writer_lock_and_immutable_corruption_fail_closed(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    root.mkdir()
    with (
        publisher._writer_lock(root),
        pytest.raises(PublicationError, match="lock is already held"),
    ):
        _publish(root, example_config, example_catalog)

    first = _publish(root, example_config, example_catalog)
    immutable = root / next(
        item.snapshot.path
        for item in first.manifest.frontiers
        if item.frontier_id == "coding-value"
    )
    immutable.write_text("{}\n", encoding="utf-8")
    with pytest.raises(PublicationError, match="digest mismatch|valid FrontierSnapshot"):
        _publish(root, example_config, example_catalog)


def test_owned_stale_temp_is_recovered_under_the_writer_lock(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    root.mkdir()
    stale = root / ".model-skyline-interrupted.tmp"
    stale.write_text("partial\n", encoding="utf-8")

    result = _publish(root, example_config, example_catalog)

    assert result.changed
    assert not stale.exists()


def test_tree_traversal_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    root.mkdir()

    def failed_walk(
        _root: object,
        *,
        followlinks: bool,
        onerror: object,
    ) -> object:
        assert not followlinks
        assert callable(onerror)
        onerror(PermissionError(13, "permission denied", root / "frontiers"))
        return iter(())

    monkeypatch.setattr(publisher.os, "walk", failed_walk)

    with pytest.raises(PublicationError, match="cannot traverse publication path"):
        _publish(root, example_config, example_catalog)


def test_failed_alias_phase_keeps_old_commit_marker_and_next_run_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    first = _publish(root, example_config, example_catalog)
    old_marker = (root / "latest.json").read_bytes()
    original = publisher._replace_mutable

    def fail_before_selection(root_value: Path, path: str, payload: bytes) -> None:
        if path == "selections/coding-agent-defaults/latest.json":
            raise PublicationError("injected alias failure")
        original(root_value, path, payload)

    monkeypatch.setattr(publisher, "_replace_mutable", fail_before_selection)
    with pytest.raises(PublicationError, match="injected alias failure"):
        _publish(root, example_config, example_catalog, at=NOW + timedelta(minutes=10))
    assert (root / "latest.json").read_bytes() == old_marker
    for reference in _references(first):
        assert hashlib.sha256((root / reference.path).read_bytes()).hexdigest() == (
            reference.sha256
        )

    monkeypatch.setattr(publisher, "_replace_mutable", original)
    recovered = _publish(
        root,
        example_config,
        example_catalog,
        at=NOW + timedelta(minutes=20),
    )
    assert recovered.changed
    assert recovered.manifest.previous_publication_id == first.manifest.publication_id
    assert (root / "latest.json").read_bytes() != old_marker
    history = load_frontier_history(root / "frontiers" / "coding-value" / "history.json")
    assert [entry.generated_at for entry in history.entries] == [
        NOW + timedelta(minutes=20),
        NOW,
    ]


def test_rejects_public_http_and_timestamp_rollback(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    with pytest.raises(PublicationError, match="HTTPS"):
        publish_project(
            example_config,
            [example_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW,
            public=True,
            base_url="http://example.test/site",
            allowed_licenses=["CC0-1.0"],
        )

    _publish(root, example_config, example_catalog)
    with pytest.raises(PublicationError, match="cannot precede"):
        _publish(root, example_config, example_catalog, at=NOW - timedelta(seconds=1))


def test_equal_instant_with_different_offset_is_idempotent(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    central = timezone(timedelta(hours=-6))
    first = _publish(root, example_config, example_catalog, at=NOW.astimezone(central))

    second = _publish(root, example_config, example_catalog, at=NOW)

    assert not second.changed
    assert second.manifest.publication_id == first.manifest.publication_id
    assert second.manifest.generated_at == NOW


def test_equal_timestamp_cannot_name_two_different_snapshots(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    _publish(root, example_config, example_catalog)
    changed = example_catalog.model_copy(deep=True)
    first = changed.offerings[0]
    changed.offerings[0] = first.model_copy(
        update={
            "signals": {
                **first.signals,
                "success_rate": first.signals["success_rate"].model_copy(
                    update={"value": Decimal("0.63")}
                ),
            }
        }
    )

    with pytest.raises(PublicationError, match="already used by a different snapshot"):
        _publish(root, example_config, changed, at=NOW)


def test_source_descriptor_retrieval_time_can_evolve_across_history(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    _publish(root, example_config, example_catalog)
    refreshed = example_catalog.model_copy(deep=True)
    for index, offering in enumerate(refreshed.offerings):
        assert offering.default_source is not None
        refreshed.offerings[index] = offering.model_copy(
            update={
                "default_source": offering.default_source.model_copy(
                    update={"retrieved_at": NOW + timedelta(minutes=5)}
                )
            }
        )

    result = _publish(root, example_config, refreshed, at=NOW + timedelta(minutes=10))

    assert result.changed
    history = load_frontier_history(root / "frontiers" / "coding-value" / "history.json")
    assert len(history.entries) == 2


def test_size_preflight_happens_before_any_artifact_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    monkeypatch.setattr(publisher, "MAX_ARTIFACT_BYTES", 32)

    with pytest.raises(PublicationError, match="publication limit"):
        _publish(root, example_config, example_catalog)

    assert root.is_dir()
    assert not list(root.rglob("*"))


def test_public_retry_reclaims_only_the_exact_failed_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    publish_project(
        example_config,
        [example_catalog],
        root,
        project_id="coding-demo",
        generated_at=NOW,
        public=True,
        base_url="https://example.test/model-skyline",
        allowed_licenses=["CC0-1.0"],
    )
    original = publisher._replace_mutable

    def fail_alias(root_value: Path, path: str, payload: bytes) -> None:
        if path == "selections/coding-agent-defaults/latest.json":
            raise PublicationError("injected public alias failure")
        original(root_value, path, payload)

    monkeypatch.setattr(publisher, "_replace_mutable", fail_alias)
    with pytest.raises(PublicationError, match="injected public alias failure"):
        publish_project(
            example_config,
            [example_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW + timedelta(minutes=10),
            public=True,
            base_url="https://example.test/model-skyline",
            allowed_licenses=["CC0-1.0"],
        )

    monkeypatch.setattr(publisher, "_replace_mutable", original)
    recovered = publish_project(
        example_config,
        [example_catalog],
        root,
        project_id="coding-demo",
        generated_at=NOW + timedelta(minutes=10),
        public=True,
        base_url="https://example.test/model-skyline",
        allowed_licenses=["CC0-1.0"],
    )
    assert recovered.changed


def test_public_mode_rejects_extra_alias_namespace(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    _publish(root, example_config, example_catalog)
    evil = root / "frontiers" / "evil" / "latest.json"
    evil.parent.mkdir()
    evil.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PublicationError, match="uncommitted alias"):
        publish_project(
            example_config,
            [example_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW + timedelta(minutes=10),
            public=True,
            base_url="https://example.test/model-skyline",
            allowed_licenses=["CC0-1.0"],
        )


def test_primary_immutable_failure_cannot_publish_dangling_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    original = publisher._write_immutable

    def fail_selection(root_value: Path, path: str, payload: bytes) -> None:
        if path.startswith("selections/") and path.endswith(".json"):
            raise PublicationError("injected primary immutable failure")
        original(root_value, path, payload)

    monkeypatch.setattr(publisher, "_write_immutable", fail_selection)
    with pytest.raises(PublicationError, match="injected primary immutable failure"):
        _publish(root, example_config, example_catalog)
    publications = root / "publications"
    assert not publications.exists() or not list(publications.iterdir())

    monkeypatch.setattr(publisher, "_write_immutable", original)
    assert _publish(root, example_config, example_catalog).changed


def test_frontier_and_selection_sets_are_additive_but_not_implicitly_retired(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    root = _root(tmp_path)
    first = publish_project(
        example_config,
        [example_catalog],
        root,
        project_id="coding-demo",
        frontier_ids=["coding-value"],
        selection_ids=["coding-agent-defaults"],
        generated_at=NOW,
    )
    assert len(first.manifest.frontiers) == 1

    expanded = _publish(
        root,
        example_config,
        example_catalog,
        at=NOW + timedelta(minutes=10),
    )
    assert len(expanded.manifest.frontiers) == 2

    with pytest.raises(PublicationError, match="every previously published frontier"):
        publish_project(
            example_config,
            [example_catalog],
            root,
            project_id="coding-demo",
            frontier_ids=["coding-responsiveness"],
            selection_ids=[],
            generated_at=NOW + timedelta(minutes=20),
        )

    without_selection = example_config.model_copy(deep=True)
    without_selection.selections.clear()
    with pytest.raises(PublicationError, match="every previously published selection"):
        publish_project(
            without_selection,
            [example_catalog],
            root,
            project_id="coding-demo",
            generated_at=NOW + timedelta(minutes=20),
        )
