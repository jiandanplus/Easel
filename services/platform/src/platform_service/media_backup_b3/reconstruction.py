"""Internal original-owner role facade. No DATA result can mint this proof."""
from .contracts import require

class RoleCreationProof:
    __slots__=('_runtime','_phase')
    def __init__(self,*args,**kwargs):raise TypeError('original owner only')
    def __setattr__(self,name,value):raise TypeError('sealed proof')
    def __copy__(self):raise TypeError('original proof only')
    def __deepcopy__(self,memo):raise TypeError('original proof only')
    def __reduce_ex__(self,protocol):raise TypeError('original proof only')
    def __repr__(self):return '<owned role creation proof>'
    def verify(self):
        runtime=self._runtime;runtime.verify()
        require(any(p is self for p in runtime._proofs) and runtime.committed(self._phase))
        runtime.owner._guard_live();runtime.owner._audit_execution()
        require(self._phase.worker_slot.terminal)
        return None


def restore_roles(target,verified,kind):
    from .execution import _FACTORIES,RootExecutionOwner
    from .target_factory import TargetHandle
    from .reconstruction_contract import Operation
    require(type(target) is TargetHandle)
    owner=_FACTORIES.get(id(target._attempt._factory))
    require(type(owner) is RootExecutionOwner and verified is owner._input)
    require(kind in ('central','identity') and owner._targets[kind].original_handle is target)
    runtime=owner._reconstruction_runtime()
    if runtime is None:runtime=owner._begin_reconstruction(target,verified,kind)
    require(not any(p.kind==kind for p in runtime.phases))
    runtime._dispatch(kind)
    # H phases have their own finite commits; never skip one to reach roles.
    while runtime.next_operation(kind) in (Operation.H_CREATE,Operation.HANDOVER,Operation.H_DROP):
        runtime._dispatch(kind)
    phase=runtime._dispatch(kind)
    require(phase.operation is Operation.ROLES_APPLY)
    return runtime._role_proof(phase)
