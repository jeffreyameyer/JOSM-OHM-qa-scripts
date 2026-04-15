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
# Japan waterway cleanup with:
# - live progress dialog for Wikidata calls
# - skip objects already marked waterway:botcheck=yes
# - mark all reviewed objects with waterway:botcheck=yes
# - unique name:ja grouping
# - batched Wikidata entity fetches via wbgetentities
# - caching
# - retry/backoff for HTTP 429
# - type/country validation
# - distance-based disambiguation
# - preview + confirmation dialog
# - matched objects get wikidata/name:en/wikipedia tags
# - unmatched objects become stream + waterway:source=no wikidata match
# - for all reviewed waterway=river objects:
#     * delete level if level=-1
#     * if source and source_ref both exist:
#         source:name = old source
#         source = old source_ref
#         delete source_ref

from java.net import URL, URLEncoder
from java.io import BufferedReader, InputStreamReader
from java.util import Collections
from java.lang import Thread
from javax.swing import (
    JOptionPane, JTextArea, JScrollPane, JDialog,
    SwingUtilities
)
from java.awt import Dimension, BorderLayout
from math import radians, sin, cos, sqrt, atan2
import json

from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.command import ChangePropertyCommand, SequenceCommand
from org.openstreetmap.josm.data import UndoRedoHandler


VALID_TYPES = set([
    "Q4022",    # river
    "Q47521",   # stream
    "Q355304"   # watercourse
])

JAPAN_QID = "Q17"
MIN_ACCEPT_SCORE = 8
MAX_DISTANCE_KM = 100

REQUEST_SLEEP_MS = 120
MAX_RETRIES = 5
ENTITY_BATCH_SIZE = 25

SEARCH_CACHE = {}
ENTITY_CACHE = {}
GROUP_MATCH_CACHE = {}

PROGRESS_DIALOG = None
PROGRESS_TEXT_AREA = None


def should_skip(obj):
    tags = obj.getKeys()
    return tags.get("waterway:botcheck") == "yes"


def sleep_ms(ms):
    Thread.sleep(ms)


def get_parent_component():
    try:
        return MainApplication.getMainFrame()
    except:
        return None


def init_progress_dialog():
    global PROGRESS_DIALOG, PROGRESS_TEXT_AREA

    PROGRESS_TEXT_AREA = JTextArea()
    PROGRESS_TEXT_AREA.setEditable(False)
    PROGRESS_TEXT_AREA.setLineWrap(False)

    scroll = JScrollPane(PROGRESS_TEXT_AREA)
    scroll.setPreferredSize(Dimension(950, 260))

    PROGRESS_DIALOG = JDialog(get_parent_component(), "Wikidata Progress", False)
    PROGRESS_DIALOG.getContentPane().setLayout(BorderLayout())
    PROGRESS_DIALOG.getContentPane().add(scroll, BorderLayout.CENTER)
    PROGRESS_DIALOG.pack()
    PROGRESS_DIALOG.setLocationRelativeTo(get_parent_component())
    PROGRESS_DIALOG.setVisible(True)


def append_progress_line(text):
    global PROGRESS_TEXT_AREA
    if PROGRESS_TEXT_AREA is None:
        return

    PROGRESS_TEXT_AREA.append(text + "\n")
    PROGRESS_TEXT_AREA.setCaretPosition(PROGRESS_TEXT_AREA.getDocument().getLength())
    PROGRESS_TEXT_AREA.repaint()


def close_progress_dialog():
    global PROGRESS_DIALOG
    if PROGRESS_DIALOG is not None:
        PROGRESS_DIALOG.dispose()
        PROGRESS_DIALOG = None


def read_stream(stream):
    reader = BufferedReader(InputStreamReader(stream, "UTF-8"))
    lines = []
    line = reader.readLine()
    while line is not None:
        lines.append(line)
        line = reader.readLine()
    reader.close()
    return "".join(lines)


