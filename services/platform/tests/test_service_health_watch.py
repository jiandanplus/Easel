import importlib.util
from pathlib import Path
import pytest
SCRIPT=Path(__file__).parents[1]/'scripts/check_service_health.py'
@pytest.fixture
def lib():
 s=importlib.util.spec_from_file_location('health_watch',SCRIPT);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def healthy(now=1000000):
 return {'services':{name:'active' for name in ('easel-central-production','easel-console-production','easel-gateway-tunnel','nginx')},'containers':{name:True for name in ('easel-central-pg-20261024','easel-identity-pg-v2-20261024','easel-logto-v2-20261024')},'api':True,'console':True,'backup_timer':True,'backup_result':'success','backup_mtime':now-3600,'disk_percent':30,'memory_available_mib':2000,'tls_expires':{'accounts.drawbookai.cn':now+90*86400,'login.drawbookai.cn':now+90*86400}}

def test_healthy_snapshot_has_no_alerts(lib):assert lib.evaluate(healthy(),1000000)==[]

def test_backup_freshness_failure_and_timer_are_separate_checks(lib):
 x=healthy();x.update(backup_timer=False,backup_result='failed',backup_mtime=1000000-49*3600)
 a=lib.evaluate(x,1000000);assert {v['code'] for v in a}=={'backup_timer_inactive','backup_failed','backup_stale'};assert all(v['severity']=='error' for v in a)

def test_resource_thresholds_and_tls_expiry_do_not_assume_missing_metrics_zero(lib):
 x=healthy();x.update(disk_percent=91,memory_available_mib=200);x['tls_expires']['login.drawbookai.cn']=1000000+2*86400
 a=lib.evaluate(x,1000000);assert {v['code'] for v in a}=={'disk_pressure','memory_pressure','tls_expiring:login.drawbookai.cn'}
 x=healthy();x['disk_percent']=None;a=lib.evaluate(x,1000000);assert any(v['code']=='metric_unavailable:disk' for v in a)

def test_private_api_and_container_failures_are_independent(lib):
 x=healthy();x['api']=False;x['containers']['easel-logto-v2-20261024']=False
 assert {a['code'] for a in lib.evaluate(x,1000000)}=={'api_unhealthy','container_inactive:easel-logto-v2-20261024'}

def test_alert_signature_changes_only_on_actionable_state_changes(lib):
 a=[{'code':'memory_pressure','severity':'warning'}]
 assert lib.alert_signature(a)==lib.alert_signature(list(reversed(a)))
 assert lib.alert_signature(a)!=lib.alert_signature([])
 assert lib.alert_signature(a)!=lib.alert_signature([{'code':'memory_pressure','severity':'error'}])
