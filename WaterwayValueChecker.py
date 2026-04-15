# ChatGPT-generated code
# prompted and tested by Jeff Meyer
# using JOSM's Scripting Plugin + the jython scripting engine
# users should verify proper function on their own before uploading changes to OHM
# this script reviews objects with waterway=river tags
# and attempts to look up their corresponding entry in Wikidata
# if it fails, the object is changed to waterway=stream and waterway:source explains why
# if it succeeds, a wikidata=qid tag is added
# a panel allows suggested changes to be reviewed before adding them

# JOSM Scripting Plugin - Python (Jython)
# Safe Japan waterway cleanup workflow
# Default assumption: downgrade river -> stream
# unless validated Wikidata river match is found

from java.net import URL, URLEncoder
from java.io import BufferedReader, InputStreamReader
from java.util import Collections
from javax.swing import JOptionPane, JTextArea, JScrollPane
from java.awt import Dimension
from math import radians, sin, cos, sqrt, atan2
import json

from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.data import UndoRedoHandler


# Valid waterway types in Wikidata
VALID_TYPES = set([
    "Q4022",    # river
    "Q47521",   # stream
    "Q355304"   # watercourse
])

JAPAN_QID = "Q17"
MIN_ACCEPT_SCORE = 8
MAX_DISTANCE_KM = 100


def http_get(url):
    conn = URL(url).openConnection()
    conn.setRequestProperty("User-Agent", "JOSM Japan waterway matcher")

    reader = BufferedReader(InputStreamReader(conn.getInputStream(), "UTF-8"))
    lines = []

    line = reader.readLine()
    while line is not None:
        lines.append(line)
        line = reader.readLine()

    reader.close()
    return "".join(lines)


def search_candidates(name, lang="ja"):
    encoded = URLEncoder.encode(name, "UTF-8")

    url = (
        "https://www.wikidata.org/w/api.php"
        "?action=wbsearchentities"
        "&search=%s"
        "&language=%s"
        "&format=json"
        "&type=item"
        "&limit=10"
    ) % (encoded, lang)

    response = http_get(url)
    data = json.loads(response)

    return data.get("search", [])


def get_entity(qid):
    url = (
        "https://www.wikidata.org/wiki/Special:EntityData/%s.json"
        % qid
    )

    response = http_get(url)
    data = json.loads(response)

    return data["entities"][qid]


def extract_claim_qids(entity, prop):
    claims = entity.get("claims", {}).get(prop, [])
    results = []

    for claim in claims:
        try:
            qid = claim["mainsnak"]["datavalue"]["value"]["id"]
            results.append(qid)
        except:
            pass

    return results


def extract_coords(entity):
    claims = entity.get("claims", {}).get("P625", [])
    if not claims:
        return None

    try:
        value = claims[0]["mainsnak"]["datavalue"]["value"]
        return (value["latitude"], value["longitude"])
    except:
        return None


def get_osm_center(obj):
    bbox = obj.getBBox()

    lat = (bbox.getTopLeftLat() + bbox.getBottomRightLat()) / 2.0
    lon = (bbox.getTopLeftLon() + bbox.getBottomRightLon()) / 2.0

    return (lat, lon)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2.0) ** 2
        + cos(radians(lat1))
        * cos(radians(lat2))
        * sin(dlon / 2.0) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def score_candidate(entity, obj):
    score = 0

    # Validate type
    p31 = extract_claim_qids(entity, "P31")

    if not any(q in VALID_TYPES for q in p31):
        return -999

    score += 5

    # Geographic country check
    countries = extract_claim_qids(entity, "P17")

    if JAPAN_QID in countries:
        score += 4

    labels = entity.get("labels", {})
    ja_label = labels.get("ja", {}).get("value", "")
    en_label = labels.get("en", {}).get("value", "")

    tags = obj.getKeys()

    # Strong preference for Japanese name match
    if tags.containsKey("name:ja"):
        if tags.get("name:ja") == ja_label:
            score += 6

    # Secondary English name check
    if tags.containsKey("name"):
        if tags.get("name") == en_label:
            score += 3

    # Coordinate validation
    wd_coords = extract_coords(entity)

    if wd_coords:
        osm_lat, osm_lon = get_osm_center(obj)

        dist = haversine_km(
            osm_lat,
            osm_lon,
            wd_coords[0],
            wd_coords[1]
        )

        if dist < 20:
            score += 8
        elif dist < MAX_DISTANCE_KM:
            score += 4
        else:
            score -= 10

    return score


