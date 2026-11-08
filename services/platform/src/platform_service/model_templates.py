"""What each kind of model needs, so the console only asks for names and prices.

Only capabilities with adapters that speak the deployed gateway's standard routes.
"""
TEXT = {"capability": "text", "label": "文本", "feature": "text", "kind": "execution",
        "limits": {"max_input_bytes": 1048576, "max_input_tokens": 16384, "max_output_tokens": 1024},
        "meters": [{"key": "input", "label": "每百万输入 token"}, {"key": "output", "label": "每百万输出 token"}]}

MEDIA = {
    "image.generate": {
        "label": "生图", "feature": "image", "unit": "image", "basis": "verified_delivery_v1", "per": 1, "meter_label": "每张",
        "max_units": 1, "limits": {"max_input_bytes": 65536, "max_assets": 0, "max_artifact_bytes": 20971520},
        "parameters": {"type": "object", "required": ["n", "size"], "additionalProperties": False, "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 1},
            "size": {"type": "string", "enum": ["1024x1024", "1024x1536", "1536x1024"]}}}},
    "video.generate": {
        "label": "视频", "feature": "video", "unit": "video_ms", "basis": "verified_delivery_v1", "per": 1000, "meter_label": "每秒",
        "max_units": 15000, "limits": {"max_input_bytes": 10485760, "max_assets": 1, "max_artifact_bytes": 67108864},
        "parameters": {"type": "object", "required": ["seconds", "size"], "additionalProperties": False, "properties": {
            "seconds": {"type": "integer", "minimum": 1, "maximum": 15},
            "size": {"type": "string", "enum": ["1280x720", "720x1280", "1024x1024"]}}}},
    "music.generate": {
        "label": "音乐", "feature": "music", "unit": "audio_clip", "basis": "verified_delivery_v1", "per": 1, "meter_label": "每首",
        "max_units": 2, "limits": {"max_input_bytes": 131072, "max_assets": 0, "max_artifact_bytes": 67108864},
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "lyrics": {"type": "string", "maxLength": 65536}, "title": {"type": "string", "maxLength": 200},
            "instrumental": {"type": "boolean"}}}},
    "audio.speech": {
        "label": "语音合成", "feature": "voice", "unit": "unicode_scalar", "basis": "verified_input_v1", "per": 10000, "meter_label": "每万字",
        "max_units": 10000, "limits": {"max_input_bytes": 131072, "max_assets": 0, "max_artifact_bytes": 67108864},
        "parameters": {"type": "object", "required": ["voice"], "additionalProperties": False, "properties": {
            "voice": {"type": "string", "maxLength": 128},
            "response_format": {"type": "string", "enum": ["mp3", "wav"]},
            "speed_percent": {"type": "integer", "minimum": 50, "maximum": 200}}}},
}


def public_templates():
    items = [{key: TEXT[key] for key in ("capability", "label", "feature", "kind", "meters")}]
    for capability, value in MEDIA.items():
        items.append({"capability": capability, "label": value["label"], "feature": value["feature"], "kind": "media",
                      "meters": [{"key": value["unit"], "label": value["meter_label"]}]})
    return items


def points_to_part(points, per):
    """Whole-unit points as an exact fraction of microcredits per metered unit."""
    from fractions import Fraction
    from decimal import Decimal, InvalidOperation
    from .errors import PlatformError
    try:
        if not isinstance(points, str) or len(points) > 16:
            raise ValueError
        value = Fraction(Decimal(points)) * 1000000 / per
    except (ValueError, InvalidOperation):
        raise PlatformError(422, "invalid_console_command") from None
    if value < 0 or value.numerator > 9 * 10**18 or value.denominator > 9 * 10**18:
        raise PlatformError(422, "invalid_console_command")
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def build_rate(capability, version, prices):
    from .errors import PlatformError
    if not isinstance(prices, dict):
        raise PlatformError(422, "invalid_console_command")
    if capability == "text":
        if set(prices) != {"input", "output"}:
            raise PlatformError(422, "invalid_console_command")
        return {"version": version, "input": points_to_part(prices["input"], 1), "output": points_to_part(prices["output"], 1)}
    template = MEDIA.get(capability)
    if template is None or set(prices) != {template["unit"]}:
        raise PlatformError(422, "invalid_console_command")
    return {"version": version, "meters": [{"unit": template["unit"], "basis": template["basis"],
                                            **points_to_part(prices[template["unit"]], template["per"])}]}
