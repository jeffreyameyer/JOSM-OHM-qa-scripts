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
# - SPARQL-first lookup for exact name:ja groups
# - Action API fallback for unresolved names and non-name:ja objects
# - live progress dialog that updates while the script runs
# - hard cap of 500 Wikidata lookup calls per run
# - skip objects already marked waterway:botcheck=yes
# - mark all reviewed objects with waterway:botcheck=yes
# - unique name:ja grouping
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
from java.lang import Thread, Runnable
from javax.swing import JOptionPane, JTextArea, JScrollPane, JDialog, SwingUtilities
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
SPARQL_NAME_BATCH_SIZE = 50
MAX_WIKIDATA_LOOKUPS = 500

USER_AGENT = "OHMJapanWaterwayBot/3.1 (Jeff Meyer; jeff@openhistoricalmap.org; https://openhistoricalmap.org) JOSM-Jython"

SEARCH_CACHE = {}          # (name, lang) -> [candidate search dicts]
ENTITY_CACHE = {}          # qid -> normalized candidate dict
GROUP_MATCH_CACHE = {}     # exact name:ja -> normalized candidate dict or None
SPARQL_NAME_CACHE = {}     # exact name:ja -> [normalized candidate dicts]

PROGRESS_DIALOG = None
PROGRESS_TEXT_AREA = None

WIKIDATA_LOOKUP_COUNT = 0
LOOKUP_LIMIT_REACHED = False


# ----------------------------
# EDT helpers
# ----------------------------

def run_on_edt_and_wait(runnable):
    if SwingUtilities.isEventDispatchThread():
        runnable.run()
    else:
        SwingUtilities.invokeAndWait(runnable)


def run_on_edt_later(runnable):
    if SwingUtilities.isEventDispatchThread():
        runnable.run()
    else:
        SwingUtilities.invokeLater(runnable)


# ----------------------------
# UI helpers
# ----------------------------

def get_parent_component():
    try:
        return MainApplication.getMainFrame()
    except:
        return None


class InitProgressDialogRunnable(Runnable):
    def run(self):
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


class AppendProgressRunnable(Runnable):
    def __init__(self, text):
        self.text = text

    def run(self):
        global PROGRESS_TEXT_AREA
        if PROGRESS_TEXT_AREA is None:
            return

        PROGRESS_TEXT_AREA.append(self.text + "\n")
        PROGRESS_TEXT_AREA.setCaretPosition(
            PROGRESS_TEXT_AREA.getDocument().getLength()
        )
        PROGRESS_TEXT_AREA.revalidate()
        PROGRESS_TEXT_AREA.repaint()


class CloseProgressDialogRunnable(Runnable):
    def run(self):
        global PROGRESS_DIALOG, PROGRESS_TEXT_AREA
        if PROGRESS_DIALOG is not None:
            PROGRESS_DIALOG.dispose()
            PROGRESS_DIALOG = None
        PROGRESS_TEXT_AREA = None


class ShowConfirmDialogRunnable(Runnable):
    def __init__(self, lines, title):
        self.lines = lines
        self.title = title
        self.result = None

    def run(self):
        text_area = JTextArea("\n".join(self.lines))
        text_area.setEditable(False)
        text_area.setLineWrap(False)

        scroll = JScrollPane(text_area)
        scroll.setPreferredSize(Dimension(1100, 600))

        self.result = JOptionPane.showConfirmDialog(
            get_parent_component(),
            scroll,
            self.title,
            JOptionPane.OK_CANCEL_OPTION,
            JOptionPane.WARNING_MESSAGE
        )


class ShowMessageDialogRunnable(Runnable):
    def __init__(self, lines, title):
        self.lines = lines
        self.title = title

    def run(self):
        text_area = JTextArea("\n".join(self.lines))
        text_area.setEditable(False)
        text_area.setLineWrap(False)

        scroll = JScrollPane(text_area)
        scroll.setPreferredSize(Dimension(1100, 600))

        JOptionPane.showMessageDialog(
            get_parent_component(),
            scroll,
            self.title,
            JOptionPane.INFORMATION_MESSAGE
        )


