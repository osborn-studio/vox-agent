#!/usr/bin/env python3
"""Create, but do not execute, an isolated Python 3.12 soundfile diagnostic.

This changes the server entry of another scratch copy. It is separate from the
library-only relink output, whose original server remains unchanged.
"""

import argparse
import marshal
import shutil
import subprocess
import sys
from pathlib import Path

from relink_qwen_soundfile import LIBRARY, archive, relink, repack

PROBE = '''import io,json,soundfile,numpy as np
assert soundfile.__libsndfile_version__ == "1.2.2-osvox-relink-test", soundfile.__libsndfile_version__
samples=np.array([0,1000,-1000,32767,-32768],dtype=np.int16)
buffer=io.BytesIO()
soundfile.write(buffer,samples,16000,format="WAV",subtype="PCM_16")
buffer.seek(0)
actual,rate=soundfile.read(buffer,dtype="int16")
assert rate==16000 and np.array_equal(actual,samples)
print(json.dumps({"library_version":soundfile.__libsndfile_version__,"wav_roundtrip":True,"listener_started":False,"model_loaded":False}))
'''

CODEC_PROBE = '''samples=np.tile(np.array([0,1000,-1000,4000,-4000,0],dtype=np.int16),2048)
results=[]
for fmt,subtype in [("WAV","PCM_16"),("FLAC","PCM_16"),("OGG","VORBIS"),("OGG","OPUS"),("MP3",None)]:
    stream=io.BytesIO()
    soundfile.write(stream,samples,16000,format=fmt,subtype=subtype)
    stream.seek(0)
    restored,rate=soundfile.read(stream,dtype="int16")
    assert len(restored)>0 and rate>0
    if fmt in ("WAV","FLAC"):
        assert rate==16000 and np.array_equal(restored,samples)
    results.append({"format":fmt,"subtype":subtype,"decoded_frames":len(restored),"rate":rate})
print(json.dumps({"codec_checks":results,"listener_started":False,"model_loaded":False}))
'''


def prepare(helper, library, output_dir, full_codecs=False):
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise ValueError("diagnostic bytecode must be compiled by CPython 3.12")
    if archive(helper.read_bytes())[1][1] != 312:
        raise ValueError("helper must contain Python 3.12")
    output_dir.mkdir(exist_ok=False)
    relinked = output_dir / "qwen3-asr-server"
    relink(helper, library, relinked)
    diagnostic = output_dir / "soundfile-probe"
    shutil.copy2(relinked, diagnostic)
    subprocess.run(["/usr/bin/codesign", "--remove-signature", str(diagnostic)], check=True)
    raw = diagnostic.read_bytes()
    source = PROBE + (CODEC_PROBE if full_codecs else "")
    replacement = marshal.dumps(compile(source, "osvox-relink-probe", "exec"))
    diagnostic.write_bytes(repack(raw, {b"server": replacement}))
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(diagnostic)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(diagnostic)], check=True)
    before, after = archive(raw)[2], archive(diagnostic.read_bytes())[2]
    if len(before) != len(after) or any(a != b for a, b in zip(before, after)
                                      if a[0].rstrip(b"\0") != b"server"):
        raise ValueError("non-diagnostic archive member changed")
    if not any(entry[0].rstrip(b"\0") == LIBRARY for entry in after):
        raise ValueError("missing native library")
    return diagnostic


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--full-codecs", action="store_true", help="Also check FLAC, Vorbis, Opus and MP3")
    args = parser.parse_args()
    print(prepare(args.helper, args.library, args.output_dir, args.full_codecs))