def http_get_json(url, log_label=None):
    attempt = 0
    backoff_ms = 1000

    if log_label:
        append_progress_line(log_label)

    while attempt < MAX_RETRIES:
        try:
            conn = URL(url).openConnection()
            conn.setRequestProperty(
                "User-Agent",
                "JOSM Japan waterway matcher/1.7 (semi-automated cleanup)"
            )
            conn.setRequestProperty("Accept", "application/json")
            conn.setConnectTimeout(15000)
            conn.setReadTimeout(30000)

            code = conn.getResponseCode()

            if code == 200:
                sleep_ms(REQUEST_SLEEP_MS)
                body = read_stream(conn.getInputStream())
                return json.loads(body)

            elif code == 429:
                retry_after = conn.getHeaderField("Retry-After")
                wait_ms = backoff_ms
                if retry_after is not None:
                    try:
                        wait_ms = int(retry_after) * 1000
                    except:
                        pass
                append_progress_line(
                    "Wikidata rate-limited (429). Waiting %s ms before retry..." % wait_ms
                )
                sleep_ms(wait_ms)
                backoff_ms = backoff_ms * 2
                attempt += 1

            elif code >= 500:
                append_progress_line(
                    "Wikidata server error (%s). Retrying..." % code
                )
                sleep_ms(backoff_ms)
                backoff_ms = backoff_ms * 2
                attempt += 1

            else:
                err = ""
                try:
                    err = read_stream(conn.getErrorStream())
                except:
                    pass
                raise Exception("HTTP %s for URL %s %s" % (code, url, err))

        except Exception as e:
            attempt += 1
            if attempt >= MAX_RETRIES:
                raise
            append_progress_line("Request failed; retrying: %s" % str(e))
            sleep_ms(backoff_ms)
            backoff_ms = backoff_ms * 2

    raise Exception("Failed after retries for URL %s" % url)


def search_candidates(name, lang):
    key = (name, lang)
    if key in SEARCH_CACHE:
        return SEARCH_CACHE[key]

    encoded = URLEncoder.encode(name, "UTF-8")
    encoded_lang = URLEncoder.encode(lang, "UTF-8")

    url = (
        "https://www.wikidata.org/w/api.php"
        "?action=wbsearchentities"
        "&search=%s"
        "&language=%s"
        "&format=json"
        "&type=item"
        "&limit=10"
        "&maxlag=5"
    ) % (encoded, encoded_lang)

    data = http_get_json(
        url,
        "Searching Wikidata for %s name=%s" % (lang, name)
    )
    results = data.get("search", [])
    SEARCH_CACHE[key] = results
    return results


def get_entities_batch(qids):
    missing = []
    for qid in qids:
        if qid not in ENTITY_CACHE:
            missing.append(qid)

    if not missing:
        return

    start = 0
    while start < len(missing):
        batch = missing[start:start + ENTITY_BATCH_SIZE]
        ids = "|".join(batch)
        encoded_ids = URLEncoder.encode(ids, "UTF-8")

        url = (
            "https://www.wikidata.org/w/api.php"
            "?action=wbgetentities"
            "&ids=%s"
            "&languages=ja|en"
            "&props=labels|aliases|claims|sitelinks"
            "&sitefilter=enwiki|jawiki"
            "&format=json"
            "&maxlag=5"
        ) % encoded_ids

        data = http_get_json(
            url,
            "Fetching Wikidata entities batch: %s" % ids
        )
        entities = data.get("entities", {})

        for qid in batch:
            if qid in entities:
                ENTITY_CACHE[qid] = entities[qid]

        start += ENTITY_BATCH_SIZE


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


def get_sitelink_title(entity, site_key):
    try:
        return entity.get("sitelinks", {}).get(site_key, {}).get("title", "")
    except:
        return ""


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
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2.0) ** 2
    )
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def label_value(entity, lang):
    return entity.get("labels", {}).get(lang, {}).get("value", "")


def aliases_values(entity, lang):
    aliases = entity.get("aliases", {}).get(lang, [])
    vals = []
    for a in aliases:
        try:
            vals.append(a.get("value", ""))
        except:
            pass
    return vals