def init_progress_dialog():
    run_on_edt_and_wait(InitProgressDialogRunnable())


def append_progress_line(text):
    run_on_edt_later(AppendProgressRunnable(text))


def close_progress_dialog():
    run_on_edt_later(CloseProgressDialogRunnable())


def show_preview_dialog(lines):
    runnable = ShowConfirmDialogRunnable(lines, "Preview Proposed Waterway Changes")
    run_on_edt_and_wait(runnable)
    return runnable.result


def show_summary_dialog(lines):
    runnable = ShowMessageDialogRunnable(lines, "Applied Waterway Changes")
    run_on_edt_and_wait(runnable)


# ----------------------------
# General helpers
# ----------------------------

def should_skip(obj):
    tags = obj.getKeys()
    return tags.get("waterway:botcheck") == "yes"


def sleep_ms(ms):
    Thread.sleep(ms)


def get_name(tags):
    if tags.containsKey("name"):
        return tags.get("name")
    if tags.containsKey("name:ja"):
        return tags.get("name:ja")
    return "Unnamed waterway"


def read_stream(stream):
    reader = BufferedReader(InputStreamReader(stream, "UTF-8"))
    lines = []
    line = reader.readLine()
    while line is not None:
        lines.append(line)
        line = reader.readLine()
    reader.close()
    return "".join(lines)


def can_make_lookup():
    global LOOKUP_LIMIT_REACHED
    if WIKIDATA_LOOKUP_COUNT >= MAX_WIKIDATA_LOOKUPS:
        if not LOOKUP_LIMIT_REACHED:
            LOOKUP_LIMIT_REACHED = True
            append_progress_line(
                "Lookup limit reached (%s). Stopping additional Wikidata requests for this run."
                % MAX_WIKIDATA_LOOKUPS
            )
        return False
    return True


def register_lookup(log_label):
    global WIKIDATA_LOOKUP_COUNT
    WIKIDATA_LOOKUP_COUNT += 1
    append_progress_line(
        "[%s/%s] %s" % (WIKIDATA_LOOKUP_COUNT, MAX_WIKIDATA_LOOKUPS, log_label)
    )


def escape_sparql_string(s):
    return s.replace("\\", "\\\\").replace("\"", "\\\"")


def parse_qid_from_uri(uri):
    if uri is None:
        return ""
    idx = uri.rfind("/")
    if idx >= 0:
        return uri[idx + 1:]
    return uri


def parse_sparql_point_literal(point_str):
    if point_str is None:
        return None
    s = point_str.strip()
    if not s.startswith("Point(") or not s.endswith(")"):
        return None
    inner = s[6:-1].strip()
    parts = inner.split(" ")
    if len(parts) != 2:
        return None
    try:
        lon = float(parts[0])
        lat = float(parts[1])
        return (lat, lon)
    except:
        return None


# ----------------------------
# HTTP / Wikidata helpers
# ----------------------------

def http_get_json(url, log_label=None, accept_header="application/json"):
    attempt = 0
    backoff_ms = 1000

    if log_label:
        register_lookup(log_label)

    while attempt < MAX_RETRIES:
        try:
            conn = URL(url).openConnection()
            conn.setRequestProperty("User-Agent", USER_AGENT)
            conn.setRequestProperty("Accept", accept_header)
            conn.setConnectTimeout(15000)
            conn.setReadTimeout(45000)

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
                backoff_ms *= 2
                attempt += 1

            elif code >= 500:
                append_progress_line(
                    "Wikidata server error (%s). Retrying..." % code
                )
                sleep_ms(backoff_ms)
                backoff_ms *= 2
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
            backoff_ms *= 2

    raise Exception("Failed after retries for URL %s" % url)


