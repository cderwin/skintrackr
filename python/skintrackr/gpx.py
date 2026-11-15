"""Build GPX 1.1 documents from activity streams.

The output matches the Go server's (gpxgo) layout: one track with one segment, a `<time>` in the metadata,
and optional Garmin TrackPointExtension `hr`/`atemp` values on each point.
"""

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from attrs import frozen

from skintrackr.models import StreamPoint

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
TRACKPOINT_EXTENSION_NAMESPACE = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
SCHEMA_LOCATION = (
    "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd "
    "http://www.garmin.com/xmlschemas/GpxExtensions/v3 http://www.garmin.com/xmlschemas/GpxExtensionsv3.xsd "
    "http://www.garmin.com/xmlschemas/TrackPointExtension/v1 http://www.garmin.com/xmlschemas/TrackPointExtensionv1.xsd"
)
CREATOR = "skintrackr.fly.dev"


@frozen
class GpxMetadata:
    """Track-level details of a GPX export.

    Attributes:
        name: Track name (the activity's name).
        type: Track type (the activity's sport type).
        time: Activity start; each point's time is this plus its `time` offset in seconds.
        use_heart_rate: Add a `gpxtpx:hr` extension to each point.
        use_temperature: Add a `gpxtpx:atemp` extension to each point.
    """

    name: str
    type: str
    time: datetime
    use_heart_rate: bool = False
    use_temperature: bool = False


def _number(value: float) -> str:
    """Shortest exact decimal for a float, never in exponent form (like Go's `FormatFloat(v, 'f', -1, 64)`)."""
    return format(Decimal(repr(value)).normalize(), "f")


def _time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _child(parent: ET.Element, tag: str, text: str) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = text
    return element


def build_gpx(points: Sequence[StreamPoint], metadata: GpxMetadata) -> bytes:
    """Serialize stream points as a GPX 1.1 document (UTF-8, tab indented).

    Args:
        points: The activity's samples, in order.
        metadata: Track name, type, start time and which extensions to include.

    Returns:
        The document, with an XML declaration.
    """
    # Namespaces are written as literal prefixes so every declaration appears on the root, as in gpxgo's output.
    root = ET.Element(
        "gpx",
        {
            "xmlns": GPX_NAMESPACE,
            "version": "1.1",
            "creator": CREATOR,
            "xmlns:xsi": XSI_NAMESPACE,
            "xsi:schemaLocation": SCHEMA_LOCATION,
            "xmlns:gpxtpx": TRACKPOINT_EXTENSION_NAMESPACE,
        },
    )
    _child(ET.SubElement(root, "metadata"), "time", _time(metadata.time))
    track = ET.SubElement(root, "trk")
    _child(track, "name", metadata.name)
    _child(track, "type", metadata.type)
    segment = ET.SubElement(track, "trkseg")

    for point in points:
        element = ET.SubElement(segment, "trkpt", {"lat": _number(point.latitude), "lon": _number(point.longitude)})
        _child(element, "ele", _number(point.altitude))
        # Go converts the offset with time.Duration(seconds), which truncates fractions
        _child(element, "time", _time(metadata.time + timedelta(seconds=int(point.time))))
        extensions = []
        if metadata.use_heart_rate:
            extensions.append(("gpxtpx:hr", point.heart_rate))
        if metadata.use_temperature:
            extensions.append(("gpxtpx:atemp", point.temperature))
        if extensions:
            container = ET.SubElement(element, "extensions")
            for tag, value in extensions:
                _child(container, tag, f"{value:f}")

    ET.indent(root, space="\t")
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)
