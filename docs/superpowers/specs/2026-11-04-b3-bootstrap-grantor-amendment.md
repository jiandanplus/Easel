# B3 original bootstrap grantor — approved isolated-target amendment

Status: user approved the narrowly scoped isolated-target exception on 2026-11-04. The approved proposal bytes are preserved in outputs/_run/b3-bootstrap-grantor-amendment-20261104/approved-proposal.md and bound by approval.safe.json. This authorizes development and scoped synthetic verification after implementation review, not production action or a correctness claim.

## Problem and binding requirement

Approved 2026-11-03 design section3 says “拒绝尝试修改新bootstrap或未列入的内建角色”; section6 requires exact memberships/grantor/admin/inherit/set and allows only three stated comparison normalizations. PostgreSQL16 grant integrity treats the bootstrap superuser OID10 specially. Recreating a source bootstrap name as an ordinary new SUPERUSER does not reproduce that identity. Adding temporary ADMIN memberships cannot be assumed to leave exactly the original membership graph after removal. Affected mandatory profiles must not be silently skipped.

Primary source research (not an actual SQL test): PostgreSQL REL_16_15 `check_role_grantor` checks explicit grantors through `select_best_admin` except BOOTSTRAP_SUPERUSERID; that helper explicitly disregards generic SUPERUSER status. See https://raw.githubusercontent.com/postgres/postgres/REL_16_15/src/backend/commands/user.c and https://raw.githubusercontent.com/postgres/postgres/REL_16_15/src/backend/utils/adt/acl.c .

## Proposed narrowly scoped exception

Only in a brand-new, independently qualified B3 synthetic target, permit a one-time controlled bootstrap handover when an independently trusted source context proves that the required original grantor is the source OID10 bootstrap. It is not triggered by archive claims alone. Source databases, production servers, old failed targets and built-in non-bootstrap roles remain outside scope. No direct system-catalog writes, grantor substitution or new comparator normalization.

Keep the independently generated operational bootstrap name B and its private authentication secret. Initially B is initdb OID10 as today. Before creating any restored roles or content, create a uniquely named short-lived helper H under a durable one-shot operation. H has only the temporary trusted SQL superuser authority necessary for this target-local handover, no external listener or published endpoint. A separate H session performs one transaction: assert original instance/CID/system id, exact B OID10 and approved source grantor A absence; rename B to A; create a new operational B using the already bound private bootstrap secret and original operational attributes; restore A’s independently authorized source attributes and exact verifier, force NOLOGIN until the approved final login phase. Do not attempt to rename the current session role. Source credentials are not used as an authentication fallback.

After confirmed commit, reconnect as the new B, verify OID10=A and B has a distinct newly assigned OID, test original operational authentication and wrong-password rejection, then drop H and prove zero helper roles/memberships/objects. Authenticate and inspect only original target resources. A is excluded from ordinary CREATE ROLE replay because its exact reconstruction is separately proven. All original restored names, grantors and options remain strict. The operational B remains independent of the restored role set; additional operational identity is explicitly recorded outside the compared restored-role inventory.

No helper or handover proof can grant wider source-role privileges than the independently approved context. Refuse unsupported source bootstrap attributes rather than changing them. Do not clear NOLOGIN until content/control/role proof gates permit it. Do not claim exact initial bootstrap OID continuity for operational B after handover; record old and new OIDs and phase transition privately. Existing factory instance/storage/network/resource checks remain unchanged; add a separately versioned role identity proof instead of relaxing their comparison.

## Additional binding details from independent review

Before helper creation, validate the complete A/B/H namespace, attributes, source-bootstrap OID provenance, independent authorization, authentication material and expected dependency map. H is a fresh independently named LOGIN superuser with fixed private SCRAM credential, NOINHERIT, no memberships, no role GUC and no database/object ownership; its temporary authority exists only on this isolated target. Secret delivery is private FD/protocol only, never argv, environment or safe logs. H creation, handover and H removal are three separately journaled commits, not a single atomic operation.

Before handover, close and independently prove termination of all old-B sessions. An old session retains OID10 despite creating a new role with the same B name; it must not run subsequent phases under a mislabeled identity. During handover only the exact H session is allowed. After confirmed commit, close H and prove its sessions are terminal before the new-B session drops H. Role deletion alone does not prove backend termination. A lost or unaccounted connection keeps the operation UNKNOWN; no unsolicited termination or compensation is implied.

New-B authentication alone cannot distinguish handover, because name/secret intentionally match. Each later privileged phase must bind exact (A,OID10), (B,new non10 OID), expected attributes/private verifier hashes and unchanged instance identity. New-B-only execution is enforced through actual current_user/session_user OIDs. Do not use a surviving old-B/H connection for follow-up.

Record initialization database/object dependency inventory before handover. OID10-owned initialization objects must retain the same OID owner and therefore display A after rename; this expected private dependency proof is distinct from restored-business equality. Do not REASSIGN OWNED or silently rename databases/objects. Add a separate full-cluster final scan outside the filtered restored-role comparison: no H role, H memberships, object dependencies, sessions or privileges; operational B has only its prescribed extra identity. Unknown or extra roles/dependencies reject finalization. The existing three normalization slots do not change.

## Failure and verification

Record durable claim and exact private before-state before any helper creation. Constructor/connection/DDL/COMMIT ambiguity consumes the operation and retains original target/helper/session custody as UNKNOWN; no retry, automatic compensation, role deletion or bootstrap recreation. Observation-only recovery may inspect exact original postconditions once work is known terminal. Keep control closed. No final success if a helper remains or identities are ambiguous.

Before use: independent specification and source review; actual PostgreSQL16.15 cases for default bootstrap grantor membership, explicit non-bootstrap grantor, admin/inherit/set combinations, correct and wrong credentials, helper cleanup, source preservation, duplicate/late operation refusal, crash before/after commit and strict catalog comparison. Demonstrate naïve ordinary-superuser recreation cannot satisfy the relevant case. No original failed run is promoted to PASS. Preserve complete mandatory central/public identity/Logto matrix.

## User approval

User answer: “批准该隔离目标例外，继续开发和验证（推荐）”. The exact pre-acknowledgement proposal is retained and hashed in approval.safe.json. The original no-bootstrap-modification rule continues outside this narrow exception. No handover has been implemented or executed yet; independent code review and actual PostgreSQL verification remain required.