def normalize_action_entity(entity):
    normalized = {
        "id": entity.get("id", ""),
        "type_qids": extract_claim_qids_from_action(entity, "P31"),
        "country_qids": extract_claim_qids_from_action(entity, "P17"),
        "coord": extract_coords_from_action(entity),
        "label_en": action_label_value(entity, "en"),
        "label_ja": action_label_value(entity, "ja"),
        "aliases_en": action_aliases_values(entity, "en"),
        "aliases_ja": action_aliases_values(entity, "ja"),
        "sitelinks": {
            "enwiki": action_sitelink_title(entity, "enwiki"),
            "jawiki": action_sitelink_title(entity, "jawiki")
        },
        "matched_name_ja": None
    }
    return normalized


def search_candidates(name, lang):
    key = (name, lang)
    if key in SEARCH_CACHE:
        return SEARCH_CACHE[key]

    if not can_make_lookup():
        return None

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
        "Action API search for %s name=%s" % (lang, name)
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
        return True

    start = 0
    while start < len(missing):
        if not can_make_lookup():
            return False

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
            "Action API entity batch: %s" % ids
        )
        entities = data.get("entities", {})

        for qid in batch:
            if qid in entities:
                ENTITY_CACHE[qid] = normalize_action_entity(entities[qid])

        start += ENTITY_BATCH_SIZE

    return True


def extract_claim_qids_from_action(entity, prop):
    claims = entity.get("claims", {}).get(prop, [])
    results = []

    for claim in claims:
        try:
            qid = claim["mainsnak"]["datavalue"]["value"]["id"]
            results.append(qid)
        except:
            pass

    return results


def extract_coords_from_action(entity):
    claims = entity.get("claims", {}).get("P625", [])
    if not claims:
        return None

    try:
        value = claims[0]["mainsnak"]["datavalue"]["value"]
        return (value["latitude"], value["longitude"])
    except:
        return None


def action_sitelink_title(entity, site_key):
    try:
        return entity.get("sitelinks", {}).get(site_key, {}).get("title", "")
    except:
        return ""


def action_label_value(entity, lang):
    return entity.get("labels", {}).get(lang, {}).get("value", "")


def action_aliases_values(entity, lang):
    aliases = entity.get("aliases", {}).get(lang, [])
    vals = []
    for a in aliases:
        try:
            vals.append(a.get("value", ""))
        except:
            pass
    return vals


# ----------------------------
# SPARQL helpers
# ----------------------------

