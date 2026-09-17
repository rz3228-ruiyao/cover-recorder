"""Run the native session checks without desktop automation or audio hardware.

conda run -n cover-syncer python tests/verify_native_session.py --exe EXE [--source EDIT]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
import soundfile as sf


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic_fixture(folder: Path) -> Path:
    rate = 48000
    for name, hz, length in (("backing", 337, 4), ("take", 701, 3)):
        time = np.arange(rate * length) / rate
        sf.write(folder / f"{name}.wav", 0.1 * np.sin(2 * np.pi * hz * time), rate, subtype="PCM_24")
    root = ET.Element("EDIT", coverVideoPath=str(folder / "phone-video.mp4"), coverVideoOffsetMs="250")
    ET.SubElement(root, "TRANSPORT", position="0.5")
    inputs = ET.SubElement(root, "INPUTDEVICES")
    device = ET.SubElement(inputs, "INPUTDEVICE", deviceID="test_input", name="Test Input 1")
    ET.SubElement(device, "INPUTDEVICEDESTINATION", targetID="20", targetIndex="0", armed="1")
    for index, name in enumerate(("backing", "take"), 1):
        track = ET.SubElement(root, "TRACK", id=str(index * 10), name=name.title())
        ET.SubElement(track, "PLUGIN", type="volume", id=str(index * 10 + 1), enabled="1", volume="0.5352614521980286")
        ET.SubElement(track, "AUDIOCLIP", id=str(index * 10 + 2), name=name,
                      source=f"{name}.wav", start="0" if index == 1 else "0.6",
                      length="4" if index == 1 else "2.4", offset="0" if index == 1 else "0.1",
                      gain="-3", fadeIn="0.005", fadeOut="0.007", sync="0")
    path = folder / "fixture.tracktionedit"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def check(exe: Path, source: Path, destination: Path) -> dict:
    xml = ET.parse(source)
    files = [source] + [(source.parent / c.attrib["source"]).resolve() for c in xml.iter("AUDIOCLIP")]
    hashes = {str(path): sha(path) for path in files}
    process = subprocess.run([str(exe), "--check-session", str(source), str(destination)],
                             capture_output=True, text=True, timeout=120)
    report_path = destination / "report.json"
    assert report_path.exists(), f"Native check did not report; exit={process.returncode}, stderr={process.stderr}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert process.returncode == 0 and report["success"], report
    assert hashes == {str(path): sha(path) for path in files}, "Original project/media changed"
    renders = sorted(destination.glob("mixdown*.wav"))
    assert len(renders) == 2, renders
    a, sr = sf.read(renders[0], always_2d=True)
    b, sr2 = sf.read(renders[1], always_2d=True)
    assert sr == sr2 and a.shape == b.shape
    assert np.max(abs(a)) > 0.001, "Silent output cannot prove successful recovery"
    error = float(np.max(abs(a - b)))
    assert error <= 2 / (2 ** 23), f"Reload changed rendered samples: {error}"
    report.update(sample_rate=sr, channels=a.shape[1], duration_s=len(a) / sr,
                  max_sample_difference=error, original_hashes_unchanged=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[1] / "tmp" / "native-session-checks"
    base.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    fixture = synthetic_fixture(run)
    reports = {"synthetic": check(args.exe.resolve(), fixture, run / "synthetic")}
    if args.source:
        reports["real_recording"] = check(args.exe.resolve(), args.source.resolve(), run / "real")
    print(json.dumps({"directory": str(run), "reports": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