def score_candidate(entity, obj, group_name_ja=None):
    score = 0

    p31 = extract_claim_qids(entity, "P31")
    if not any(q in VALID_TYPES for q in p31):
        return -999

    score += 5

    countries = extract_claim_qids(entity, "P17")
    if JAPAN_QID in countries:
        score += 4

    tags = obj.getKeys()

    ja_label = label_value(entity, "ja")
    en_label = label_value(entity, "en")
    ja_aliases = aliases_values(entity, "ja")
    en_aliases = aliases_values(entity, "en")

    if group_name_ja is not None:
        if group_name_ja == ja_label:
            score += 6
        elif group_name_ja in ja_aliases:
            score += 5
    elif tags.containsKey("name:ja"):
        name_ja = tags.get("name:ja")
        if name_ja == ja_label:
            score += 6
        elif name_ja in ja_aliases:
            score += 5

    if tags.containsKey("name"):
        name = tags.get("name")
        if name == en_label:
            score += 3
        elif name in en_aliases:
            score += 2

    wd_coords = extract_coords(entity)
    if wd_coords:
        osm_lat, osm_lon = get_osm_center(obj)
        dist = haversine_km(osm_lat, osm_lon, wd_coords[0], wd_coords[1])

        if dist < 20:
            score += 8
        elif dist < MAX_DISTANCE_KM:
            score += 4
        else:
            score -= 10

    return score


def build_search_names(tags):
    names = []

    if tags.containsKey("name:ja"):
        names.append((tags.get("name:ja"), "ja"))

    if tags.containsKey("name"):
        names.append((tags.get("name"), "en"))

    for key in tags.keySet():
        if key.startswith("name:") and key != "name:ja":
            val = tags.get(key)
            if val:
                names.append((val, "en"))

    seen = set()
    out = []
    for pair in names:
        if pair not in seen:
            seen.add(pair)
            out.append(pair)

    return out


def find_best_match_for_obj(obj):
    tags = obj.getKeys()
    search_names = build_search_names(tags)

    all_candidates = []
    qids = []
    seen_qids = set()

    for name, lang in search_names:
        candidates = search_candidates(name, lang)
        for c in candidates:
            qid = c.get("id")
            if qid and qid not in seen_qids:
                seen_qids.add(qid)
                all_candidates.append(c)
                qids.append(qid)

    if not qids:
        return None

    get_entities_batch(qids)

    best_score = -999
    best_entity = None

    for c in all_candidates:
        qid = c.get("id")
        entity = ENTITY_CACHE.get(qid)
        if entity is None:
            continue

        score = score_candidate(entity, obj)
        if score > best_score:
            best_score = score
            best_entity = entity

    if best_score >= MIN_ACCEPT_SCORE:
        return best_entity

    return None


def find_best_match_for_group(name_ja, objs):
    if name_ja in GROUP_MATCH_CACHE:
        return GROUP_MATCH_CACHE[name_ja]

    sample_obj = objs[0]
    candidates = search_candidates(name_ja, "ja")

    qids = []
    seen_qids = set()

    for c in candidates:
        qid = c.get("id")
        if qid and qid not in seen_qids:
            seen_qids.add(qid)
            qids.append(qid)

    if not qids:
        GROUP_MATCH_CACHE[name_ja] = None
        return None

    get_entities_batch(qids)

    best_score = -999
    best_entity = None

    for qid in qids:
        entity = ENTITY_CACHE.get(qid)
        if entity is None:
            continue

        score = score_candidate(entity, sample_obj, group_name_ja=name_ja)
        if score > best_score:
            best_score = score
            best_entity = entity

    if best_score >= MIN_ACCEPT_SCORE:
        GROUP_MATCH_CACHE[name_ja] = best_entity
        return best_entity

    GROUP_MATCH_CACHE[name_ja] = None
    return None


def show_preview_dialog(lines):
    text_area = JTextArea("\n".join(lines))
    text_area.setEditable(False)
    text_area.setLineWrap(False)

    scroll = JScrollPane(text_area)
    scroll.setPreferredSize(Dimension(1100, 600))

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
    scroll.setPreferredSize(Dimension(1100, 600))

    JOptionPane.showMessageDialog(
        None,
        scroll,
        "Applied Waterway Changes",
        JOptionPane.INFORMATION_MESSAGE
    )


def get_name(tags):
    if tags.containsKey("name"):
        return tags.get("name")
    if tags.containsKey("name:ja"):
        return tags.get("name:ja")
    return "Unnamed waterway"


def get_common_cleanup_preview_bits(obj):
    bits = []
    tags = obj.getKeys()

    if tags.get("level") == "-1":
        bits.append("delete level=-1")

    if tags.containsKey("source") and tags.containsKey("source_ref"):
        bits.append("rename source->source:name and source_ref->source")

    return bits