def make_sparql_candidates_for_names(name_batch):
    if not name_batch:
        return {}

    unresolved = []
    for n in name_batch:
        if n not in SPARQL_NAME_CACHE:
            unresolved.append(n)

    if not unresolved:
        result = {}
        for n in name_batch:
            result[n] = SPARQL_NAME_CACHE.get(n, [])
        return result

    if not can_make_lookup():
        return None

    values_parts = []
    for n in unresolved:
        values_parts.append("\"%s\"" % escape_sparql_string(n))
    values_block = " ".join(values_parts)

    query = """
SELECT ?needle ?item ?coord ?country ?itemLabelEn ?itemLabelJa ?enwikiTitle ?jawikiTitle WHERE {
  VALUES ?needle { %s }

  ?item (rdfs:label|skos:altLabel) ?needle .
  FILTER(LANG(?needle) = "ja")

  ?item wdt:P31 ?type .
  VALUES ?type { wd:Q4022 wd:Q47521 wd:Q355304 }

  OPTIONAL { ?item wdt:P625 ?coord }
  OPTIONAL { ?item wdt:P17 ?country }

  OPTIONAL {
    ?item rdfs:label ?itemLabelEn .
    FILTER(LANG(?itemLabelEn) = "en")
  }

  OPTIONAL {
    ?item rdfs:label ?itemLabelJa .
    FILTER(LANG(?itemLabelJa) = "ja")
  }

  OPTIONAL {
    ?articleEn schema:about ?item ;
               schema:isPartOf <https://en.wikipedia.org/> ;
               schema:name ?enwikiTitle .
  }

  OPTIONAL {
    ?articleJa schema:about ?item ;
               schema:isPartOf <https://ja.wikipedia.org/> ;
               schema:name ?jawikiTitle .
  }
}
""" % values_block

    encoded_query = URLEncoder.encode(query, "UTF-8")
    url = "https://query.wikidata.org/sparql?format=json&query=%s" % encoded_query

    data = http_get_json(
        url,
        "WDQS batch for %s unique name:ja values" % len(unresolved),
        "application/sparql-results+json, application/json"
    )

    bindings = data.get("results", {}).get("bindings", [])

    grouped = {}
    for n in unresolved:
        grouped[n] = []

    seen_by_name = {}

    for row in bindings:
        try:
            needle = row.get("needle", {}).get("value", "")
            item_uri = row.get("item", {}).get("value", "")
            qid = parse_qid_from_uri(item_uri)
            if not needle or not qid:
                continue

            dedupe_key = needle + "||" + qid
            if dedupe_key in seen_by_name:
                continue
            seen_by_name[dedupe_key] = True

            coord = None
            if "coord" in row:
                coord = parse_sparql_point_literal(row["coord"]["value"])

            country_qids = []
            if "country" in row:
                country_qids.append(parse_qid_from_uri(row["country"]["value"]))

            candidate = {
                "id": qid,
                "type_qids": list(VALID_TYPES),
                "country_qids": country_qids,
                "coord": coord,
                "label_en": row.get("itemLabelEn", {}).get("value", ""),
                "label_ja": row.get("itemLabelJa", {}).get("value", ""),
                "aliases_en": [],
                "aliases_ja": [],
                "sitelinks": {
                    "enwiki": row.get("enwikiTitle", {}).get("value", ""),
                    "jawiki": row.get("jawikiTitle", {}).get("value", "")
                },
                "matched_name_ja": needle
            }

            grouped.setdefault(needle, []).append(candidate)
        except:
            pass

    for n in unresolved:
        SPARQL_NAME_CACHE[n] = grouped.get(n, [])

    result = {}
    for n in name_batch:
        result[n] = SPARQL_NAME_CACHE.get(n, [])
    return result


# ----------------------------
# Geometry / scoring
# ----------------------------

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


def score_candidate(candidate, obj, group_name_ja=None):
    score = 0

    if not any(q in VALID_TYPES for q in candidate.get("type_qids", [])):
        return -999

    score += 5

    if JAPAN_QID in candidate.get("country_qids", []):
        score += 4

    tags = obj.getKeys()

    ja_label = candidate.get("label_ja", "")
    en_label = candidate.get("label_en", "")
    ja_aliases = candidate.get("aliases_ja", [])
    en_aliases = candidate.get("aliases_en", [])
    matched_name_ja = candidate.get("matched_name_ja")

    if group_name_ja is not None:
        if group_name_ja == matched_name_ja or group_name_ja == ja_label:
            score += 6
        elif group_name_ja in ja_aliases:
            score += 5
    elif tags.containsKey("name:ja"):
        name_ja = tags.get("name:ja")
        if name_ja == matched_name_ja or name_ja == ja_label:
            score += 6
        elif name_ja in ja_aliases:
            score += 5

    if tags.containsKey("name"):
        name = tags.get("name")
        if name == en_label:
            score += 3
        elif name in en_aliases:
            score += 2

    wd_coords = candidate.get("coord")
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


# ----------------------------
# Matching helpers
# ----------------------------

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
        if candidates is None:
            return None, False

        for c in candidates:
            qid = c.get("id")
            if qid and qid not in seen_qids:
                seen_qids.add(qid)
                qids.append(qid)

    if not qids:
        return None, True

    fetched = get_entities_batch(qids)
    if not fetched:
        return None, False

    for qid in qids:
        candidate = ENTITY_CACHE.get(qid)
        if candidate:
            all_candidates.append(candidate)

    best_score = -999
    best_candidate = None

    for candidate in all_candidates:
        score = score_candidate(candidate, obj)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_score >= MIN_ACCEPT_SCORE:
        return best_candidate, True

    return None, True


