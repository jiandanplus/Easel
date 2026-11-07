"""Exact equality with only the three approved positional substitutions."""
import copy
from dataclasses import dataclass
from .contracts import ComparisonContext, digest, require, validate_observation


@dataclass(frozen=True)
class ComparisonProof:
    equal: bool
    schema_equal: bool
    roles_equal: bool
    rows_equal: bool
    copy_equal: bool
    sequences_equal: bool
    source_sha256: str
    target_sha256: str
    source_normalized_sha256: str
    target_normalized_sha256: str


def normalize_observation(value: dict, context: ComparisonContext) -> dict:
    validated = validate_observation(value, context)
    normalized = copy.deepcopy(validated)
    normalized["schema"]["database"][0][0] = "<trusted-database>"
    for row in normalized["roles"]["memberships"]:
        row[6] = "<membership-xmin>"
    for row in normalized["roles"]["credentials"]:
        row[2] = "<credential-xmin>"
    return normalized


def compare_observations(source: dict, target: dict, source_context: ComparisonContext,
                         target_context: ComparisonContext) -> ComparisonProof:
    require(source_context.source_kind == target_context.source_kind)
    require(source_context.evidence_mode == target_context.evidence_mode)
    require(source_context.profile == target_context.profile)
    require(source_context.role_names == target_context.role_names)
    require(source_context.allowed_grantors == target_context.allowed_grantors)
    require(source_context.allowed_guc_keys == target_context.allowed_guc_keys)
    require(source_context.row_order_pins == target_context.row_order_pins)
    left = normalize_observation(source, source_context)
    right = normalize_observation(target, target_context)
    equalities = [left[k] == right[k] for k in ("schema","roles","rows","copy","sequences")]
    return ComparisonProof(all(equalities), *equalities, digest(source), digest(target), digest(left), digest(right))
