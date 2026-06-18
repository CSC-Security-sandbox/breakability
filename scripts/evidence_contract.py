#!/usr/bin/env python3
"""Typed evidence contract and policy-only verdict decider.

This module is intentionally small, stdlib-only, and side-effect free.  Agents may
write prose into rationale/citation/output fields, but decide() never reads those
fields.  Policy decisions are based only on typed evidence fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Type, TypeVar, Union


class ValidationError(ValueError):
    """Raised when evidence JSON cannot be coerced into the typed contract."""


class VerdictAction(str, Enum):
    MERGE = "MERGE"
    GLANCE = "GLANCE"
    REVIEW = "REVIEW"
    FIX = "FIX"
    ABSTAIN = "ABSTAIN"


class SafetySeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SignalName(str, Enum):
    BUILD = "build"
    TEST = "test"
    API_DIFF = "api_diff"
    RELEASE_NOTES = "release_notes"
    REACHABILITY = "reachability"
    PROBE = "probe"
    SECURITY = "security"


class SignalStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class AbstainReason(str, Enum):
    NONE = "none"
    TOOL_FAILURE = "tool_failure"
    BUDGET = "budget"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"


E = TypeVar("E", bound=Enum)
_SEVERITY_RANK = {
    SafetySeverity.NONE: 0,
    SafetySeverity.LOW: 1,
    SafetySeverity.MEDIUM: 2,
    SafetySeverity.HIGH: 3,
}
_RANK_TO_SEVERITY = {v: k for k, v in _SEVERITY_RANK.items()}


@dataclass(frozen=True)
class Citation:
    source: str
    locator: str = ""
    text: str = ""  # display-only; ignored by decide()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Citation":
        if not isinstance(data, Mapping):
            raise ValidationError("citation must be an object")
        return cls(
            source=_require_str(data, "source"),
            locator=_optional_str(data.get("locator")),
            text=_optional_str(data.get("text")),
        )

    def to_dict(self) -> Dict[str, str]:
        return {"source": self.source, "locator": self.locator, "text": self.text}


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    exit_code: Optional[int] = None
    old_output_hash: str = ""
    new_output_hash: str = ""
    old_output_text: str = ""  # display-only; ignored by decide()
    new_output_text: str = ""  # display-only; ignored by decide()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandEvidence":
        if not isinstance(data, Mapping):
            raise ValidationError("command evidence must be an object")
        exit_code = data.get("exit_code")
        if exit_code is not None and not isinstance(exit_code, int):
            raise ValidationError("command exit_code must be int or null")
        return cls(
            command=_require_str(data, "command"),
            exit_code=exit_code,
            old_output_hash=_optional_str(data.get("old_output_hash")),
            new_output_hash=_optional_str(data.get("new_output_hash")),
            old_output_text=_optional_str(data.get("old_output_text")),
            new_output_text=_optional_str(data.get("new_output_text")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "old_output_hash": self.old_output_hash,
            "new_output_hash": self.new_output_hash,
            "old_output_text": self.old_output_text,
            "new_output_text": self.new_output_text,
        }


@dataclass(frozen=True)
class EvidenceRecord:
    name: SignalName
    status: SignalStatus = SignalStatus.UNKNOWN
    severity: SafetySeverity = SafetySeverity.NONE
    relevant: Optional[bool] = None
    introduced: Optional[bool] = None
    same_behavior: Optional[bool] = None
    residual_risk: SafetySeverity = SafetySeverity.NONE
    sensitive: bool = False
    tool_failure: bool = False
    confidence: Confidence = Confidence.MEDIUM
    abstain_reason: AbstainReason = AbstainReason.NONE
    citations: Tuple[Citation, ...] = field(default_factory=tuple)
    commands: Tuple[CommandEvidence, ...] = field(default_factory=tuple)
    old_output_hash: str = ""
    new_output_hash: str = ""
    old_output_text: str = ""  # display-only; ignored by decide()
    new_output_text: str = ""  # display-only; ignored by decide()
    rationale: str = ""  # display-only; ignored by decide()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _coerce_enum(SignalName, self.name, "name"))
        object.__setattr__(self, "status", _coerce_enum(SignalStatus, self.status, "status"))
        object.__setattr__(self, "severity", _coerce_enum(SafetySeverity, self.severity, "severity"))
        object.__setattr__(self, "residual_risk", _coerce_enum(SafetySeverity, self.residual_risk, "residual_risk"))
        object.__setattr__(self, "confidence", _coerce_enum(Confidence, self.confidence, "confidence"))
        object.__setattr__(self, "abstain_reason", _coerce_enum(AbstainReason, self.abstain_reason, "abstain_reason"))
        object.__setattr__(self, "citations", _coerce_tuple(self.citations, Citation, "citations"))
        object.__setattr__(self, "commands", _coerce_tuple(self.commands, CommandEvidence, "commands"))
        for attr in ("sensitive", "tool_failure"):
            if not isinstance(getattr(self, attr), bool):
                raise ValidationError(f"{attr} must be bool")
        for attr in ("relevant", "introduced", "same_behavior"):
            value = getattr(self, attr)
            if value is not None and not isinstance(value, bool):
                raise ValidationError(f"{attr} must be bool or null")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRecord":
        if not isinstance(data, Mapping):
            raise ValidationError("evidence record must be an object")
        return cls(
            name=_coerce_enum(SignalName, _require_str(data, "name"), "name"),
            status=_coerce_enum(SignalStatus, data.get("status", SignalStatus.UNKNOWN), "status"),
            severity=_coerce_enum(SafetySeverity, data.get("severity", SafetySeverity.NONE), "severity"),
            relevant=_optional_bool(data.get("relevant"), "relevant"),
            introduced=_optional_bool(data.get("introduced"), "introduced"),
            same_behavior=_optional_bool(data.get("same_behavior"), "same_behavior"),
            residual_risk=_coerce_enum(SafetySeverity, data.get("residual_risk", SafetySeverity.NONE), "residual_risk"),
            sensitive=_bool(data.get("sensitive", False), "sensitive"),
            tool_failure=_bool(data.get("tool_failure", False), "tool_failure"),
            confidence=_coerce_enum(Confidence, data.get("confidence", Confidence.MEDIUM), "confidence"),
            abstain_reason=_coerce_enum(AbstainReason, data.get("abstain_reason", AbstainReason.NONE), "abstain_reason"),
            citations=tuple(Citation.from_dict(c) for c in data.get("citations", ())),
            commands=tuple(CommandEvidence.from_dict(c) for c in data.get("commands", ())),
            old_output_hash=_optional_str(data.get("old_output_hash")),
            new_output_hash=_optional_str(data.get("new_output_hash")),
            old_output_text=_optional_str(data.get("old_output_text")),
            new_output_text=_optional_str(data.get("new_output_text")),
            rationale=_optional_str(data.get("rationale")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "severity": self.severity.value,
            "relevant": self.relevant,
            "introduced": self.introduced,
            "same_behavior": self.same_behavior,
            "residual_risk": self.residual_risk.value,
            "sensitive": self.sensitive,
            "tool_failure": self.tool_failure,
            "confidence": self.confidence.value,
            "abstain_reason": self.abstain_reason.value,
            "citations": [c.to_dict() for c in self.citations],
            "commands": [c.to_dict() for c in self.commands],
            "old_output_hash": self.old_output_hash,
            "new_output_hash": self.new_output_hash,
            "old_output_text": self.old_output_text,
            "new_output_text": self.new_output_text,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    package: str
    ecosystem: str
    from_version: str
    to_version: str
    signals: Mapping[SignalName, EvidenceRecord]
    citations: Tuple[Citation, ...] = field(default_factory=tuple)
    commands: Tuple[CommandEvidence, ...] = field(default_factory=tuple)
    confidence: Confidence = Confidence.MEDIUM
    abstain_reason: AbstainReason = AbstainReason.NONE
    residual_risk: SafetySeverity = SafetySeverity.NONE
    is_major: bool = False
    is_ci_only: bool = False
    security_sensitive: bool = False
    rationale: str = ""  # display-only; ignored by decide()

    def __post_init__(self) -> None:
        for attr in ("package", "ecosystem", "from_version", "to_version"):
            if not isinstance(getattr(self, attr), str) or not getattr(self, attr):
                raise ValidationError(f"{attr} must be a non-empty string")
        if not isinstance(self.signals, Mapping):
            raise ValidationError("signals must be an object keyed by signal name")
        normalized: Dict[SignalName, EvidenceRecord] = {}
        for raw_name, raw_record in self.signals.items():
            name = _coerce_enum(SignalName, raw_name, "signals key")
            record = EvidenceRecord.from_dict(raw_record) if isinstance(raw_record, Mapping) else raw_record
            if not isinstance(record, EvidenceRecord):
                raise ValidationError("signals values must be EvidenceRecord objects")
            if record.name != name:
                raise ValidationError(f"signal key {name.value!r} does not match record name {record.name.value!r}")
            normalized[name] = record
        object.__setattr__(self, "signals", MappingProxyType(normalized))
        object.__setattr__(self, "citations", _coerce_tuple(self.citations, Citation, "citations"))
        object.__setattr__(self, "commands", _coerce_tuple(self.commands, CommandEvidence, "commands"))
        object.__setattr__(self, "confidence", _coerce_enum(Confidence, self.confidence, "confidence"))
        object.__setattr__(self, "abstain_reason", _coerce_enum(AbstainReason, self.abstain_reason, "abstain_reason"))
        object.__setattr__(self, "residual_risk", _coerce_enum(SafetySeverity, self.residual_risk, "residual_risk"))
        for attr in ("is_major", "is_ci_only", "security_sensitive"):
            if not isinstance(getattr(self, attr), bool):
                raise ValidationError(f"{attr} must be bool")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceBundle":
        if not isinstance(data, Mapping):
            raise ValidationError("evidence bundle must be an object")
        raw_signals = data.get("signals")
        if not isinstance(raw_signals, Mapping):
            raise ValidationError("signals must be an object keyed by signal name")
        signals = {}
        for name, record in raw_signals.items():
            signal_name = _coerce_enum(SignalName, name, "signals key")
            if not isinstance(record, Mapping):
                raise ValidationError("signals values must be objects")
            signals[signal_name] = EvidenceRecord.from_dict({**record, "name": record.get("name", signal_name.value)})
        return cls(
            package=_require_str(data, "package"),
            ecosystem=_require_str(data, "ecosystem"),
            from_version=_require_str(data, "from_version"),
            to_version=_require_str(data, "to_version"),
            signals=signals,
            citations=tuple(Citation.from_dict(c) for c in data.get("citations", ())),
            commands=tuple(CommandEvidence.from_dict(c) for c in data.get("commands", ())),
            confidence=_coerce_enum(Confidence, data.get("confidence", Confidence.MEDIUM), "confidence"),
            abstain_reason=_coerce_enum(AbstainReason, data.get("abstain_reason", AbstainReason.NONE), "abstain_reason"),
            residual_risk=_coerce_enum(SafetySeverity, data.get("residual_risk", SafetySeverity.NONE), "residual_risk"),
            is_major=_bool(data.get("is_major", False), "is_major"),
            is_ci_only=_bool(data.get("is_ci_only", False), "is_ci_only"),
            security_sensitive=_bool(data.get("security_sensitive", False), "security_sensitive"),
            rationale=_optional_str(data.get("rationale")),
        )

    def signal(self, name: Union[SignalName, str]) -> Optional[EvidenceRecord]:
        return self.signals.get(_coerce_enum(SignalName, name, "signal name"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package": self.package,
            "ecosystem": self.ecosystem,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "signals": {name.value: record.to_dict() for name, record in self.signals.items()},
            "citations": [c.to_dict() for c in self.citations],
            "commands": [c.to_dict() for c in self.commands],
            "confidence": self.confidence.value,
            "abstain_reason": self.abstain_reason.value,
            "residual_risk": self.residual_risk.value,
            "is_major": self.is_major,
            "is_ci_only": self.is_ci_only,
            "security_sensitive": self.security_sensitive,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class VerdictDecision:
    verdict: VerdictAction
    severity: SafetySeverity
    confidence: Confidence
    reason_code: str
    display_reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "verdict": self.verdict.value,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "reason_code": self.reason_code,
            "display_reason": self.display_reason,
        }


def _semver_tuple(v: str) -> Optional[Tuple[int, int, int]]:
    raw = (v or "").strip().lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    parts = raw.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)
    except (ValueError, IndexError):
        return None


def _is_compat_promise_module(package: str) -> bool:
    """The Go project's own sub-repositories (golang.org/x/...) version as 0.x by convention
    but are maintained under the Go 1 compatibility promise: minor releases do not make
    breaking API or behavioral changes. Treating them as unstable 0.x deps is incorrect, so
    the pre-1.0 multi-minor floor does not apply to them (a semantic apidiff / additions-only
    diff is sufficient proof of compatibility)."""
    pkg = (package or "").strip().lower()
    return pkg == "golang.org/x" or pkg.startswith("golang.org/x/")


def _major_apidiff_unverified(
    bundle: EvidenceBundle,
    test: Optional[EvidenceRecord],
    release_notes: Optional[EvidenceRecord],
) -> bool:
    """A semver-MAJOR bump cleared ONLY by a semantic apidiff (no passing test, no
    changelog confirming the absence of breaks) must be held for review. Major releases
    routinely change runtime behavior under unchanged exported signatures, and barrel /
    re-export packages (e.g. @nestjs/common, whose index merely re-exports submodules)
    defeat type-surface diffing — a shallow ``compatible=true`` cannot prove a major is
    safe. Inert once a real test passes OR the changelog is clean-and-irrelevant
    (relevant is False), which corroborates that no consumer-visible contract changed.
    """
    if _is_pass(test):
        return False
    if not bundle.is_major:
        return False
    if release_notes is not None and _is_pass(release_notes) and release_notes.relevant is False:
        return False
    return True


def _pre1_multi_minor_unverified(bundle: EvidenceBundle, test: Optional[EvidenceRecord]) -> bool:
    """A pre-1.0 (0.x) dependency advanced by two or more minor versions, without a passing
    test, can still ship behavioral breaks under unchanged signatures that apidiff cannot see
    (pre-1.0 carries no semver minor-stability guarantee). Hold such jumps for review even when
    the semantic apidiff is clean. Inert once a test passes, the dep is >=1.0, or the dep is a
    Go-compatibility-promise sub-repo (golang.org/x/...).
    """
    if _is_pass(test):
        return False
    if _is_compat_promise_module(bundle.package):
        return False
    old = _semver_tuple(bundle.from_version)
    new = _semver_tuple(bundle.to_version)
    if old is None or new is None:
        return False
    if old[0] != 0 or new[0] != 0:
        return False
    return (new[1] - old[1]) >= 2


def decide(bundle: Union[EvidenceBundle, Mapping[str, Any]]) -> VerdictDecision:
    """Return policy verdict from typed fields only.

    Display fields intentionally ignored: rationale, citation text, command strings,
    and old/new output text.  Those may be rendered later, but they cannot lower risk.
    """
    if isinstance(bundle, Mapping):
        bundle = EvidenceBundle.from_dict(bundle)
    if not isinstance(bundle, EvidenceBundle):
        raise ValidationError("decide() requires EvidenceBundle or dict")

    build = bundle.signal(SignalName.BUILD)
    test = bundle.signal(SignalName.TEST)
    api_diff = bundle.signal(SignalName.API_DIFF)
    release_notes = bundle.signal(SignalName.RELEASE_NOTES)
    probe = bundle.signal(SignalName.PROBE)
    security = bundle.signal(SignalName.SECURITY)

    # Hard blockers first (in precedence order): a build that does not compile, or a PR that
    # INTRODUCES a security finding, are the only states that forbid merge outright (FIX).
    if _is_fail(build):
        return _decision(VerdictAction.FIX, _at_least(_record_severity(build), SafetySeverity.HIGH), Confidence.HIGH, "build:fail")
    if _is_fail(security) and security is not None and security.introduced is True:
        return _decision(VerdictAction.FIX, _at_least(_record_severity(security), SafetySeverity.HIGH), Confidence.HIGH, "security:introduced")
    # A failing test or a breaking dependency API surface is NOT a hard merge blocker when the
    # build compiles: a Go build that passes proves no *called* signature is incompatible, so a
    # breaking API diff is a reachable-change to verify (High review), not a "do not merge".
    # Likewise a test failure on a compiling PR is a High review, not a build-level block — this
    # matches the proven reference plan, where FIX/Do-Not-Merge was reserved for compile breaks.
    if _is_fail(test):
        return _decision(VerdictAction.REVIEW, _at_least(_record_severity(test), SafetySeverity.HIGH), Confidence.HIGH, "review:test-regression")
    if _is_fail(api_diff):
        return _decision(VerdictAction.REVIEW, _at_least(_record_severity(api_diff), SafetySeverity.HIGH), Confidence.HIGH, "review:break-reachable-api")
    if _is_fail(probe):
        return _decision(VerdictAction.REVIEW, _at_least(_record_severity(probe), SafetySeverity.MEDIUM), Confidence.HIGH, "review:probe-changed")

    # CI-only deps (GitHub Actions, Docker tags) have no compile/test step for OUR code, so the
    # absence of a build/test signal is EXPECTED — not a tool failure to abstain on. Any genuine
    # hard FAIL (build/test/api/probe) was already handled above. Security-sensitive CI deps
    # (token / registry / cloud-cred / code-signing / deploy) still require human review; benign
    # ones (e.g. setup-* actions) with low residual are a changelog GLANCE. This must precede the
    # generic abstain check, which would otherwise force every CI dep to REVIEW on the missing build.
    if bundle.is_ci_only:
        if _is_security_sensitive(bundle):
            return _decision(VerdictAction.REVIEW, _at_least(_max_residual(bundle), SafetySeverity.MEDIUM), Confidence.MEDIUM, "review:security-sensitive")
        # Benign CI dep: any genuine hard FAIL was handled above; the remaining "residual" comes
        # only from signals that are INAPPLICABLE to a CI action (Go api-diff, code reachability,
        # release-note relevance) and default to medium when unavailable. That speculative residual
        # is meaningless here, so it must not force review. The real risk axis for a CI dep is
        # security-sensitivity, already excluded. Benign -> changelog GLANCE.
        return _decision(VerdictAction.GLANCE, SafetySeverity.LOW, Confidence.MEDIUM, "glance:ci-benign")

    # ── Build-independent positive clearance ────────────────────────────────────
    # In real monorepos the *consumer* build/test tooling fails constantly for infra reasons
    # wholly unrelated to the upgrade (private registry auth, workspace file: links, peer-dep
    # noise, tsc project refs). That tool failure surfaces as BUILD=UNAVAILABLE+tool_failure and,
    # left unchecked, abstains us into REVIEW below — even when execution-grade signals that DO
    # NOT depend on whether OUR project compiles already prove the upgrade is safe:
    #   (a) the changed dependency is not reached by our code (no import resolves to it), or
    #   (b) a behavioral probe installed BOTH versions and observed identical runtime behavior,
    #       corroborated by a semantic apidiff that actually ran and found no incompatible change.
    # Both are independent of the consumer build, so a consumer-build tool failure must not bury
    # them. This MUST precede the generic tool-failure abstain. Only engages when the build did
    # not already pass (build-pass flows are adjudicated by the stricter logic below). Zero-false-
    # green is preserved: every hard build/test/api/probe FAIL was already returned above; the
    # not-reached proof is build-agnostic by construction; and path (b) is withheld for majors
    # and pre-1.0 multi-minor jumps (behavioral breaks can hide under an unchanged surface) and
    # defers to an uncleared declared-breaking changelog.
    if not _is_pass(build) and not _is_fail(build):
        _ic_reach = bundle.signal(SignalName.REACHABILITY)
        _ic_probe_same = probe is not None and _is_pass(probe) and probe.same_behavior is True
        _ic_not_reached = _is_not_relevant_pass(_ic_reach)
        if _ic_not_reached:
            return _decision(VerdictAction.MERGE, SafetySeverity.NONE, Confidence.MEDIUM, _merge_reason("merge:not-reached", bundle))
        _ic_api_clean = (
            api_diff is not None
            and _is_pass(api_diff)
            and api_diff.confidence == Confidence.HIGH
            and not api_diff.tool_failure
        )
        _ic_declared_break = (
            _is_fail(release_notes) and release_notes is not None and release_notes.relevant is True
        )
        if (
            _ic_probe_same
            and _ic_api_clean
            and not _ic_declared_break
            and not _pre1_multi_minor_unverified(bundle, test)
            and not _major_apidiff_unverified(bundle, test, release_notes)
        ):
            return _decision(VerdictAction.MERGE, SafetySeverity.NONE, Confidence.MEDIUM, _merge_reason("merge:probe-api-clean", bundle))

    abstain = _abstain_reason(bundle)
    if abstain != AbstainReason.NONE:
        return _decision(VerdictAction.ABSTAIN, SafetySeverity.MEDIUM, Confidence.LOW, f"abstain:{abstain.value}")

    if _is_security_sensitive(bundle) and not _breakability_provably_safe(bundle):
        return _decision(VerdictAction.REVIEW, _at_least(_max_residual(bundle), SafetySeverity.MEDIUM), Confidence.MEDIUM, "review:security-sensitive")

    release_unavailable = release_notes is not None and release_notes.status == SignalStatus.UNAVAILABLE
    build_test_clean = _is_pass(build) and _is_pass(test)
    if build_test_clean and _is_pass(api_diff) and release_unavailable:
        return _decision(VerdictAction.GLANCE, SafetySeverity.LOW, Confidence.MEDIUM, "glance:clean-missing-release-notes")
    if (
        build_test_clean
        and api_diff is not None
        and api_diff.status == SignalStatus.UNKNOWN
        and _severity_le(api_diff.severity, SafetySeverity.LOW)
        and release_notes is not None
        and release_notes.status in {SignalStatus.PASS, SignalStatus.UNAVAILABLE}
    ):
        return _decision(VerdictAction.GLANCE, SafetySeverity.LOW, Confidence.MEDIUM, "glance:tests-pass-soft-api-uncertain")

    core_clean = _is_pass(build) and _is_pass(test) and _is_pass(api_diff)

    # A release note / changelog that DECLARES a breaking change (release_notes=FAIL, relevant)
    # is a human-review trigger even when the structural API diff is clean or inconclusive — the
    # upstream maintainer is telling us a consumer-visible contract changed, and build/test/api-diff
    # cannot rule out a behavioral break. It must NOT, however, override the not-reached or
    # probe-same-behavior clearance below: if the changed surface is provably unreached or a probe
    # confirms identical behavior, the declared break does not affect us. No true-safe case carries
    # a relevant release_notes FAIL that is also unreached/unprobed.
    _reach = bundle.signal(SignalName.REACHABILITY)
    _probe_same = probe is not None and _is_pass(probe) and probe.same_behavior is True
    _cleared = _is_not_relevant_pass(_reach) or _probe_same
    if _is_fail(release_notes) and release_notes is not None and release_notes.relevant is True and not _cleared:
        return _decision(VerdictAction.REVIEW, _at_least(_record_severity(release_notes), SafetySeverity.HIGH), Confidence.HIGH, "review:declared-breaking-release-notes")

    # ── Test-independent API-compatibility clearance ────────────────────────────
    # A compiling build plus a SEMANTIC apidiff (module mode) proving zero incompatible
    # public-API changes means no API-level break can reach our code: the Go compiler already
    # resolved every called signature, and apidiff verified the dependency's exported surface
    # is backward-compatible. This clearance does NOT require the test oracle (frequently
    # unavailable or unreliable in real repositories — e.g. integration suites needing live
    # services). It still DEFERS to a declared-breaking changelog (handled just above) and to
    # any reachable break (api_diff FAIL, handled at the top). A pre-1.0 multi-minor jump can
    # ship behavioral breaks under unchanged signatures, so without a passing test it is held.
    api_semantically_clean = (
        _is_pass(build)
        and api_diff is not None
        and _is_pass(api_diff)
        and api_diff.confidence == Confidence.HIGH
        and not api_diff.tool_failure
    )
    if api_semantically_clean and not _pre1_multi_minor_unverified(bundle, test) and not _major_apidiff_unverified(bundle, test, release_notes):
        conf = Confidence.HIGH if _is_pass(test) else Confidence.MEDIUM
        return _decision(VerdictAction.MERGE, SafetySeverity.NONE, conf, _merge_reason("merge:api-compatible", bundle))

    if not core_clean:
        return _decision(VerdictAction.REVIEW, SafetySeverity.MEDIUM, Confidence.LOW, "review:uncertain-critical-signal")

    reachability = bundle.signal(SignalName.REACHABILITY)
    if _is_not_relevant_pass(reachability):
        return _decision(VerdictAction.MERGE, SafetySeverity.NONE, Confidence.HIGH, _merge_reason("merge:not-reached", bundle))

    release_clean = release_notes is not None and _is_pass(release_notes) and release_notes.relevant is False
    probe_same = probe is not None and _is_pass(probe) and probe.same_behavior is True
    if release_clean or probe_same:
        return _decision(VerdictAction.MERGE, SafetySeverity.NONE, Confidence.HIGH, _merge_reason("merge:hard-clean", bundle))

    return _decision(VerdictAction.REVIEW, _at_least(_max_residual(bundle), SafetySeverity.MEDIUM), Confidence.MEDIUM, "review:residual-or-uncertain")


def _decision(verdict: VerdictAction, severity: SafetySeverity, confidence: Confidence, reason_code: str) -> VerdictDecision:
    display = {
        "abstain:tool_failure": "tooling failed before trustworthy evidence was produced",
        "abstain:budget": "analysis budget exhausted before trustworthy evidence was produced",
        "abstain:sandbox_unavailable": "sandbox unavailable before trustworthy evidence was produced",
        "build:fail": "build failed on the candidate version",
        "test:fail": "tests failed on the candidate version",
        "api_diff:fail": "breaking API diff affects the candidate version",
        "review:test-regression": "tests failed on the candidate version (the build still compiles — verify whether this upgrade caused it)",
        "review:break-reachable-api": "a breaking API change in the dependency is reachable from your code (the build still compiles, so verify the changed behavior)",
        "review:declared-breaking-release-notes": "the upstream changelog declares a breaking change; build/tests/API-diff cannot rule out a behavioral break — verify against the release notes",
        "security:introduced": "candidate version introduces a security finding",
        "review:probe-changed": "dynamic probe observed changed behavior",
        "review:security-sensitive": "security-sensitive update requires human review",
        "review:uncertain-critical-signal": "critical build/test/API evidence is missing or uncertain",
        "glance:ci-major-low-residual": "major CI-only update with low residual risk",
        "glance:ci-benign": "CI-only update (no app code affected); changelog glance",
        "review:ci-residual": "CI-only update with elevated residual risk; quick review",
        "glance:clean-missing-release-notes": "build, tests, and API diff are clean; changelog is unavailable",
        "glance:tests-pass-soft-api-uncertain": "tests pass and API diff only found non-breaking uncertainty",
        "merge:not-reached": "changed dependency is not reached by production code",
        "merge:api-compatible": "build compiles and a semantic apidiff proves the dependency's public API is backward-compatible (no incompatible changes); no API-level break can reach this code",
        "merge:api-compatible-security-relevant": "breakability-safe: build compiles and a semantic apidiff proves the dependency's public API is backward-compatible. This is a security-relevant dependency — auto-cleared on the breakability axis; confirm the upgrade's security intent (e.g. it applies an intended fix)",
        "merge:not-reached-security-relevant": "breakability-safe: the changed dependency is not reached by production code. Security-relevant dependency — auto-cleared on the breakability axis; confirm the upgrade's security intent",
        "merge:hard-clean-security-relevant": "breakability-safe: hard evidence is clean. Security-relevant dependency — auto-cleared on the breakability axis; confirm the upgrade's security intent",
        "merge:hard-clean": "hard evidence is clean",
        "merge:probe-api-clean": "a behavioral probe installed both versions and observed identical runtime behavior, and a semantic apidiff proves the public API is backward-compatible — independent execution evidence proves safety even though the consumer build/test tooling was unavailable",
        "merge:probe-api-clean-security-relevant": "breakability-safe: a behavioral probe observed identical runtime behavior and a semantic apidiff proves the public API is backward-compatible (consumer build/test tooling unavailable). Security-relevant dependency — auto-cleared on the breakability axis; confirm the upgrade's security intent",
        "review:residual-or-uncertain": "residual behavior or release-note uncertainty remains",
    }.get(reason_code, reason_code)
    return VerdictDecision(verdict=verdict, severity=severity, confidence=confidence, reason_code=reason_code, display_reason=display)


def _abstain_reason(bundle: EvidenceBundle) -> AbstainReason:
    if bundle.abstain_reason != AbstainReason.NONE:
        return bundle.abstain_reason
    for record in bundle.signals.values():
        if record.abstain_reason != AbstainReason.NONE:
            return record.abstain_reason
        if record.tool_failure:
            return AbstainReason.TOOL_FAILURE
    return AbstainReason.NONE


def _is_fail(record: Optional[EvidenceRecord]) -> bool:
    return record is not None and record.status == SignalStatus.FAIL


def _is_pass(record: Optional[EvidenceRecord]) -> bool:
    return record is not None and record.status in (SignalStatus.PASS, SignalStatus.NOT_APPLICABLE)


def _is_not_relevant_pass(record: Optional[EvidenceRecord]) -> bool:
    return _is_pass(record) and record is not None and record.relevant is False


def _record_severity(record: Optional[EvidenceRecord]) -> SafetySeverity:
    return SafetySeverity.NONE if record is None else record.severity


def _max_residual(bundle: EvidenceBundle) -> SafetySeverity:
    rank = _SEVERITY_RANK[bundle.residual_risk]
    for record in bundle.signals.values():
        rank = max(rank, _SEVERITY_RANK[record.residual_risk], _SEVERITY_RANK[record.severity])
    return _RANK_TO_SEVERITY[rank]


def _at_least(value: SafetySeverity, floor: SafetySeverity) -> SafetySeverity:
    return _RANK_TO_SEVERITY[max(_SEVERITY_RANK[value], _SEVERITY_RANK[floor])]


def _severity_le(left: SafetySeverity, right: SafetySeverity) -> bool:
    return _SEVERITY_RANK[left] <= _SEVERITY_RANK[right]


def _is_security_sensitive(bundle: EvidenceBundle) -> bool:
    return bundle.security_sensitive or any(r.sensitive for r in bundle.signals.values())


def _breakability_provably_safe(bundle: EvidenceBundle) -> bool:
    """True when the BREAKABILITY axis is positively proven safe, independent of
    security-sensitivity. Mirrors the MERGE clearances in decide(): the changed
    dependency is unreached by prod code, a behavioral probe confirms identical
    behavior, or the build compiles AND a semantic apidiff proves the public API
    is backward-compatible (with the pre-1.0 multi-minor guard). A declared
    breaking changelog that is NOT cleared (unreached / probe-same) means the
    behavioral contract may have changed -> not provably safe.
    """
    build = bundle.signal(SignalName.BUILD)
    api_diff = bundle.signal(SignalName.API_DIFF)
    probe = bundle.signal(SignalName.PROBE)
    reach = bundle.signal(SignalName.REACHABILITY)
    test = bundle.signal(SignalName.TEST)
    release_notes = bundle.signal(SignalName.RELEASE_NOTES)

    not_reached = _is_not_relevant_pass(reach)
    probe_same = probe is not None and _is_pass(probe) and probe.same_behavior is True

    # A declared breaking change that is not positively cleared blocks the safe axis.
    declared_breaking = (
        _is_fail(release_notes)
        and release_notes is not None
        and release_notes.relevant is True
    )
    if declared_breaking and not (not_reached or probe_same):
        return False

    if not_reached or probe_same:
        return True
    api_clean = (
        _is_pass(build)
        and api_diff is not None
        and _is_pass(api_diff)
        and api_diff.confidence == Confidence.HIGH
        and not api_diff.tool_failure
    )
    return bool(api_clean and not _pre1_multi_minor_unverified(bundle, test) and not _major_apidiff_unverified(bundle, test, release_notes))


def _merge_reason(base: str, bundle: EvidenceBundle) -> str:
    """Annotate a MERGE clearance reason when the dependency is security-relevant
    but breakability is proven safe: the verdict auto-clears on the breakability
    axis while surfacing the security relevance as a note (not a hard review)."""
    return base + "-security-relevant" if _is_security_sensitive(bundle) else base


def _coerce_enum(enum_type: Type[E], value: Any, field_name: str) -> E:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    allowed = ", ".join(e.value for e in enum_type)
    raise ValidationError(f"{field_name} must be one of: {allowed}")


def _coerce_tuple(values: Iterable[Any], item_type: Type[Any], field_name: str) -> Tuple[Any, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValidationError(f"{field_name} must be iterable")
    out = []
    for item in values:
        if not isinstance(item, item_type):
            raise ValidationError(f"{field_name} entries must be {item_type.__name__}")
        out.append(item)
    return tuple(out)


def _require_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{key} must be a non-empty string")
    return value


def _optional_str(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("expected string")
    return value


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be bool")
    return value


def _optional_bool(value: Any, field_name: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be bool or null")
    return value
