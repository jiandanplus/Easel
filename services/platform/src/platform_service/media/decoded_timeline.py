"""Bounded framehash-v2 parser for completely decoded PCM/rawvideo packets.

Only scalar aggregates survive a row. Time is measured from decoded samples or
complete presentation intervals, never from progress or a declared duration.
"""
from fractions import Fraction
import re


_INTEGER = re.compile(r'0|[1-9][0-9]{0,18}')
_INT64 = (1 << 63) - 1
_HEADER = re.compile(r'#([a-z_]+(?: 0)?): ([\x20-\x7e]{1,128})')


def _integer(text, *, positive=False, maximum=_INT64):
    if not _INTEGER.fullmatch(text):
        raise ValueError
    value = int(text)
    if value > maximum or positive and value == 0:
        raise ValueError
    return value


def time_base(text):
    if not isinstance(text, str) or len(text) > 21 or text.count('/') != 1:
        raise ValueError
    numerator, denominator = text.split('/')
    return Fraction(_integer(numerator, positive=True, maximum=(1 << 31) - 1),
                    _integer(denominator, positive=True, maximum=(1 << 31) - 1))


class DecodedTimeline:
    def __init__(self, *, kind, maximum_ms, sample_rate=None, channels=None,
                 dimensions=None, time_base=None, expected_frames=None, expected_samples=None):
        if kind not in {'audio', 'video'} or type(maximum_ms) is not int or not 1 <= maximum_ms <= 900000:
            raise ValueError
        if kind == 'audio':
            if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000 or type(channels) is not int or not 1 <= channels <= 8:
                raise ValueError
        elif (not isinstance(time_base, Fraction) or time_base <= 0
              or max(time_base.numerator, time_base.denominator) > (1 << 31) - 1
              or not isinstance(dimensions, tuple) or len(dimensions) != 2
              or any(type(v) is not int or not 1 <= v <= 16384 for v in dimensions)
              or dimensions[0] * dimensions[1] > 33554432):
            raise ValueError
        self.expected_frames, self.expected_samples = expected_frames, expected_samples
        self.kind, self.maximum_ms = kind, maximum_ms
        self.sample_rate, self.channels = sample_rate, channels
        self.dimensions, self.expected_tb = dimensions, time_base
        self._carry = b''
        self._headers = {}
        self._header_bytes = self._bytes = self._records = self._ticks = self._samples = 0
        self._first = self._end = None
        self._ready = self._finished = False

    def feed(self, chunk):
        if self._finished or not isinstance(chunk, bytes) or len(chunk) > 4096:
            raise ValueError
        self._bytes += len(chunk)
        self._carry += chunk
        if len(self._carry) > 131072:
            raise ValueError
        while b'\n' in self._carry:
            line, self._carry = self._carry.split(b'\n', 1)
            try:
                self._line(line.decode('ascii', errors='strict'))
            except UnicodeError:
                raise ValueError from None
        if len(self._carry) > (256 if self._ready else 1024):
            raise ValueError
        if self._bytes > (4096 + 256 * self._maximum_records if self._ready else 4096):
            raise ValueError

    def _line(self, line):
        if not line:
            raise ValueError
        if line.startswith('#'):
            if self._ready or len(line) > 1024:
                raise ValueError
            self._header_bytes += len(line) + 1
            if self._header_bytes > 4096:
                raise ValueError
            if line.replace(' ', '') == '#stream#,dts,pts,duration,size,hash':
                self._seal()
                self._ready = True
                return
            match = _HEADER.fullmatch(line)
            if match is None:
                raise ValueError
            key, value = match.groups()
            if key in self._headers or key not in {
                'format', 'version', 'hash', 'software', 'tb 0', 'media_type 0', 'codec_id 0',
                'sample_rate 0', 'channel_layout_name 0', 'dimensions 0', 'sar 0',
            }:
                raise ValueError
            self._headers[key] = value
            return
        if not self._ready or len(line) > 256:
            raise ValueError
        fields = [field.strip(' ') for field in line.split(',')]
        if len(fields) != 6 or re.fullmatch(r'[0-9a-f]{64}', fields[5]) is None:
            raise ValueError
        stream, dts, pts = (_integer(field) for field in fields[:3])
        duration, size = (_integer(field, positive=True) for field in fields[3:5])
        if stream != 0 or dts != pts or pts + duration > _INT64:
            raise ValueError
        if self._first is None:
            self._first = pts
        elif pts != self._end:
            raise ValueError
        self._end = pts + duration
        self._ticks += duration
        self._records += 1
        if self._records > self._maximum_records or self._ticks > self._maximum_ticks:
            raise ValueError
        if self.kind == 'audio':
            if size % (2 * self.channels):
                raise ValueError
            samples = size // (2 * self.channels)
            if duration * self._tb != Fraction(samples, self.sample_rate):
                raise ValueError
            self._samples += samples
            if self._samples > self._maximum_samples:
                raise ValueError
        if self._end - self._first != self._ticks:
            raise ValueError

    def _seal(self):
        common = {'format', 'version', 'hash', 'software', 'tb 0', 'media_type 0', 'codec_id 0'}
        extra = {'sample_rate 0', 'channel_layout_name 0'} if self.kind == 'audio' else {'dimensions 0', 'sar 0'}
        h = self._headers
        if (set(h) != common | extra or h['format'] != 'frame checksums' or h['version'] != '2'
            or h['hash'] != 'SHA256' or re.fullmatch(r'Lavf[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}', h['software']) is None
            or h['media_type 0'] != self.kind or h['codec_id 0'] != ('pcm_s16le' if self.kind == 'audio' else 'rawvideo')):
            raise ValueError
        self._tb = time_base(h['tb 0'])
        self._maximum_ticks = self.maximum_ms * self._tb.denominator // (1000 * self._tb.numerator)
        if self.kind == 'audio':
            if (h['sample_rate 0'] != str(self.sample_rate) or self._tb != Fraction(1, self.sample_rate)
                or re.fullmatch(r'[A-Za-z0-9 ._()+/-]{1,128}', h['channel_layout_name 0']) is None):
                raise ValueError
            self._maximum_samples = self.maximum_ms * self.sample_rate // 1000
            self._maximum_records = self._maximum_samples
        else:
            if h['dimensions 0'] != f'{self.dimensions[0]}x{self.dimensions[1]}' or self._tb != self.expected_tb:
                raise ValueError
            sar = h['sar 0'].split('/')
            if len(sar) != 2:
                raise ValueError
            _integer(sar[0], maximum=(1 << 31) - 1)
            _integer(sar[1], positive=True, maximum=(1 << 31) - 1)
            self._maximum_records = self._maximum_ticks

    def finish(self):
        if self._finished or not self._ready or self._carry or not self._records:
            raise ValueError
        if (self.expected_frames is not None and self._records != self.expected_frames
            or self.expected_samples is not None and self._samples != self.expected_samples):
            raise ValueError
        duration = Fraction(self._samples, self.sample_rate) if self.kind == 'audio' else self._ticks * self._tb
        if duration * 1000 > self.maximum_ms:
            raise ValueError
        milliseconds = duration.numerator * 1000 // duration.denominator
        if milliseconds < 1:
            raise ValueError
        self._finished = True
        return milliseconds


