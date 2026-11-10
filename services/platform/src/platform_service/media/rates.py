"""Exact media quantities; the complete operation is rounded upward once."""
from decimal import Decimal, DecimalException
from fractions import Fraction
from ..credits import integer, bounded
from ..errors import PlatformError

UNITS = frozenset({'audio_clip','image','video_ms','audio_ms','unicode_scalar','registration','input_token','output_token'})
BASES = frozenset({'provider_usage_v1','verified_delivery_v1','verified_input_v1'})


def monetary(value):
    if isinstance(value,str) and value.isascii() and value.isdecimal() and len(value)<=19:
        value=int(value)
    return integer(value)


def normalize(value):
    if not isinstance(value,dict) or set(value)!={'version','meters'}:
        raise PlatformError(422,'invalid_media_rate')
    bounded(value['version'])
    meters=value['meters']
    if not isinstance(meters,list) or not 1<=len(meters)<=len(UNITS):
        raise PlatformError(422,'invalid_media_rate')
    result=[];seen=set()
    for part in meters:
        if not isinstance(part,dict) or set(part)!={'unit','basis','numerator','denominator'}:
            raise PlatformError(422,'invalid_media_rate')
        unit,basis=part['unit'],part['basis']
        if not isinstance(unit,str) or unit not in UNITS or not isinstance(basis,str) or basis not in BASES or unit in seen:
            raise PlatformError(422,'invalid_media_rate')
        if (unit=='audio_clip' and basis!='verified_delivery_v1' or unit in {'input_token','output_token'} and basis!='provider_usage_v1'
                or basis=='verified_input_v1' and unit not in {'audio_ms','video_ms','unicode_scalar'}
                or basis=='verified_delivery_v1' and unit not in {'image','audio_clip','audio_ms','video_ms','registration'}):
            raise PlatformError(422,'invalid_media_rate_basis')
        numerator=monetary(part['numerator']);denominator=integer(monetary(part['denominator']),positive=True)
        seen.add(unit);result.append({'unit':unit,'basis':basis,'numerator':str(numerator),'denominator':str(denominator)})
    if not any(int(part['numerator']) for part in result):
        raise PlatformError(422,'invalid_media_rate')
    return {'version':value['version'],'meters':result}


def quantities(rate,value):
    expected={part['unit'] for part in normalize(rate)['meters']}
    if not isinstance(value,dict) or set(value)!=expected:
        raise PlatformError(422,'invalid_media_quantities')
    return {unit:integer(amount) for unit,amount in value.items()}


def cost(rate,value):
    rate=normalize(rate);value=quantities(rate,value)
    total=sum((Fraction(int(part['numerator'])*value[part['unit']],int(part['denominator'])) for part in rate['meters']),Fraction())
    return integer((total.numerator+total.denominator-1)//total.denominator)


def unicode_scalars(text):
    if not isinstance(text,str) or any(0xD800<=ord(character)<=0xDFFF for character in text):
        raise PlatformError(422,'invalid_media_text')
    return integer(len(text))


def seconds_to_milliseconds(value,*,rounding):
    if not isinstance(value,str) or len(value)>64 or rounding not in {'ceil','floor'}:
        raise PlatformError(422,'invalid_media_duration')
    try:
        result=Decimal(value)
        if not result.is_finite() or result<0:raise ValueError()
        # Decimal multiplication obeys the process precision and can round away
        # the very fraction deciding ceil/floor. Scale its exact finite tuple
        # instead, bounding magnitude before constructing powers or integers.
        parts=result.as_tuple();digits=parts.digits;exponent=parts.exponent+3
        coefficient=0
        for digit in digits:coefficient=coefficient*10+digit
        if not coefficient:return 0
        magnitude=len(digits)+exponent
        if magnitude>19:raise ValueError()
        if magnitude<=0:return 1 if rounding=='ceil' else 0
        if exponent>=0:rounded=coefficient*10**exponent
        else:
            denominator=10**(-exponent)
            rounded=(coefficient+denominator-1)//denominator if rounding=='ceil' else coefficient//denominator
        if rounded>9223372036854775807:raise ValueError()
        return integer(rounded)
    except (DecimalException,ValueError,OverflowError):
        raise PlatformError(422,'invalid_media_duration') from None