def find_best_match_for_group_via_action(name_ja, objs):
    sample_obj = objs[0]
    candidates = search_candidates(name_ja, "ja")
    if candidates is None:
        return None, False

    qids = []
    seen_qids = set()

    for c in candidates:
        qid = c.get("id")
        if qid and qid not in seen_qids:
            seen_qids.add(qid)
            qids.append(qid)

    if not qids:
        return None, True

    fetched = get_entities_batch(qids)
    if not fetched:
        return None, False

    best_score = -999
    best_candidate = None

    for qid in qids:
        candidate = ENTITY_CACHE.get(qid)
        if candidate is None:
            continue

        score = score_candidate(candidate, sample_obj, group_name_ja=name_ja)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_score >= MIN_ACCEPT_SCORE:
        return best_candidate, True

    return None, True


def find_best_match_for_group_from_candidates(name_ja, objs, candidates):
    sample_obj = objs[0]

    best_score = -999
    best_candidate = None

    for candidate in candidates:
        score = score_candidate(candidate, sample_obj, group_name_ja=name_ja)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_score >= MIN_ACCEPT_SCORE:
        return best_candidate

    return None


# ----------------------------
# Planning helpers
# ----------------------------

def get_common_cleanup_preview_bits(obj):
    bits = []
    tags = obj.getKeys()

    if tags.get("level") == "-1":
        bits.append("delete level=-1")

    if tags.containsKey("source") and tags.containsKey("source_ref"):
        bits.append("rename source->source:name and source_ref->source")

    return bits


def build_common_cleanup_summary_bits(obj):
    bits = []
    tags = obj.getKeys()

    if tags.get("level") == "-1":
        bits.append("deleted level=-1")

    if tags.containsKey("source") and tags.containsKey("source_ref"):
        bits.append("changed source->source:name and source_ref->source")

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


def add_match_plan_entries(plan, preview, objs, candidate, grouped_label=None):
    qid = candidate["id"]
    en_label = candidate.get("label_en", "")
    enwiki_title = candidate.get("sitelinks", {}).get("enwiki", "")
    jawiki_title = candidate.get("sitelinks", {}).get("jawiki", "")

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


# ----------------------------
# Command helpers
# ----------------------------

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


# ----------------------------
# Main worker
# ----------------------------

