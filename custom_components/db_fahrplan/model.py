"""Pure timetable models and XML normalization independent of Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import unicodedata
from zoneinfo import ZoneInfo

from defusedxml import ElementTree

BERLIN = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True, slots=True)
class Station:
    """A station returned by the DB Timetables API."""

    eva: str
    name: str
    ds100: str = ""
    display_name: str = ""

    @property
    def label(self) -> str:
        """Display label, including the unambiguous EVA number."""
        return f"{self.display_name or self.name} (EVA {self.eva})"


@dataclass(frozen=True, slots=True)
class Departure:
    """One direct departure whose path contains the requested destination."""

    stop_id: str
    line: str
    train: str
    destination: str
    scheduled: datetime | None
    expected: datetime
    platform: str | None
    planned_platform: str | None
    delay_minutes: int | None
    cancelled: bool
    path: tuple[str, ...]


def parse_db_time(value: str | None) -> datetime | None:
    """Parse a DB YYMMDDHHMM timestamp as local German railway time."""
    if not value or not re.fullmatch(r"\d{10}", value):
        return None
    try:
        return datetime.strptime(value, "%y%m%d%H%M").replace(tzinfo=BERLIN)
    except ValueError:
        return None


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_stations(xml: str | bytes) -> list[Station]:
    """Decode station lookup results without unsafe XML entity expansion."""
    root = ElementTree.fromstring(xml)
    stations: dict[str, Station] = {}
    for element in root.iter():
        if _local_tag(element.tag) != "station":
            continue
        eva, name = element.get("eva", ""), element.get("name", "")
        if eva.isdigit() and name:
            stations[eva] = Station(eva=eva, name=name, ds100=element.get("ds100", ""))
    return list(stations.values())


def parse_board(xml: str | bytes) -> dict[str, dict]:
    """Map stop ID to trip label and departure fields (plan or changes)."""
    root = ElementTree.fromstring(xml)
    result: dict[str, dict] = {}
    for stop in root.iter():
        if _local_tag(stop.tag) != "s":
            continue
        stop_id = stop.get("id")
        if not stop_id:
            continue
        label: dict[str, str] = {}
        departure: dict[str, str] = {}
        for child in stop:
            tag = _local_tag(child.tag)
            if tag == "tl":
                label = dict(child.attrib)
            elif tag == "dp":
                departure = dict(child.attrib)
        if departure:
            result[stop_id] = {"tl": label, "dp": departure}
    return result


def merge_boards(
    plan: dict[str, dict], changes: dict[str, dict]
) -> dict[str, dict]:
    """Overlay changed fields without discarding untouched planned values."""
    merged = {key: {"tl": value["tl"].copy(), "dp": value["dp"].copy()}
              for key, value in plan.items()}
    for stop_id, item in changes.items():
        if stop_id not in merged:
            merged[stop_id] = {"tl": item["tl"].copy(), "dp": item["dp"].copy()}
            continue
        merged[stop_id]["tl"].update(item["tl"])
        merged[stop_id]["dp"].update(item["dp"])
    return merged


def normalize_station(name: str) -> str:
    """Canonicalize DB station names, without accepting substring matches."""
    name = name.casefold().strip()
    name = name.replace("hauptbahnhof", "hbf").replace("ostbahnhof", "ost")
    name = name.replace("westbahnhof", "west")
    name = "".join(c for c in unicodedata.normalize("NFKD", name)
                   if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", name)


def station_match_keys(name: str) -> set[str]:
    """Return conservative variants of a DB station name.

    /station names often contain city metadata ("Trudering, München")
    while /plan departure paths call that stop "Trudering" or
    "München-Trudering". Timetables path entries have *no EVA IDs*, so
    exact EVA comparison is not possible for intermediate stops.
    """
    name = name.strip()
    if not name:
        return set()
    variants = {normalize_station(name)}
    if "," in name:
        local, city = (part.strip() for part in name.split(",", 1))
        if local and city:
            variants.add(normalize_station(local))
            variants.add(normalize_station(f"{city}-{local}"))
    # DB city prefixes are common in path data; strip only known city
    # prefixes, not arbitrary first words (avoid Hbf/Ost false matches).
    city_names = ("München", "Berlin", "Hamburg", "Köln", "Frankfurt (Main)", "Frankfurt am Main", "Stuttgart", "Dresden", "Leipzig", "Bremen", "Nürnberg", "Augsburg")
    for city in city_names:
        for separator in ("-", " "):
            prefix = city + separator
            if name.casefold().startswith(prefix.casefold()):
                local = name[len(prefix):].strip()
                # A lone "Hbf" or "Ost" is not a distinct station name.
                if len(normalize_station(local)) >= 5 and normalize_station(local) not in ("hbf", "ost", "west", "nord", "sud"):
                    variants.add(normalize_station(local))
    return {item for item in variants if item}


def select_departures(
    plan: dict[str, dict],
    changes: dict[str, dict],
    destination: str,
    now: datetime,
    hours: int = 4,
    include_cancelled: bool = False,
) -> list[Departure]:
    """Return at most three upcoming direct trains visiting `destination`."""
    destination_keys = station_match_keys(destination)
    if not destination_keys:
        return []
    stop_time = now + timedelta(hours=hours)
    departures: list[Departure] = []
    for stop_id, item in merge_boards(plan, changes).items():
        dp = item["dp"]
        path = tuple(x.strip() for x in (dp.get("cpth") or dp.get("ppth") or "").split("|") if x.strip())
        if not any(station_match_keys(x) & destination_keys for x in path):
            continue
        cancelled = (dp.get("cs") or dp.get("ps")) == "c"
        if cancelled and not include_cancelled:
            continue
        scheduled = parse_db_time(dp.get("pt"))
        expected = parse_db_time(dp.get("ct")) or scheduled
        if expected is None or not now <= expected < stop_time:
            continue
        label = item["tl"]
        train = " ".join(part for part in (label.get("c"), label.get("n")) if part)
        line = dp.get("l") or train or "Unbekannt"
        planned_platform = dp.get("pp")
        platform = dp.get("cp") or planned_platform
        delay = (
            round((expected - scheduled).total_seconds() / 60)
            if scheduled is not None else None
        )
        departures.append(Departure(
            stop_id=stop_id,
            line=line,
            train=train or line,
            destination=path[-1],
            scheduled=scheduled,
            expected=expected,
            platform=platform,
            planned_platform=planned_platform,
            delay_minutes=delay,
            cancelled=cancelled,
            path=path,
        ))
    departures.sort(key=lambda dep: (dep.expected, dep.stop_id))
    return departures[:3]