def group_objects(objs):
    grouped_by_name_ja = {}
    singles = []
    skipped = []

    for obj in objs:
        if should_skip(obj):
            skipped.append(obj)
            continue

        tags = obj.getKeys()

        if tags.get("waterway") != "river":
            continue

        if tags.containsKey("name:ja"):
            key = tags.get("name:ja")
            if key not in grouped_by_name_ja:
                grouped_by_name_ja[key] = []
            grouped_by_name_ja[key].append(obj)
        else:
            singles.append(obj)

    return grouped_by_name_ja, singles, skipped


def add_match_plan_entries(plan, preview, objs, entity, grouped_label=None):
    qid = entity["id"]
    en_label = label_value(entity, "en")
    enwiki_title = get_sitelink_title(entity, "enwiki")
    jawiki_title = get_sitelink_title(entity, "jawiki")

    for obj in objs:
        name = get_name(obj.getKeys())

        preview_bits = ["wikidata=%s" % qid, "waterway:botcheck=yes"]

        if en_label:
            preview_bits.append("name:en=%s" % en_label)
        if enwiki_title:
            preview_bits.append("wikipedia=en:%s" % enwiki_title)
        if jawiki_title:
            preview_bits.append("wikipedia:ja=%s" % jawiki_title)

        preview_bits.extend(get_common_cleanup_preview_bits(obj))
        preview_text = " ; ".join(preview_bits)

        if grouped_label is not None:
            preview.append(
                "%s -> KEEP river ; add/change %s ; grouped by name:ja=%s"
                % (name, preview_text, grouped_label)
            )
        else:
            preview.append(
                "%s -> KEEP river ; add/change %s"
                % (name, preview_text)
            )

        plan.append(("match", obj, qid, en_label, enwiki_title, jawiki_title))


def add_nomatch_plan_entries(plan, preview, objs, grouped_label=None):
    for obj in objs:
        name = get_name(obj.getKeys())

        preview_bits = [
            "waterway=stream",
            "waterway:source=no wikidata match",
            "waterway:botcheck=yes"
        ]
        preview_bits.extend(get_common_cleanup_preview_bits(obj))
        preview_text = " ; ".join(preview_bits)

        if grouped_label is not None:
            preview.append(
                "%s -> CHANGE river -> stream ; add/change %s ; grouped by name:ja=%s"
                % (name, preview_text, grouped_label)
            )
        else:
            preview.append(
                "%s -> CHANGE river -> stream ; add/change %s"
                % (name, preview_text)
            )

        plan.append(("nomatch", obj))


def append_common_cleanup_commands(commands, obj):
    tags = obj.getKeys()

    if tags.get("level") == "-1":
        commands.append(
            ChangePropertyCommand(
                Collections.singleton(obj),
                "level",
                None
            )
        )

    if tags.containsKey("source") and tags.containsKey("source_ref"):
        old_source = tags.get("source")
        old_source_ref = tags.get("source_ref")

        commands.append(
            ChangePropertyCommand(
                Collections.singleton(obj),
                "source:name",
                old_source
            )
        )

        commands.append(
            ChangePropertyCommand(
                Collections.singleton(obj),
                "source",
                old_source_ref
            )
        )

        commands.append(
            ChangePropertyCommand(
                Collections.singleton(obj),
                "source_ref",
                None
            )
        )


def build_common_cleanup_summary_bits(obj):
    bits = []
    tags = obj.getKeys()

    if tags.get("level") == "-1":
        bits.append("deleted level=-1")

    if tags.containsKey("source") and tags.containsKey("source_ref"):
        bits.append("changed source->source:name and source_ref->source")

    return bits