class NativeFrameAudit:
    """Audit decoder frames before filters can scale/remix or reinitialize.

    One compact ffprobe stream covers all validated input stream indices. Only
    scalar totals and the first native format survive a row; no frame list.
    """
    def __init__(self, *, streams, maximum_ms, max_pixels, max_channels):
        if (not isinstance(streams, list) or not 1 <= len(streams) <= 2
            or type(maximum_ms) is not int or not 1 <= maximum_ms <= 900000):
            raise ValueError
        self._streams = {}
        self._maximum_records = 0
        for stream in streams:
            index = stream.get('index')
            if type(index) is not int or not 0 <= index <= (1 << 31) - 1 or index in self._streams:
                raise ValueError
            kind = stream.get('codec_type')
            if kind == 'video':
                width, height = stream.get('width'), stream.get('height')
                if (any(type(v) is not int or not 1 <= v <= 16384 for v in (width, height))
                    or width * height > max_pixels):
                    raise ValueError
                tb = time_base(stream.get('time_base'))
                maximum = maximum_ms * tb.denominator // (1000 * tb.numerator)
                expected = (width, height)
            elif kind == 'audio':
                channels = stream.get('channels')
                if type(channels) is not int or not 1 <= channels <= max_channels:
                    raise ValueError
                rate = _integer(stream.get('sample_rate', ''), positive=True, maximum=192000)
                if rate < 8000:
                    raise ValueError
                maximum = maximum_ms * rate // 1000
                expected = (channels,)
            else:
                raise ValueError
            self._streams[index] = dict(kind=kind, expected=expected, maximum=maximum,
                                        format=None, frames=0, samples=0)
            self._maximum_records += maximum
        self._carry = b''
        self._bytes = 0
        self._finished = False

    def feed(self, chunk):
        if self._finished or not isinstance(chunk, bytes) or len(chunk) > 4096:
            raise ValueError
        self._bytes += len(chunk)
        if self._bytes > self._maximum_records * 1024:
            raise ValueError
        self._carry += chunk
        if len(self._carry) > 131072:
            raise ValueError
        while b'\n' in self._carry:
            line, self._carry = self._carry.split(b'\n', 1)
            if len(line) > 1023:
                raise ValueError
            try:
                self._line(line.decode('ascii', errors='strict'))
            except UnicodeError:
                raise ValueError from None
        if len(self._carry) > 1023:
            raise ValueError

    def _line(self, line):
        # ffprobe emits trailing empty side-data sections when their fields
        # are explicitly excluded. They carry no input metadata; interior
        # empty fields and any named side-data/unknown records are rejected.
        fields = line.rstrip('|').split('|')
        if not fields or fields[0] != 'frame':
            raise ValueError
        values = {}
        for field in fields[1:]:
            if field.count('=') != 1:
                raise ValueError
            key, value = field.split('=')
            if key in values or not value:
                raise ValueError
            values[key] = value
        index = _integer(values.get('stream_index', ''), maximum=(1 << 31) - 1)
        current = self._streams.get(index)
        if current is None or values.get('media_type') != current['kind']:
            raise ValueError
        common = {'media_type', 'stream_index'}
        if current['kind'] == 'video':
            if set(values) != common | {'width', 'height', 'pix_fmt'}:
                raise ValueError
            geometry = tuple(_integer(values[k], positive=True, maximum=16384) for k in ('width', 'height'))
            if geometry != current['expected']:
                raise ValueError
            fmt = values['pix_fmt']
            signature = (fmt,)
        else:
            keys = common | {'sample_fmt', 'nb_samples', 'channels'}
            if set(values) not in (keys, keys | {'channel_layout'}):
                raise ValueError
            channels = _integer(values['channels'], positive=True, maximum=8)
            if (channels,) != current['expected']:
                raise ValueError
            samples = _integer(values['nb_samples'], positive=True)
            current['samples'] += samples
            if current['samples'] > current['maximum']:
                raise ValueError
            fmt = values['sample_fmt']
            layout = values.get('channel_layout')
            if layout is not None and re.fullmatch(r'[A-Za-z0-9 ._()+/:-]{1,128}', layout) is None:
                raise ValueError
            signature = (fmt, layout)
        if re.fullmatch(r'[A-Za-z0-9_]{1,32}', fmt) is None:
            raise ValueError
        if current['format'] is None:
            current['format'] = signature
        elif current['format'] != signature:
            raise ValueError
        current['frames'] += 1
        if current['frames'] > current['maximum']:
            raise ValueError

    def finish(self):
        if self._finished or self._carry or any(not item['frames'] for item in self._streams.values()):
            raise ValueError
        self._finished = True
        return {index: {'frames': item['frames'], 'samples': item['samples']}
                for index, item in self._streams.items()}
