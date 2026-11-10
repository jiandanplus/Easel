"""Closed, private normalized supplier facts. No bodies or resource identifiers."""
from decimal import Decimal
from ..errors import PlatformError
from .rates import seconds_to_milliseconds


def validate_protocol_facts(namespace, facts):
    def integer(value):
        if type(value) is not int or not 0<=value<=9223372036854775807:raise ValueError
    try:
        if not isinstance(facts,dict):raise ValueError
        if namespace=='openai_transcriptions_v1':
            if facts.get('type')=='tokens':
                if set(facts)!={'type','input_tokens','output_tokens','total_tokens'}:raise ValueError
                for key in ('input_tokens','output_tokens','total_tokens'):integer(facts[key])
                if facts['total_tokens']!=facts['input_tokens']+facts['output_tokens']:raise ValueError
            elif facts.get('type')=='duration':
                if set(facts)!={'type','seconds','rounding'} or facts['rounding']!='ceil_milliseconds_v1':raise ValueError
                if not isinstance(facts['seconds'],str) or len(facts['seconds'])>128:raise ValueError
                seconds_to_milliseconds(facts['seconds'],rounding='ceil')
            else:raise ValueError
        elif namespace=='newapi_minimax_voice_v1':
            if set(facts)-{'usage_characters','audio_length_ms'} or 'usage_characters'not in facts:raise ValueError
            integer(facts['usage_characters'])
            if 'audio_length_ms'in facts:
                value=facts['audio_length_ms']
                if not isinstance(value,str) or len(value)>128 or not Decimal(value).is_finite() or not 0<=Decimal(value)<=900000:raise ValueError
        elif namespace=='newapi_dashscope_voice_v1':
            if set(facts)!={'characters'}:raise ValueError
            integer(facts['characters'])
        else:raise ValueError
    except (ValueError,ArithmeticError,PlatformError):raise PlatformError(422,'invalid_media_usage_facts')from None
    return facts