def run_script():
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
        unresolved_due_to_limit = 0

        group_keys = list(grouped_by_name_ja.keys())
        group_keys.sort()

        append_progress_line("Unique name:ja groups to resolve: %s" % len(group_keys))
        append_progress_line("Single unnamed-ja rivers to resolve individually: %s" % len(singles))
        append_progress_line("Skipped due to waterway:botcheck=yes: %s" % len(skipped))
        append_progress_line("Lookup limit for this run: %s" % MAX_WIKIDATA_LOOKUPS)

        idx = 0
        while idx < len(group_keys):
            if LOOKUP_LIMIT_REACHED:
                for j in range(idx, len(group_keys)):
                    unresolved_due_to_limit += len(grouped_by_name_ja[group_keys[j]])
                break

            batch_names = group_keys[idx:idx + SPARQL_NAME_BATCH_SIZE]
            append_progress_line(
                "WDQS resolving %s exact name:ja values..." % len(batch_names)
            )

            batch_candidates = make_sparql_candidates_for_names(batch_names)
            if batch_candidates is None:
                for name_ja in batch_names:
                    unresolved_due_to_limit += len(grouped_by_name_ja[name_ja])
                idx += SPARQL_NAME_BATCH_SIZE
                continue

            for name_ja in batch_names:
                objs = grouped_by_name_ja[name_ja]

                if name_ja in GROUP_MATCH_CACHE:
                    cached = GROUP_MATCH_CACHE[name_ja]
                    if cached:
                        add_match_plan_entries(plan, preview, objs, cached, grouped_label=name_ja)
                    else:
                        add_nomatch_plan_entries(plan, preview, objs, grouped_label=name_ja)
                    continue

                candidates = batch_candidates.get(name_ja, [])
                match = None

                if candidates:
                    append_progress_line(
                        "Scoring WDQS candidates for '%s' (%s object(s), %s candidate(s))..."
                        % (name_ja, len(objs), len(candidates))
                    )
                    match = find_best_match_for_group_from_candidates(name_ja, objs, candidates)

                if match is None:
                    append_progress_line(
                        "No accepted WDQS match for '%s'; falling back to Action API..."
                        % name_ja
                    )
                    match, completed = find_best_match_for_group_via_action(name_ja, objs)
                    if not completed:
                        unresolved_due_to_limit += len(objs)
                        continue

                GROUP_MATCH_CACHE[name_ja] = match

                if match:
                    add_match_plan_entries(plan, preview, objs, match, grouped_label=name_ja)
                else:
                    add_nomatch_plan_entries(plan, preview, objs, grouped_label=name_ja)

            idx += SPARQL_NAME_BATCH_SIZE

        for obj in singles:
            if LOOKUP_LIMIT_REACHED:
                unresolved_due_to_limit += 1
                continue

            append_progress_line(
                "Resolving single object '%s' via Action API..." % get_name(obj.getKeys())
            )

            match, completed = find_best_match_for_obj(obj)

            if not completed:
                unresolved_due_to_limit += 1
                continue

            if match:
                add_match_plan_entries(plan, preview, [obj], match)
            else:
                add_nomatch_plan_entries(plan, preview, [obj])

        append_progress_line("Finished Wikidata lookups.")
        append_progress_line("Total actual Wikidata lookup calls: %s" % WIKIDATA_LOOKUP_COUNT)

        close_progress_dialog()

        if not plan:
            lines = [
                "No selected river objects found to review.",
                "Skipped due to waterway:botcheck=yes: %s" % len(skipped),
                "Wikidata lookup calls made: %s" % WIKIDATA_LOOKUP_COUNT
            ]
            if unresolved_due_to_limit > 0:
                lines.append(
                    "Left untouched because lookup limit was reached: %s"
                    % unresolved_due_to_limit
                )
            show_summary_dialog(lines)
            return

        preview.insert(0, "Total proposed changes: %s" % len(plan))
        preview.insert(1, "Skipped due to waterway:botcheck=yes: %s" % len(skipped))
        preview.insert(2, "Wikidata lookup calls made: %s / %s" % (WIKIDATA_LOOKUP_COUNT, MAX_WIKIDATA_LOOKUPS))
        if unresolved_due_to_limit > 0:
            preview.insert(3, "Left untouched because lookup limit was reached: %s" % unresolved_due_to_limit)
            preview.insert(4, "-" * 110)
        else:
            preview.insert(3, "-" * 110)

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
        summary.insert(2, "Wikidata lookup calls made: %s / %s" % (WIKIDATA_LOOKUP_COUNT, MAX_WIKIDATA_LOOKUPS))
        if unresolved_due_to_limit > 0:
            summary.insert(3, "Left untouched because lookup limit was reached: %s" % unresolved_due_to_limit)
            summary.insert(4, "-" * 110)
        else:
            summary.insert(3, "-" * 110)

        show_summary_dialog(summary)

    except Exception as e:
        close_progress_dialog()
        show_summary_dialog([
            "Script stopped because of an error.",
            str(e)
        ])


class WorkerRunnable(Runnable):
    def run(self):
        run_script()


def process():
    init_progress_dialog()
    append_progress_line("Starting review of selected waterways...")
    Thread(WorkerRunnable()).start()


process()
