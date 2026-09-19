"""Road graph, cut times, destination choice and safe routing (PLAN 6.5). Contract in CONTRACTS.md.

Routing is a time-dependent Dijkstra: an edge may be used only if its cut time (earliest p10 fire
arrival along it) is later than the time the vehicle finishes traversing it, i.e.
`cut > departure_min + travel_so_far + travel_edge`. Earliest arrival at a node is never worse than
a later one, so plain Dijkstra order is optimal. This is stricter and more useful than the v0
simplification of dropping every edge cut before `departure_min + DEST_MARGIN_MIN`; DEST_MARGIN_MIN
is instead applied to the destination (fire must not reach it within that margin after we arrive).
"""

from __future__ import annotations

import heapq
import json
import math

import networkx as nx
import numpy as np

from . import config
from .grid import lonlat_to_xy, xy_to_lonlat

MEDICAL_CLASSES = ("care_home", "hospital")


def destination_kind_for(asset_class: str) -> str:
    return "medical" if asset_class in MEDICAL_CLASSES else "reception"


class RoadGraph:
    """Undirected networkx graph. Nodes carry lon/lat and x/y (EPSG:25831); edges carry name,
    highway, length_m, speed_kmh, travel_min, closed, cut_min (after cut_times())."""

    def __init__(self, g: nx.Graph | None = None):
        self.g = g if g is not None else nx.Graph()
        self.closed_names: list[str] = []
        self._cut_source = None
        self._node_index = None

    # ---------- construction ----------
    @classmethod
    def from_fixture(cls, path) -> "RoadGraph":
        with open(path) as f:
            data = json.load(f)
        rg = cls()
        for nid, (lon, lat) in data["nodes"].items():
            rg.add_node(nid, lon, lat)
        for e in data["edges"]:
            rg.add_edge(e["u"], e["v"], e.get("name"), e.get("highway", "unclassified"),
                        e.get("length_m"), e.get("speed_kmh"))
        return rg

    @classmethod
    def from_osmnx(cls, bbox_4326, network_type: str = "drive") -> "RoadGraph":
        """bbox = (lon_min, lat_min, lon_max, lat_max). Requires the optional `osmnx` package."""
        try:
            import osmnx as ox  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised only without osmnx
            raise ImportError(
                "RoadGraph.from_osmnx needs the optional package `osmnx` (pip install osmnx); "
                "use RoadGraph.from_fixture(...) for offline work") from exc
        lon_min, lat_min, lon_max, lat_max = bbox_4326
        try:
            mg = ox.graph_from_bbox(bbox=(lon_min, lat_min, lon_max, lat_max), network_type=network_type)
        except TypeError:  # older osmnx signature (north, south, east, west)
            mg = ox.graph_from_bbox(lat_max, lat_min, lon_max, lon_min, network_type=network_type)
        rg = cls()
        for nid, d in mg.nodes(data=True):
            rg.add_node(str(nid), float(d["x"]), float(d["y"]))
        for u, v, d in mg.edges(data=True):
            name = d.get("name")
            if isinstance(name, list):
                name = name[0]
            hw = d.get("highway", "unclassified")
            if isinstance(hw, list):
                hw = hw[0]
            speed = d.get("maxspeed")
            if isinstance(speed, list):
                speed = speed[0]
            try:
                speed = float(str(speed).split()[0]) if speed is not None else None
            except ValueError:
                speed = None
            rg.add_edge(str(u), str(v), name, hw, float(d.get("length", 0.0)), speed, keep_fastest=True)
        return rg

    def add_node(self, nid: str, lon: float, lat: float) -> None:
        x, y = lonlat_to_xy(lon, lat)
        self.g.add_node(nid, lon=float(lon), lat=float(lat), x=float(x), y=float(y))
        self._node_index = None

    def add_edge(self, u, v, name, highway="unclassified", length_m=None, speed_kmh=None, keep_fastest=False):
        if length_m is None:
            du, dv = self.g.nodes[u], self.g.nodes[v]
            length_m = math.hypot(du["x"] - dv["x"], du["y"] - dv["y"])
        if speed_kmh is None:
            speed_kmh = config.DEFAULT_SPEED_KMH.get(highway, config.DEFAULT_SPEED_KMH["unclassified"])
        travel_min = (float(length_m) / 1000.0) / float(speed_kmh) * 60.0
        if keep_fastest and self.g.has_edge(u, v) and self.g[u][v]["travel_min"] <= travel_min:
            return
        self.g.add_edge(u, v, name=name, highway=highway, length_m=float(length_m),
                        speed_kmh=float(speed_kmh), travel_min=travel_min, closed=False, cut_min=math.inf)

    # ---------- state ----------
    def apply_closures(self, closed_edge_names) -> int:
        """Mark every edge whose name is in `closed_edge_names` as closed (SCT closures). Returns count."""
        names = set(closed_edge_names)
        n = 0
        for u, v, d in self.g.edges(data=True):
            if d.get("name") in names and not d["closed"]:
                d["closed"] = True
                n += 1
        self.closed_names = sorted(set(self.closed_names) | names)
        return n

    def clear_closures(self) -> None:
        for _, _, d in self.g.edges(data=True):
            d["closed"] = False
        self.closed_names = []

    def cut_times(self, arrival) -> dict[tuple, float]:
        """Cut time per edge = min arrival_p10 sampled at both endpoints and the midpoint (inf if never
        or outside the grid). Stored on the edge as `cut_min` and returned as {(u, v): minutes} with
        (u, v) sorted so the key does not depend on edge orientation."""
        out = {}
        for u, v, d in self.g.edges(data=True):
            du, dv = self.g.nodes[u], self.g.nodes[v]
            mx, my = (du["x"] + dv["x"]) / 2.0, (du["y"] + dv["y"]) / 2.0
            mlon, mlat = xy_to_lonlat(mx, my)
            samples = [arrival.sample("arrival_p10", du["lon"], du["lat"]),
                       arrival.sample("arrival_p10", dv["lon"], dv["lat"]),
                       arrival.sample("arrival_p10", mlon, mlat)]
            vals = [float(s) for s in samples if not math.isnan(float(s))]
            cut = min(vals) if vals else math.inf
            d["cut_min"] = cut
            out[tuple(sorted((u, v)))] = cut
        self._cut_source = id(arrival)
        return out

    def ensure_cut_times(self, arrival) -> None:
        if self._cut_source != id(arrival):
            self.cut_times(arrival)

    def cut_of(self, u, v) -> float:
        return float(self.g[u][v].get("cut_min", math.inf))

    # ---------- geometry ----------
    def nearest_node(self, lon: float, lat: float) -> str | None:
        if self.g.number_of_nodes() == 0:
            return None
        if self._node_index is None:
            ids = list(self.g.nodes)
            xy = np.array([[self.g.nodes[n]["x"], self.g.nodes[n]["y"]] for n in ids])
            self._node_index = (ids, xy)
        ids, xy = self._node_index
        x, y = lonlat_to_xy(lon, lat)
        i = int(np.argmin((xy[:, 0] - x) ** 2 + (xy[:, 1] - y) ** 2))
        return ids[i]

    def node_lonlat(self, nid) -> tuple[float, float]:
        d = self.g.nodes[nid]
        return (d["lon"], d["lat"])

    # ---------- search ----------
    def earliest_arrival(self, source, departure_min: float = 0.0):
        """Time-dependent Dijkstra. Returns (travel_min: dict[node, float], prev: dict[node, node])."""
        dist = {source: 0.0}
        prev = {}
        heap = [(0.0, source)]
        done = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            for v, e in self.g[u].items():
                if e.get("closed"):
                    continue
                t = e["travel_min"]
                if e.get("cut_min", math.inf) <= departure_min + d + t:
                    continue
                nd = d + t
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        return dist, prev

    def path_to(self, prev: dict, source, target) -> list:
        if target == source:
            return [source]
        if target not in prev:
            return []
        path = [target]
        while path[-1] != source:
            path.append(prev[path[-1]])
        return path[::-1]

    def edge_summary(self, path: list) -> tuple[float, str | None, float, list[str]]:
        """(first_cut_min, first_cut_road, travel_min, highways) along a node path."""
        first_cut, first_road, travel, highways = math.inf, None, 0.0, []
        for u, v in zip(path[:-1], path[1:]):
            e = self.g[u][v]
            travel += e["travel_min"]
            highways.append(e.get("highway"))
            c = e.get("cut_min", math.inf)
            if c < first_cut:
                first_cut, first_road = c, e.get("name")
        return first_cut, first_road, travel, highways


