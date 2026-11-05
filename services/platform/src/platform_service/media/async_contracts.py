"""Private normalized observations from explicit, fixed asynchronous adapters."""
from dataclasses import dataclass,field


@dataclass(frozen=True)
class ArtifactSource:
    source_id:str
    kind:str
    location:str=field(repr=False)
    mime:str|None=None


@dataclass(frozen=True)
class Observation:
    provider_job_id:str
    state:str
    sources:tuple[ArtifactSource,...]
    quantities:dict[str,int]
    usage_hash:str|None
    response_hash:str
    reported_facts:dict=field(default_factory=dict,repr=False)


@dataclass(frozen=True)
class Submission:
    provider_job_id:str
    observation:Observation|None=None
