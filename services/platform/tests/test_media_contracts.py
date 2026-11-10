"""Closed media definitions, request boundaries and exact non-token metering."""
import copy
import pytest
from platform_service.errors import PlatformError


def definition(capability='image.generate'):
    return dict(id='fixture-image', version='1', name='Fixture image', capability=capability,
                feature='media', products=['easel', 'second'], test_only=True,
                provider_id='fixture', provider_version='1', adapter='openai_images_v1', protocol_version='1',
                verification='fixture', verification_ref='synthetic-only',
                rate={'version':'media-1','meters':[{'unit':'image','basis':'verified_delivery_v1','numerator':'3','denominator':'2'}]},
                limits={'max_input_bytes':65536,'max_assets':4,'max_artifact_bytes':8388608,'max_units':{'image':4}},
                parameters={'type':'object','properties':{'n':{'type':'integer','minimum':1,'maximum':4},
                            'size':{'type':'string','enum':['1024x1024']}},'required':[],'additionalProperties':False})


def test_closed_capability_definition_and_positive_bounded_limits():
    from platform_service.media.contracts import validate_definition, CAPABILITIES
    assert len(CAPABILITIES) == 9
    assert validate_definition(definition()) == definition()
    for patch in [dict(capability='text'),dict(test_only=False),dict(protocol_version='2'),dict(rate={'version':'x','meters':[]}),
                  dict(limits={**definition()['limits'],'max_units':{'image':0}}),dict(secret='not-allowed')]:
        with pytest.raises(PlatformError): validate_definition({**definition(),**patch})


@pytest.mark.parametrize('value', [True, -1, 1.2, '1', 2**63])
def test_quantity_contract_rejects_non_integer_and_unbounded_values(value):
    from platform_service.media.rates import cost
    with pytest.raises(PlatformError): cost(definition()['rate'],{'image':value})


def test_rates_sum_all_exact_fractions_and_round_once():
    from platform_service.media.rates import cost, normalize
    rate={'version':'mixed','meters':[{'unit':'input_token','basis':'provider_usage_v1','numerator':'1','denominator':'3'},
                                  {'unit':'output_token','basis':'provider_usage_v1','numerator':'2','denominator':'3'}]}
    assert cost(normalize(rate),{'input_token':1,'output_token':1})==1
    assert cost(normalize(rate),{'input_token':2,'output_token':0})==1
    assert cost(normalize(rate),{'input_token':0,'output_token':0})==0
    for quantities in [{'input_token':1},{'input_token':1,'output_token':1,'image':1}]:
        with pytest.raises(PlatformError): cost(rate,quantities)


def test_duplicate_units_unknown_basis_zero_rate_and_overflow_are_rejected():
    from platform_service.media.rates import normalize, cost
    rate=definition()['rate']; meter=rate['meters'][0]
    for meters in [[meter,meter],[{**meter,'basis':'client_reported'}],[{**meter,'denominator':'0'}],[{**meter,'numerator':'0'}],
                   [{**meter,'unit':'tokens'}],[{**meter,'numerator':True}]]:
        with pytest.raises(PlatformError): normalize({'version':'v','meters':meters})
    with pytest.raises(PlatformError): cost({'version':'v','meters':[{**meter,'numerator':str(2**63-1)}]},{'image':3})


def test_parameters_are_bounded_explicit_schema_not_free_objects():
    from platform_service.media.contracts import validate_definition
    for schema in [{'type':'object','properties':{},'additionalProperties':True},
                   {'type':'object','properties':{'x':{'type':'object'}},'additionalProperties':False},
                   {'type':'object','properties':{'x':{'type':'string'}},'additionalProperties':False},
                   {'type':'object','properties':{'x':{'type':'integer','minimum':0}},'additionalProperties':False},
                   {**definition()['parameters'],'unknown':True}]:
        with pytest.raises(PlatformError): validate_definition({**definition(),'parameters':schema})


def test_request_never_accepts_amount_wallet_provider_url_or_unpublished_parameters():
    from platform_service.media.contracts import validate_request
    value={'model':'fixture-image','capability':'image.generate','prompt':'合成测试','assets':[],'parameters':{'n':1}}
    assert validate_request(definition(),value)['prompt']=='合成测试'
    for patch in [{'amount':0},{'billing_account_id':'other'},{'provider_url':'https://private.invalid'},
                  {'capability':'video.generate'},{'parameters':{'n':True}},{'parameters':{'n':5}},
                  {'parameters':{'url':'https://private.invalid'}},{'assets':['../private']}]:
        with pytest.raises(PlatformError): validate_request(definition(),{**value,**patch})
    before=copy.deepcopy(value); validate_request(definition(),value); assert value==before


def test_unicode_scalar_meter_never_counts_utf8_bytes_or_silently_normalizes_text():
    from platform_service.media.rates import unicode_scalars, seconds_to_milliseconds
    assert unicode_scalars('A😀e\u0301')==4
    assert unicode_scalars('é')==1
    with pytest.raises(PlatformError): unicode_scalars('\ud800')
    assert seconds_to_milliseconds('1.0001',rounding='ceil')==1001
    assert seconds_to_milliseconds('1.0009',rounding='floor')==1000
    for value in [1.2,'NaN','-1','Infinity']:
        with pytest.raises(PlatformError): seconds_to_milliseconds(value,rounding='ceil')


def test_unhashable_public_values_are_rejected_with_contract_errors():
    from platform_service.media.contracts import validate_definition, validate_request
    for patch in [{'products':[{}]}, {'parameters':{**definition()['parameters'],'required':[{}]}}]:
        with pytest.raises(PlatformError):validate_definition({**definition(),**patch})
    with pytest.raises(PlatformError):
        validate_request(definition(),{'model':'fixture-image','capability':'image.generate','prompt':'x','assets':[{}]})


def test_explicit_legacy_variation_accepts_asset_without_ignored_prompt():
    from platform_service.media.contracts import validate_request
    request={'model':'fixture-image','capability':'image.variation','assets':['owned-image'],'parameters':{'n':1}}
    assert validate_request(definition('image.variation'),request)['prompt']==''


@pytest.mark.parametrize("value,rounding,expected", [
    ("1.0000000000000000000000000000000001", "ceil", 1001),
    ("0.9999999999999999999999999999999999", "floor", 999),
    ("1e-999999", "ceil", 1), ("1e-999999", "floor", 0),
    ("9223372036854775.807", "ceil", 9223372036854775807),
    ("9223372036854775.8079", "floor", 9223372036854775807),
])
def test_duration_scaling_is_exact_and_bounded_before_integer_conversion(value, rounding, expected):
    from platform_service.media.rates import seconds_to_milliseconds
    assert seconds_to_milliseconds(value, rounding=rounding) == expected


@pytest.mark.parametrize("value", ["1e999999", "9223372036854775.808", "sNaN", "1e9999999999999999999999999999999999999999999999"])
def test_duration_overflow_is_a_safe_contract_error(value):
    from platform_service.media.rates import seconds_to_milliseconds
    with pytest.raises(PlatformError) as error:
        seconds_to_milliseconds(value, rounding="ceil")
    assert error.value.code == "invalid_media_duration"
