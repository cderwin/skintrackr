import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from skintrackr.clients.strava import merge_streams
from skintrackr.gpx import GPX_NAMESPACE, SCHEMA_LOCATION, TRACKPOINT_EXTENSION_NAMESPACE, GpxMetadata, build_gpx
from skintrackr.models import RawStream, StreamPoint, converter
from tests.conftest import ACTIVITY, STREAMS

NS = {"gpx": GPX_NAMESPACE, "gpxtpx": TRACKPOINT_EXTENSION_NAMESPACE}
XSI_SCHEMA_LOCATION = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"
START = datetime(2024, 1, 15, 8, 30, tzinfo=UTC)
POINTS = [
    StreamPoint(time=0, latitude=47.6062, longitude=-122.3321, altitude=56.2, heart_rate=120, temperature=-3),
    StreamPoint(time=5.9, latitude=47.60625, longitude=-122.33215, altitude=57.0, heart_rate=130, temperature=-4),
]

# The Go server's (gpxgo) export of the same activity: tests.conftest.ACTIVITY with its STREAMS.
GO_EXPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<gpx xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1" xmlns="http://www.topografix.com/GPX/1/1"'
    f' xsi:schemaLocation="{SCHEMA_LOCATION}" version="1.1" creator="skintrackr.fly.dev">'
    "<metadata><author></author><time>2024-01-15T08:30:00Z</time></metadata><trk><name>Morning Skin</name>"
    '<type>BackcountrySki</type><trkseg><trkpt lat="47.6062" lon="-122.3321"><ele>56.2</ele>'
    '<time>2024-01-15T08:30:00Z</time></trkpt><trkpt lat="47.60625" lon="-122.33215"><ele>57</ele>'
    "<time>2024-01-15T08:30:05Z</time></trkpt></trkseg></trk></gpx>"
)


def parse(document: bytes | str) -> ET.Element:
    return ET.fromstring(document)


def shape(element: ET.Element) -> list[tuple[str, dict[str, str], str]]:
    """Every element's tag, attributes and text, ignoring whitespace and empty elements Go adds."""
    return [
        (e.tag, dict(e.attrib), (e.text or "").strip())
        for e in element.iter()
        if not (e.tag.endswith("author") and not e.text)
    ]


def test_document_structure() -> None:
    root = parse(build_gpx(POINTS, GpxMetadata(name="Morning Skin", type="BackcountrySki", time=START)))
    assert root.tag == f"{{{GPX_NAMESPACE}}}gpx"
    assert root.attrib["version"] == "1.1"
    assert root.attrib["creator"] == "skintrackr.fly.dev"
    assert root.attrib[XSI_SCHEMA_LOCATION] == SCHEMA_LOCATION
    assert root.findtext("gpx:metadata/gpx:time", namespaces=NS) == "2024-01-15T08:30:00Z"
    assert root.findtext("gpx:trk/gpx:name", namespaces=NS) == "Morning Skin"
    assert root.findtext("gpx:trk/gpx:type", namespaces=NS) == "BackcountrySki"
    points = root.findall("gpx:trk/gpx:trkseg/gpx:trkpt", NS)
    assert [(p.attrib["lat"], p.attrib["lon"], p.findtext("gpx:ele", namespaces=NS)) for p in points] == [
        ("47.6062", "-122.3321", "56.2"),
        ("47.60625", "-122.33215", "57"),
    ]
    assert points[0].find("gpx:extensions", NS) is None


def test_point_times_are_offsets_from_start_truncated_to_seconds() -> None:
    root = parse(build_gpx(POINTS, GpxMetadata(name="", type="", time=START)))
    times = [p.findtext("gpx:time", namespaces=NS) for p in root.iter(f"{{{GPX_NAMESPACE}}}trkpt")]
    assert times == ["2024-01-15T08:30:00Z", "2024-01-15T08:30:05Z"]


def test_numbers_never_use_exponents() -> None:
    root = parse(build_gpx([StreamPoint(latitude=1e-05, longitude=0.0, altitude=1e3)], GpxMetadata("", "", START)))
    point = next(root.iter(f"{{{GPX_NAMESPACE}}}trkpt"))
    assert (point.attrib["lat"], point.attrib["lon"], point.findtext("gpx:ele", namespaces=NS)) == ("0.00001", "0", "1000")


def test_heart_rate_and_temperature_extensions() -> None:
    metadata = GpxMetadata(name="", type="", time=START, use_heart_rate=True, use_temperature=True)
    point = next(parse(build_gpx(POINTS, metadata)).iter(f"{{{GPX_NAMESPACE}}}trkpt"))
    assert point.findtext("gpx:extensions/gpxtpx:hr", namespaces=NS) == "120.000000"
    assert point.findtext("gpx:extensions/gpxtpx:atemp", namespaces=NS) == "-3.000000"


def test_matches_go_export() -> None:
    points = merge_streams(converter.structure(STREAMS, list[RawStream]))
    ours = build_gpx(points, GpxMetadata(name=str(ACTIVITY["name"]), type=str(ACTIVITY["type"]), time=START))
    # Go's output uses the xsi prefix without declaring it, which XML parsers reject; declare it to compare
    go = GO_EXPORT.replace("xsi:schemaLocation", 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation')
    assert shape(parse(ours)) == shape(parse(go))