def find_best_match(obj):
    tags = obj.getKeys()
    search_names = []

    if tags.containsKey("name:ja"):
        search_names.append((tags.get("name:ja"), "ja"))

    if tags.containsKey("name"):
        search_names.append((tags.get("name"), "en"))

    for key in tags.keySet():
        if key.startswith("name:") and key != "name:ja":
            search_names.append((tags.get(key), "en"))

    best_score = -999
    best_entity = None

    for name, lang in search_names:
        candidates = search_candidates(name, lang)

        for c in candidates:
            qid = c["id"]
            entity = get_entity(qid)

            score = score_candidate(entity, obj)

            if score > best_score:
                best_score = score
                best_entity = entity

    if best_score >= MIN_ACCEPT_SCORE:
        return best_entity

    return None


def show_preview_dialog(lines):
    text_area = JTextArea("\n".join(lines))
    text_area.setEditable(False)
    text_area.setLineWrap(False)

    scroll = JScrollPane(text_area)
    scroll.setPreferredSize(Dimension(1000, 550))

    return JOptionPane.showConfirmDialog(
        None,
        scroll,
        "Preview Proposed Waterway Changes",
        JOptionPane.OK_CANCEL_OPTION,
        JOptionPane.WARNING_MESSAGE
    )


def show_summary_dialog(lines):
    text_area = JTextArea("\n".join(lines))
    text_area.setEditable(False)
    text_area.setLineWrap(False)

    scroll = JScrollPane(text_area)
    scroll.setPreferredSize(Dimension(1000, 550))

    JOptionPane.showMessageDialog(
        None,
        scroll,
        "Applied Waterway Changes",
        JOptionPane.INFORMATION_MESSAGE
    )


def process():
    layer = MainApplication.getLayerManager().getEditLayer()

    if layer is None:
        show_summary_dialog(["No active edit layer."])
        return

    selected = layer.data.getSelected()

    if selected.isEmpty():
        show_summary_dialog(["No selected objects."])
        return

    plan = []
    preview = []

    for obj in selected:
        tags = obj.getKeys()

        if tags.get("waterway") != "river":
            continue

        name = tags.get("name", "Unnamed waterway")

        match = find_best_match(obj)

        if match:
            qid = match["id"]
            en_label = match.get("labels", {}).get("en", {}).get("value", "")

            preview.append(
                "%s -> KEEP river ; add wikidata=%s ; name:en=%s"
                % (name, qid, en_label)
            )

            plan.append(("match", obj, qid, en_label))

        else:
            preview.append(
                "%s -> CHANGE river -> stream ; add source=no wikidata match"
                % name
            )

            plan.append(("nomatch", obj))

    if not plan:
        show_summary_dialog(["No selected river objects found."])
        return

    result = show_preview_dialog(preview)

    if result != JOptionPane.OK_OPTION:
        show_summary_dialog(["Operation cancelled. No changes applied."])
        return

    summary = []

    for item in plan:
        commands = []

        if item[0] == "match":
            _, obj, qid, en_label = item
            name = obj.getKeys().get("name", "Unnamed waterway")

            commands.append(
                ChangePropertyCommand(
                    Collections.singleton(obj),
                    "wikidata",
                    qid
                )
            )

            if en_label:
                commands.append(
                    ChangePropertyCommand(
                        Collections.singleton(obj),
                        "name:en",
                        en_label
                    )
                )

            summary.append(
                "%s -> kept river ; wikidata=%s"
                % (name, qid)
            )

        else:
            _, obj = item
            name = obj.getKeys().get("name", "Unnamed waterway")

            commands.append(
                ChangePropertyCommand(
                    Collections.singleton(obj),
                    "waterway",
                    "stream"
                )
            )

            commands.append(
                ChangePropertyCommand(
                    Collections.singleton(obj),
                    "waterway:source",
                    "no wikidata match"
                )
            )

            summary.append(
                "%s -> changed river -> stream ; source=no wikidata match"
                % name
            )

        UndoRedoHandler.getInstance().add(
            SequenceCommand(
                "Japan waterway cleanup",
                commands
            )
        )

    show_summary_dialog(summary)


process()