def valid_destinations(destinations: list[dict], kind: str, arrival, cfg=config, exclude_id=None) -> list[dict]:
    """Destinations of `kind` whose burn_prob is below DEST_BURN_PROB_MAX. NaN (outside grid) is treated
    as unknown and therefore invalid (pessimistic)."""
    out = []
    for dst in destinations:
        if dst.get("kind") != kind or dst.get("asset_id") == exclude_id:
            continue
        bp = float(arrival.sample("burn_prob", dst["lon"], dst["lat"]))
        if math.isnan(bp) or bp >= cfg.DEST_BURN_PROB_MAX:
            continue
        out.append(dst)
    return out


def best_route(graph: RoadGraph, asset: dict, destinations: list[dict], arrival,
               departure_min: float = 0.0, cfg=config) -> dict | None:
    """Fastest safe route from the asset to any valid destination of the kind its class needs.
    None when no destination is valid or none is reachable over uncut, unclosed edges."""
    graph.ensure_cut_times(arrival)
    kind = destination_kind_for(asset["asset_class"])
    cands = valid_destinations(destinations, kind, arrival, cfg, exclude_id=asset.get("asset_id"))
    if not cands:
        return None
    source = graph.nearest_node(asset["lon"], asset["lat"])
    if source is None:
        return None
    # Three passes, fastest route within the first pass that finds one:
    #  1. robust: still open DEST_MARGIN_MIN after people are loaded (departure + load + buffer + margin),
    #     so a short road that is cut soon does not beat a longer road away from the fire;
    #  2. open: still open once people are loaded (departure + load + buffer) - decide.py's exit window;
    #  3. now: open at departure, so the coordinator sees where the exit was and when it closes
    #     (decide.py then reports the window as closed).
    load = cfg.LOAD_TIME_MIN.get(asset["asset_class"], 0)
    open_at = departure_min + load + cfg.ROUTE_BUFFER_MIN
    for effective_departure in (open_at + cfg.DEST_MARGIN_MIN, open_at, departure_min):
        best = _best_for_departure(graph, asset, cands, arrival, source, effective_departure, kind, cfg)
        if best is not None:
            return best
    return None


def _best_for_departure(graph, asset, cands, arrival, source, departure_min, kind, cfg):
    dist, prev = graph.earliest_arrival(source, departure_min)
    best = None
    for dst in cands:
        target = graph.nearest_node(dst["lon"], dst["lat"])
        if target not in dist:
            continue
        path = graph.path_to(prev, source, target)
        first_cut, first_road, travel, highways = graph.edge_summary(path)
        dest_p10 = float(arrival.sample("arrival_p10", dst["lon"], dst["lat"]))
        if not math.isnan(dest_p10) and dest_p10 <= departure_min + travel + cfg.DEST_MARGIN_MIN:
            continue
        if best is None or travel < best["travel_min"]:
            best = {
                "asset_id": asset["asset_id"],
                "destination_id": dst["asset_id"],
                "destination_name": dst["name"],
                "destination_kind": kind,
                "travel_min": travel,
                "first_cut_road": first_road,
                "first_cut_min": first_cut,
                "path_lonlat": [(float(asset["lon"]), float(asset["lat"]))]
                               + [graph.node_lonlat(n) for n in path]
                               + [(float(dst["lon"]), float(dst["lat"]))],
                "path_nodes": path,
                "via_track": "track" in highways,
                "departure_min": departure_min,
            }
    return best