def process():
    init_progress_dialog()
    append_progress_line("Starting review of selected waterways...")

    try:
        layer = MainApplication.getLayerManager().getEditLayer()

        if layer is None:
            close_progress_dialog()
            show_summary_dialog(["No active edit layer."])
            return

        selected = list(layer.data.getSelected())

        if not selected:
            close_progress_dialog()
            show_summary_dialog(["No selected objects."])
            return

        grouped_by_name_ja, singles, skipped = group_objects(selected)

        plan = []
        preview = []

        group_keys = list(grouped_by_name_ja.keys())
        group_keys.sort()

        append_progress_line("Unique name:ja groups to resolve: %s" % len(group_keys))
        append_progress_line("Single unnamed-ja rivers to resolve individually: %s" % len(singles))
        append_progress_line("Skipped due to waterway:botcheck=yes: %s" % len(skipped))

        for name_ja in group_keys:
            objs = grouped_by_name_ja[name_ja]
            append_progress_line(
                "Resolving grouped name:ja '%s' for %s object(s)..." % (name_ja, len(objs))
            )
            match = find_best_match_for_group(name_ja, objs)

            if match:
                add_match_plan_entries(plan, preview, objs, match, grouped_label=name_ja)
            else:
                add_nomatch_plan_entries(plan, preview, objs, grouped_label=name_ja)

        for obj in singles:
            append_progress_line(
                "Resolving single object '%s'..." % get_name(obj.getKeys())
            )
            match = find_best_match_for_obj(obj)

            if match:
                add_match_plan_entries(plan, preview, [obj], match)
            else:
                add_nomatch_plan_entries(plan, preview, [obj])

        append_progress_line("Finished Wikidata lookups.")

        close_progress_dialog()

        if not plan:
            show_summary_dialog([
                "No selected river objects found to review.",
                "Skipped due to waterway:botcheck=yes: %s" % len(skipped)
            ])
            return

        preview.insert(0, "Total proposed changes: %s" % len(plan))
        preview.insert(1, "Skipped due to waterway:botcheck=yes: %s" % len(skipped))
        preview.insert(2, "-" * 110)

        result = show_preview_dialog(preview)

        if result != JOptionPane.OK_OPTION:
            show_summary_dialog(["Operation cancelled. No changes applied."])
            return

        summary = []
        command_count = 0

        for item in plan:
            commands = []

            if item[0] == "match":
                _, obj, qid, en_label, enwiki_title, jawiki_title = item
                name = get_name(obj.getKeys())
                common_bits = build_common_cleanup_summary_bits(obj)

                append_common_cleanup_commands(commands, obj)

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

                if enwiki_title:
                    commands.append(
                        ChangePropertyCommand(
                            Collections.singleton(obj),
                            "wikipedia",
                            "en:%s" % enwiki_title
                        )
                    )

                if jawiki_title:
                    commands.append(
                        ChangePropertyCommand(
                            Collections.singleton(obj),
                            "wikipedia:ja",
                            jawiki_title
                        )
                    )

                commands.append(
                    ChangePropertyCommand(
                        Collections.singleton(obj),
                        "waterway:botcheck",
                        "yes"
                    )
                )

                summary_bits = ["wikidata=%s" % qid, "waterway:botcheck=yes"]

                if en_label:
                    summary_bits.append("name:en=%s" % en_label)
                if enwiki_title:
                    summary_bits.append("wikipedia=en:%s" % enwiki_title)
                if jawiki_title:
                    summary_bits.append("wikipedia:ja=%s" % jawiki_title)

                summary_bits.extend(common_bits)

                summary.append(
                    "%s -> kept river ; %s"
                    % (name, " ; ".join(summary_bits))
                )

            else:
                _, obj = item
                name = get_name(obj.getKeys())
                common_bits = build_common_cleanup_summary_bits(obj)

                append_common_cleanup_commands(commands, obj)

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

                commands.append(
                    ChangePropertyCommand(
                        Collections.singleton(obj),
                        "waterway:botcheck",
                        "yes"
                    )
                )

                summary_bits = [
                    "waterway=stream",
                    "waterway:source=no wikidata match",
                    "waterway:botcheck=yes"
                ]
                summary_bits.extend(common_bits)

                summary.append(
                    "%s -> changed river -> stream ; %s"
                    % (name, " ; ".join(summary_bits))
                )

            UndoRedoHandler.getInstance().add(
                SequenceCommand("Japan waterway cleanup", commands)
            )
            command_count += 1

        summary.insert(0, "Total objects changed: %s" % command_count)
        summary.insert(1, "Skipped due to waterway:botcheck=yes: %s" % len(skipped))
        summary.insert(2, "-" * 110)

        show_summary_dialog(summary)

    except Exception as e:
        close_progress_dialog()
        show_summary_dialog([
            "Script stopped بسبب error." if False else "Script stopped because of an error.",
            str(e)
        ])


process()
