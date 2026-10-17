from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

root = Path(__file__).resolve().parent
destination = root.parent / "packages"
destination.mkdir(parents=True, exist_ok=True)

for existing in destination.glob("*.zip"):
    existing.unlink()

for track in ("wazuh", "graylog", "victorialogs", "quickwit", "splunk"):
    packages = ["vector", "wazuh"] if track == "wazuh" else ["vector"]
    for package in packages:
        with ZipFile(destination / f"{track}-{package}.zip", "w", ZIP_DEFLATED) as archive:
            track_file = ZipInfo("track.txt")
            track_file.compress_type = ZIP_DEFLATED
            track_file.external_attr = (0o100644 << 16)
            archive.writestr(track_file, track)
            for filename in ("install.sh", "uninstall.sh"):
                source = root / package / filename
                item = ZipInfo(filename)
                item.compress_type = ZIP_DEFLATED
                item.external_attr = (0o100755 << 16)
                archive.writestr(item, source.read_bytes())
