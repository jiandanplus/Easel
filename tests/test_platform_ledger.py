import pytest
from concurrent.futures import ThreadPoolExecutor
from easel_platform import Settings, Store
from easel_platform.auth import PlatformError
from easel_platform.ledger import Ledger

@pytest.fixture
def setup(tmp_path):
    s=Store(Settings(tmp_path/'db'),clock=lambda:1000)
    u=s.register('a@example.test','long-password-123')
    s.create_plan('p','trial',10000,1,1,['alias'])
    g=s.grant('a@example.test','p','grant')['id']
    return s,u,g,Ledger(s)

def test_atomic_reserve_settle_and_idempotency(setup):
    s,u,g,l=setup
    args=dict(owner=u['account']['id'],grant=g,key='same',body_hash='h',tokens=100)
    with ThreadPoolExecutor(2) as pool:
        def attempt(_):
            try: return l.reserve(**args)
            except PlatformError as e: return e.code
        results=list(pool.map(attempt,range(2)))
    assert sum(isinstance(x,dict) for x in results)==1
    r=next(x for x in results if isinstance(x,dict))
    assert l.snapshot(args['owner'])['reserved_tokens']==100
    l.dispatch(r['id']);l.settle(r['id'],10,20);l.settle(r['id'],10,20)
    assert l.snapshot(args['owner'])['used_tokens']==30
    with pytest.raises(PlatformError): l.reserve(**{**args,'body_hash':'other'})

def test_restart_pending_and_reconciliation(setup):
    s,u,g,l=setup
    r=l.reserve(owner=u['account']['id'],grant=g,key='x',body_hash='h',tokens=100)
    l.dispatch(r['id'])
    other=Ledger(Store(s.settings,clock=s.clock));other.recover()
    assert other.snapshot(u['account']['id'])['pending_reconciliation']==1
    other.reconcile(r['id'],5,6,'op');other.reconcile(r['id'],5,6,'op')
    assert other.snapshot(u['account']['id'])['used_tokens']==11
    with pytest.raises(PlatformError): other.reconcile(r['id'],5,7,'op')

def test_budget_and_concurrency(setup):
    s,u,g,l=setup
    base=dict(owner=u['account']['id'],grant=g,body_hash='h')
    with pytest.raises(PlatformError):l.reserve(**base,key='big',tokens=10001)
    r=l.reserve(**base,key='one',tokens=100)
    with pytest.raises(PlatformError):l.reserve(**base,key='two',tokens=100)
    l.pending(r['id'])
    l.reserve(**base,key='two',tokens=100)
    assert l.snapshot(base['owner'])['reserved_tokens']==200


def test_separate_connections_cannot_exceed_budget(setup):
    s,u,g,l=setup
    with s.transaction() as db:db.execute('UPDATE grants SET token_quota=150,concurrency_limit=5')
    other=Ledger(Store(s.settings,clock=s.clock))
    with ThreadPoolExecutor(2) as pool:
        def attempt(pair):
            ledger,key=pair
            try:return ledger.reserve(owner=u['account']['id'],grant=g,key=key,body_hash='h',tokens=100)
            except PlatformError as e:return e.code
        results=list(pool.map(attempt,[(l,'a'),(other,'b')]))
    assert sum(isinstance(r,dict) for r in results)==1
    assert l.snapshot(u['account']['id'])['reserved_tokens']==100

def test_operator_constructor_does_not_recover_live_request(setup):
    s,u,g,l=setup
    r=l.reserve(owner=u['account']['id'],grant=g,key='a',body_hash='h',tokens=100);l.dispatch(r['id'])
    other=Ledger(Store(s.settings,clock=s.clock))
    assert other.snapshot(u['account']['id'])['usage'][0]['state']=='dispatched'


def test_reconcile_cli_and_server_lifespan_recovery(setup,capsys):
    import asyncio,json
    from easel_platform import create_app
    from easel_platform.__main__ import main
    s,u,g,l=setup
    r=l.reserve(owner=u['account']['id'],grant=g,key='cli',body_hash='h',tokens=100);l.dispatch(r['id'])
    # Merely constructing a second app must not mutate a live reservation.
    app=create_app(s.settings,clock=s.clock)
    assert l.snapshot(u['account']['id'])['usage'][0]['state']=='dispatched'
    async def startup():
        async with app.router.lifespan_context(app):pass
    asyncio.run(startup())
    main(['reconcile','--database',str(s.settings.database),'--request-id',r['id'],'--input-tokens','4','--output-tokens','6','--key','operator'])
    assert json.loads(capsys.readouterr().out)['state']=='settled'
    assert l.snapshot(u['account']['id'])['used_tokens']==10

def test_database_never_persists_request_content_or_task_secret(setup):
    s,u,g,l=setup
    r=l.reserve(owner=u['account']['id'],grant=g,key='safe',body_hash='hash-only',tokens=100)
    with s.transaction() as db:
        columns=[row[1] for row in db.execute('PRAGMA table_info(requests)')]
    assert not any(c in columns for c in ('input','body','prompt','response','text','token','authorization'))
