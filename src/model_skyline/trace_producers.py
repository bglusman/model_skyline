"""Reviewed producer identities accepted in canonical request traces.

Trace rows carry only a compact producer key.  Public source metadata is
resolved from this code-owned registry so untrusted row strings can never
become URLs, license claims, or publication prose.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from model_skyline.models import SourceReference

ProducerKey = tuple[str, str, str, str, str, str | None, str | None]
_REVIEWED_AT: Final = datetime(2026, 8, 30, tzinfo=UTC)
_OPENCLAW_REVIEWED_AT: Final = datetime(2026, 9, 1, tzinfo=UTC)
_HERMES_021_REVIEWED_AT: Final = datetime(2026, 9, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class TrustedTraceProducer:
    key: ProducerKey
    source: SourceReference


def _source(
    *,
    source_id: str,
    version: str,
    url: str,
    terms_url: str,
    license_name: str,
    methodology: str,
    retrieved_at: datetime = _REVIEWED_AT,
) -> SourceReference:
    return SourceReference(
        id=source_id,
        version=version,
        url=url,
        terms_url=terms_url,
        license=license_name,
        methodology=methodology,
        retrieved_at=retrieved_at,
    )


_CODEX_LICENSE = (
    "https://github.com/openai/codex/blob/a6645b6b8a656360fa16fb7e1c6721d0697d3d6a/LICENSE"
)
_CLAUDE_LICENSE = (
    "https://github.com/anthropics/claude-agent-sdk-python/blob/"
    "af5ff1b9f2f279575f89b78f17572c6e35fbc2b6/LICENSE"
)
_OPENCLAW_LICENSE = (
    "https://github.com/openclaw/openclaw/blob/ea806575e6450e4d1efdfc72c19f04be982a1b9b/LICENSE"
)
_OPENCLAW_NPM_TARBALL_INTEGRITY = (
    "sha512-bSaFeaDFnQH/bU1vgKMac6eHkHHPHG0C/uwduXGI3eIS3lyiYSwmDU5ehhBUUhlPeV85tL5/"
    "KVwmoH48nX1tWw=="
)

_TRACE_PRODUCERS: dict[ProducerKey, TrustedTraceProducer] = {}


def _register(producer: TrustedTraceProducer) -> None:
    if producer.key in _TRACE_PRODUCERS:
        raise RuntimeError("duplicate trusted trace producer key")
    _TRACE_PRODUCERS[producer.key] = producer


for _codex_version, _codex_commit in (
    ("0.144.2", "a6645b6b8a656360fa16fb7e1c6721d0697d3d6a"),
    ("0.151.0", "78c290807ce710180111df227df3b7a4fe845452"),
):
    _register(
        TrustedTraceProducer(
            key=(
                "model-skyline/codex-exec-jsonl",
                "1",
                "openai/codex",
                _codex_version,
                _codex_commit,
                None,
                None,
            ),
            source=_source(
                source_id=f"producer:openai-codex:{_codex_version}",
                version=_codex_commit,
                url=(
                    "https://github.com/openai/codex/blob/"
                    f"{_codex_commit}/sdk/typescript/src/events.ts"
                ),
                terms_url=_CODEX_LICENSE,
                license_name="Apache-2.0",
                methodology=(
                    "Exact reviewed Codex exec JSONL event contract; adapter accepts only the "
                    "pinned release and commit."
                ),
            ),
        )
    )

for _claude_adapter_version in ("1", "2"):
    _register(
        TrustedTraceProducer(
            key=(
                "model-skyline/claude-agent-sdk-result",
                _claude_adapter_version,
                "anthropics/claude-agent-sdk-python+claude-code",
                "0.2.148+cli.2.1.251",
                "af5ff1b9f2f279575f89b78f17572c6e35fbc2b6",
                None,
                None,
            ),
            source=_source(
                source_id="producer:anthropic-claude-agent-sdk:0.2.148-cli-2.1.251",
                version="af5ff1b9f2f279575f89b78f17572c6e35fbc2b6",
                url=(
                    "https://github.com/anthropics/claude-agent-sdk-python/blob/"
                    "af5ff1b9f2f279575f89b78f17572c6e35fbc2b6/"
                    "src/claude_agent_sdk/types.py"
                ),
                terms_url=_CLAUDE_LICENSE,
                license_name="MIT",
                methodology=(
                    "Exact reviewed Claude Agent SDK ResultMessage contract with the bundled "
                    "Claude Code CLI version pinned separately by the adapter."
                ),
            ),
        )
    )

_HERMES_ADAPTER_METHODOLOGIES = {
    "1": (
        "Exact reviewed Hermes Agent v26 usage-ledger and aggregate-report subset; "
        "adapter requires explicit route attestations and exact raw route strings."
    ),
    "2": (
        "Exact reviewed Hermes Agent v26 usage-ledger and aggregate-report subset; "
        "adapter requires explicit route attestations, preserves absent billing mode, and "
        "mirrors the safe subset of Hermes route URL identity normalization."
    ),
}
_HERMES_021_METHODOLOGY = (
    "Exact reviewed Hermes Agent schema-v26 SQLite usage-ledger subset; adapter requires "
    "an operator-asserted exact Agent version and route, preserves absent billing mode, and "
    "mirrors the safe subset of Hermes route URL identity normalization. Agent 0.21.0 "
    "usage-report import is intentionally unsupported."
)
for _hermes_version, _hermes_commit, _hermes_adapter_versions, _hermes_reviewed_at in (
    (
        "0.20.6",
        "4f22543509d1b91dc45bcb369447126c5eb14fb7",
        ("1", "2"),
        _REVIEWED_AT,
    ),
    (
        "0.21.0",
        "29112bef099274229cadff79cdff7bf7b99c4b77",
        ("2",),
        _HERMES_021_REVIEWED_AT,
    ),
):
    for _hermes_adapter_version in _hermes_adapter_versions:
        _register(
            TrustedTraceProducer(
                key=(
                    "model-skyline/hermes-agent-aggregate",
                    _hermes_adapter_version,
                    "nousresearch/hermes-agent",
                    _hermes_version,
                    _hermes_commit,
                    None,
                    None,
                ),
                source=_source(
                    source_id=(
                        "producer:nousresearch-hermes-agent:"
                        f"{_hermes_version}:adapter-{_hermes_adapter_version}"
                    ),
                    version=_hermes_commit,
                    url=(
                        "https://github.com/NousResearch/hermes-agent/blob/"
                        f"{_hermes_commit}/agent/usage_pricing.py"
                    ),
                    terms_url=(
                        "https://github.com/NousResearch/hermes-agent/blob/"
                        f"{_hermes_commit}/LICENSE"
                    ),
                    license_name="MIT",
                    methodology=(
                        _HERMES_021_METHODOLOGY
                        if _hermes_version == "0.21.0"
                        else _HERMES_ADAPTER_METHODOLOGIES[_hermes_adapter_version]
                    ),
                    retrieved_at=_hermes_reviewed_at,
                ),
            )
        )

_register(
    TrustedTraceProducer(
        key=(
            "model-skyline/openclaw-model-call",
            "1alpha3",
            "openclaw/openclaw",
            "2026.8.1",
            "ea806575e6450e4d1efdfc72c19f04be982a1b9b",
            "model-skyline/openclaw-trusted-projector",
            "3",
        ),
        source=_source(
            source_id="producer:openclaw-model-call:2026.8.1",
            version="ea806575e6450e4d1efdfc72c19f04be982a1b9b",
            url=(
                "https://github.com/openclaw/openclaw/blob/"
                "ea806575e6450e4d1efdfc72c19f04be982a1b9b/"
                "src/infra/diagnostic-events.ts"
            ),
            terms_url=_OPENCLAW_LICENSE,
            license_name="MIT",
            retrieved_at=_OPENCLAW_REVIEWED_AT,
            methodology=(
                "Exact reviewed OpenClaw model-call diagnostic projection; the trusted "
                "projector correlates each model-call child span to its per-attempt "
                "run.started parent, independently proves asynchronous segment "
                "completeness, rejects dropped events, and authenticates completeness "
                "attestations in the canonical envelope. The signed v2026.8.1 tag and "
                "published npm build-info identify this commit; the npm root tarball "
                f"integrity is {_OPENCLAW_NPM_TARBALL_INTEGRITY}."
            ),
        ),
    )
)

TRUSTED_TRACE_PRODUCERS: Final[Mapping[ProducerKey, TrustedTraceProducer]] = MappingProxyType(
    _TRACE_PRODUCERS
)


def trusted_trace_producer(key: ProducerKey) -> TrustedTraceProducer | None:
    """Resolve an exact reviewed producer key without interpreting its strings."""

    return TRUSTED_TRACE_PRODUCERS.get(key)
