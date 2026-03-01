import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker, GeoJSON, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  fetchAircraft,
  fetchStrikes,
  fetchBases,
  fetchNews,
  fetchAircraftInfo,
  fetchTrail,
  classifyAircraft,
  getAircraftLabel,
  getAircraftShape,
  countryFlag,
  countryName,
  conflictScore,
  scoreColor,
  scoreLabel,
  CATEGORY_META,
  type AircraftPosition,
  type AircraftInfo,
  type AircraftShape,
  type ScoreResult,
  type TrailPoint,
  type StrikeEvent,
  type MilitaryBase,
  type NewsItem,
  type MilCategory,
} from "./tracker-api";
import iranGeoJson from "./iran.geo.json";
import israelGeoJson from "./israel.geo.json";

const ME_CENTER: [number, number] = [32.0, 44.0];
const GLOBAL_CENTER: [number, number] = [25.0, 30.0];
const ME_ZOOM = 5;
const GLOBAL_ZOOM = 3;
const AC_REFRESH = 15_000;
const TRAIL_REFRESH = 10_000;

// ---------------------------------------------------------------------------
// Aircraft silhouette SVG paths per shape type
// ---------------------------------------------------------------------------

const ICON_SIZE = 38;

const SILHOUETTE: Record<AircraftShape, string> = {
  "tanker-jet":
    // Swept-wing 4-engine jet (KC-135 style) — nose up
    `<path d="M12 1.5L11 5V8L4 11.5V13L11 11.5V17L8.5 19V20.5L12 19.5L15.5 20.5V19L13 17V11.5L20 13V11.5L13 8V5L12 1.5Z" fill="COLOR" stroke="#000" stroke-width=".4"/>`,
  "awacs":
    // E-3 Sentry with rotodome disc on top
    `<path d="M12 2L11 5V8L4 11.5V13L11 11.5V17L8.5 19V20.5L12 19.5L15.5 20.5V19L13 17V11.5L20 13V11.5L13 8V5L12 2Z" fill="COLOR" stroke="#000" stroke-width=".4"/>
     <ellipse cx="12" cy="8.5" rx="4.5" ry="1" fill="COLOR" stroke="#000" stroke-width=".4"/>`,
  "transport-prop":
    // C-130 style — high straight wing, 4 props, thick fuselage
    `<path d="M12 1.5L11 4V7.5L3.5 10V12L11 10.5V17.5L8 19.5V21L12 20L16 21V19.5L13 17.5V10.5L20.5 12V10L13 7.5V4L12 1.5Z" fill="COLOR" stroke="#000" stroke-width=".4"/>
     <circle cx="7" cy="10.5" r=".8" fill="#fff" opacity=".4"/>
     <circle cx="9" cy="10.2" r=".8" fill="#fff" opacity=".4"/>
     <circle cx="15" cy="10.2" r=".8" fill="#fff" opacity=".4"/>
     <circle cx="17" cy="10.5" r=".8" fill="#fff" opacity=".4"/>`,
  "transport-jet":
    // C-17 style — high wing, T-tail, 4 engines
    `<path d="M12 1L11.2 4V7.5L3 10.5V12.5L11.2 11V16.5L9 19.5V21L12 20L15 21V19.5L12.8 16.5V11L21 12.5V10.5L12.8 7.5V4L12 1Z" fill="COLOR" stroke="#000" stroke-width=".4"/>
     <path d="M10 20.5H14" stroke="COLOR" stroke-width="1.5"/>`,
  "patrol-jet":
    // P-8 style — twin engine, swept wing
    `<path d="M12 2L11.2 5V8.5L5 11.5V13L11.2 11.5V17.5L9 19V20.5L12 19.5L15 20.5V19L12.8 17.5V11.5L19 13V11.5L12.8 8.5V5L12 2Z" fill="COLOR" stroke="#000" stroke-width=".4"/>`,
  "recon-jet":
    // RC-135 — 4-engine swept wing, long fuselage
    `<path d="M12 1L11 4.5V8L3.5 11V12.5L11 11V17L8.5 19V20.5L12 19.5L15.5 20.5V19L13 17V11L20.5 12.5V11L13 8V4.5L12 1Z" fill="COLOR" stroke="#000" stroke-width=".4"/>`,
  "uav":
    // MQ-9/RQ-4 drone — very long straight wings, V-tail, thin fuselage
    `<path d="M12 3L11.5 5V9L2.5 11.5V12.5L11.5 11V17L10 19.5L12 18.5L14 19.5L12.5 17V11L21.5 12.5V11.5L12.5 9V5L12 3Z" fill="COLOR" stroke="#000" stroke-width=".4"/>`,
  "bizjet":
    // Gulfstream — small swept wing, T-tail
    `<path d="M12 2.5L11.3 5V9L6 11.5V12.5L11.3 11.5V17L9.5 18.5V19.5L12 19L14.5 19.5V18.5L12.7 17V11.5L18 12.5V11.5L12.7 9V5L12 2.5Z" fill="COLOR" stroke="#000" stroke-width=".4"/>`,
  "heli":
    // Helicopter — rotor disc, body, tail boom
    `<ellipse cx="12" cy="8" rx="7" ry="1.2" fill="COLOR" opacity=".5" stroke="#000" stroke-width=".3"/>
     <path d="M12 6C10.5 6 10 7.5 10 9V14C10 15 10.5 16 12 16C13.5 16 14 15 14 14V9C14 7.5 13.5 6 12 6Z" fill="COLOR" stroke="#000" stroke-width=".4"/>
     <path d="M12 16V20.5" stroke="COLOR" stroke-width="1"/>
     <path d="M10 20L14 21" stroke="COLOR" stroke-width=".8"/>`,
  "generic-jet":
    // Generic military jet
    `<path d="M12 2L11 5V8.5L5.5 11.5V13L11 11.5V17L9 19V20.5L12 19.5L15 20.5V19L13 17V11.5L18.5 13V11.5L13 8.5V5L12 2Z" fill="COLOR" stroke="#000" stroke-width=".4"/>`,
};

function aircraftIcon(
  cat: MilCategory,
  shape: AircraftShape,
  track: number | null,
  label: string,
  flag: string,
  country: string,
  selected = false,
): L.DivIcon {
  const color = CATEGORY_META[cat].color;
  const rot = track ?? 0;
  const size = selected ? ICON_SIZE + 8 : ICON_SIZE;
  const glow = selected
    ? `filter:drop-shadow(0 0 8px ${color}) drop-shadow(0 0 16px ${color})`
    : "filter:drop-shadow(0 1px 3px rgba(0,0,0,.7))";
  const svg = SILHOUETTE[shape].replace(/COLOR/g, color);
  const flagHtml = flag
    ? `<span style="font-size:14px;margin-right:2px;vertical-align:middle">${flag}</span>`
    : "";
  const countryHtml = country
    ? `<span style="font-size:10px;color:#ccc;font-weight:500">${country}</span>`
    : "";
  const sep = label && country ? `<span style="color:#666;margin:0 2px">·</span>` : "";
  const labelHtml = (label || flag || country)
    ? `<div style="position:absolute;top:${size + 2}px;left:50%;transform:translateX(-50%);
        white-space:nowrap;font-size:12px;font-weight:700;color:${color};
        text-shadow:0 0 4px #000,0 0 8px #000,0 1px 3px #000;letter-spacing:.3px;
        pointer-events:none;display:flex;align-items:center;gap:1px">
        ${flagHtml}<span>${label}</span>${sep}${countryHtml}</div>`
    : "";
  return L.divIcon({
    className: "ac-marker",
    iconSize: [size, size + (labelHtml ? 20 : 0)],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -size / 2],
    html: `<div style="position:relative;width:${size}px;height:${size}px">
      <div style="transform:rotate(${rot}deg);width:${size}px;height:${size}px;display:flex;align-items:center;justify-content:center;${glow}">
        <svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none">${svg}</svg>
      </div>${labelHtml}</div>`,
  });
}


function strikeIcon(fatalities: number | null): L.DivIcon {
  const s = fatalities && fatalities > 10 ? 18 : fatalities && fatalities > 0 ? 13 : 9;
  return L.divIcon({
    className: "",
    iconSize: [s, s],
    iconAnchor: [s / 2, s / 2],
    popupAnchor: [0, -s / 2],
    html: `<div style="width:${s}px;height:${s}px;border-radius:50%;background:rgba(239,68,68,.7);border:1.5px solid #dc2626;box-shadow:0 0 8px rgba(239,68,68,.4)"></div>`,
  });
}

const BASE_TYPE_META: Record<string, { color: string; svg: string; label: string }> = {
  airbase: {
    color: "#f59e0b",
    label: "Airbase",
    svg: `<path d="M12 3L6 8h3v5H5l7 6 7-6h-4V8h3L12 3z" fill="COLOR" stroke="#000" stroke-width=".5"/>`,
  },
  naval_base: {
    color: "#06b6d4",
    label: "Naval Base",
    svg: `<path d="M4 14c1.5-2 3-3 5-3h6c2 0 3.5 1 5 3H4zm2 2h12v1H6v-1zM10 8h4v3h-4V8z" fill="COLOR" stroke="#000" stroke-width=".5"/>`,
  },
  base: {
    color: "#a3a3a3",
    label: "Military Base",
    svg: `<rect x="5" y="6" width="14" height="10" rx="1" fill="COLOR" stroke="#000" stroke-width=".5"/><path d="M8 9h8v4H8z" fill="#000" opacity=".2"/>`,
  },
};

function baseIcon(baseType: string, name: string | null): L.DivIcon {
  const meta = BASE_TYPE_META[baseType] || BASE_TYPE_META.base;
  const svg = meta.svg.replace(/COLOR/g, meta.color);
  const lbl = name ? `<div style="position:absolute;top:22px;left:50%;transform:translateX(-50%);white-space:nowrap;font:600 8px/1 sans-serif;color:${meta.color};text-shadow:0 0 3px #000,0 0 6px #000;pointer-events:none">${name.length > 18 ? name.slice(0, 18) + "…" : name}</div>` : "";
  return L.divIcon({
    className: "",
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -12],
    html: `<div style="position:relative;width:24px;height:24px;opacity:.85"><svg viewBox="0 0 24 24" width="24" height="24">${svg}</svg>${lbl}</div>`,
  });
}

// ---------------------------------------------------------------------------
// Side panel — Flightradar-style detail view
// ---------------------------------------------------------------------------

function SidePanel({
  aircraft,
  trail,
  onClose,
}: {
  aircraft: AircraftPosition;
  trail: TrailPoint[];
  onClose: () => void;
}) {
  const [info, setInfo] = useState<AircraftInfo | null>(null);
  const [infoLoading, setInfoLoading] = useState(false);

  const cat = classifyAircraft(aircraft);
  const catMeta = CATEGORY_META[cat];

  useEffect(() => {
    if (!aircraft.aircraft_type) {
      setInfo(null);
      return;
    }
    setInfoLoading(true);
    fetchAircraftInfo(aircraft.aircraft_type).then((d) => {
      setInfo(d);
      setInfoLoading(false);
    });
  }, [aircraft.aircraft_type]);

  const altStr =
    typeof aircraft.alt_baro === "number"
      ? `${aircraft.alt_baro.toLocaleString()} ft`
      : aircraft.alt_baro ?? "—";
  const speedStr = aircraft.ground_speed != null ? `${aircraft.ground_speed.toFixed(0)} kts` : "—";
  const headingStr = aircraft.track != null ? `${aircraft.track.toFixed(0)}°` : "—";

  const trailDurationMin =
    trail.length > 1 ? Math.round((Date.now() / 1000 - trail[0].ts) / 60) : 0;
  const trailPct = Math.min(100, (trailDurationMin / 60) * 100);

  const maxAlt =
    trail.length > 0
      ? Math.max(...trail.map((p) => p.alt ?? 0))
      : typeof aircraft.alt_baro === "number"
        ? aircraft.alt_baro
        : 0;

  return (
    <>
      {/* Header */}
      <div className="side-panel-header">
        <div>
          <div className="sp-callsign">{aircraft.flight?.trim() || "UNKNOWN"}</div>
          <div className="sp-subtitle">
            {aircraft.registration || "—"} · {aircraft.aircraft_type || "—"}
          </div>
        </div>
        <button className="sp-close" onClick={onClose}>
          ✕
        </button>
      </div>

      {/* Scrollable body */}
      <div className="side-panel-body">
        {/* Aircraft photo */}
        {info?.image_url && (
          <img className="side-panel-photo" src={info.image_url} alt={info.name || ""} />
        )}
        {infoLoading && !info?.image_url && (
          <div className="side-panel-section" style={{ textAlign: "center", color: "#8888a0" }}>
            Loading aircraft data...
          </div>
        )}

        {/* Aircraft name + description */}
        {info?.name && (
          <div className="side-panel-section">
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>{info.name}</div>
            {info.description && <div className="sp-desc-text">{info.description}</div>}
            {info.wiki_url && (
              <a
                className="sp-wiki-link"
                href={info.wiki_url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ marginTop: 8 }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                  <polyline points="15 3 21 3 21 9" />
                  <line x1="10" y1="14" x2="21" y2="3" />
                </svg>
                Wikipedia
              </a>
            )}
          </div>
        )}

        {/* Category */}
        <div className="side-panel-section">
          <div className="sp-label">Classification</div>
          <div className="sp-badge">
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: catMeta.color,
                flexShrink: 0,
              }}
            />
            {catMeta.label}
          </div>
        </div>

        {/* Flight data */}
        <div className="side-panel-section">
          <div className="sp-label">Flight Data</div>
          <div className="sp-grid">
            <div>
              <div className="sp-field-label">Callsign</div>
              <div className="sp-field-value">{aircraft.flight?.trim() || "—"}</div>
            </div>
            <div>
              <div className="sp-field-label">Registration</div>
              <div className="sp-field-value">{aircraft.registration || "—"}</div>
            </div>
            <div>
              <div className="sp-field-label">Type</div>
              <div className="sp-field-value">{aircraft.aircraft_type || "—"}</div>
            </div>
            <div>
              <div className="sp-field-label">ICAO Hex</div>
              <div className="sp-field-value" style={{ fontFamily: "monospace" }}>
                {aircraft.hex?.toUpperCase() || "—"}
              </div>
            </div>
            {aircraft.squawk && (
              <div>
                <div className="sp-field-label">Squawk</div>
                <div className="sp-field-value" style={{ fontFamily: "monospace" }}>
                  {aircraft.squawk}
                </div>
              </div>
            )}
            {aircraft.description && (
              <div>
                <div className="sp-field-label">Description</div>
                <div className="sp-field-value" style={{ fontSize: 12 }}>{aircraft.description}</div>
              </div>
            )}
          </div>
        </div>

        {/* Telemetry */}
        <div className="side-panel-section">
          <div className="sp-label">Live Telemetry</div>
          <div className="sp-grid">
            <div>
              <div className="sp-field-label">Altitude</div>
              <div className="sp-field-value">{altStr}</div>
            </div>
            <div>
              <div className="sp-field-label">Speed</div>
              <div className="sp-field-value">{speedStr}</div>
            </div>
            <div>
              <div className="sp-field-label">Heading</div>
              <div className="sp-field-value">{headingStr}</div>
            </div>
            <div>
              <div className="sp-field-label">Max Alt</div>
              <div className="sp-field-value">
                {maxAlt > 0 ? `${maxAlt.toLocaleString()} ft` : "—"}
              </div>
            </div>
          </div>
        </div>

        {/* Trail */}
        <div className="side-panel-section">
          <div className="sp-label">Position Trail</div>
          <div className="sp-grid">
            <div>
              <div className="sp-field-label">Data Points</div>
              <div className="sp-field-value">{trail.length}</div>
            </div>
            <div>
              <div className="sp-field-label">Duration</div>
              <div className="sp-field-value">{trailDurationMin > 0 ? `${trailDurationMin} min` : "—"}</div>
            </div>
          </div>
          <div className="sp-trail-bar">
            <div
              className="sp-trail-fill"
              style={{ width: `${trailPct}%`, background: catMeta.color }}
            />
          </div>
          <div style={{ fontSize: 10, color: "#8888a0", marginTop: 4 }}>
            Up to 60 min history · refreshes every 10s
          </div>
        </div>

        {/* Altitude profile (simple) */}
        {trail.length > 2 && (
          <div className="side-panel-section">
            <div className="sp-label">Altitude Profile</div>
            <AltitudeChart trail={trail} color={catMeta.color} />
          </div>
        )}

        {/* Data source */}
        <div className="side-panel-section" style={{ paddingBottom: 24 }}>
          <div style={{ fontSize: 10, color: "#8888a0", lineHeight: 1.6 }}>
            Data: adsb.lol (ADS-B) · Position updated every 15s
            <br />
            Aircraft info: Wikipedia
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Mini altitude chart (SVG sparkline)
// ---------------------------------------------------------------------------

function AltitudeChart({ trail, color }: { trail: TrailPoint[]; color: string }) {
  const W = 340;
  const H = 60;
  const PAD = 2;

  const alts = trail.map((p) => p.alt ?? 0);
  const minAlt = Math.min(...alts);
  const maxAlt = Math.max(...alts);
  const range = maxAlt - minAlt || 1;

  const points = trail
    .map((_, i) => {
      const x = PAD + ((W - 2 * PAD) * i) / (trail.length - 1);
      const y = H - PAD - ((alts[i] - minAlt) / range) * (H - 2 * PAD);
      return `${x},${y}`;
    })
    .join(" ");

  const areaPoints = `${PAD},${H - PAD} ${points} ${W - PAD},${H - PAD}`;

  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: "block" }}>
        <polygon points={areaPoints} fill={color} fillOpacity="0.12" />
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#8888a0", marginTop: 2 }}>
        <span>{minAlt > 0 ? `${minAlt.toLocaleString()} ft` : "GND"}</span>
        <span>{maxAlt > 0 ? `${maxAlt.toLocaleString()} ft` : ""}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Map helper components
// ---------------------------------------------------------------------------

function ZoomControl() {
  const map = useMap();
  useEffect(() => {
    L.control.zoom({ position: "bottomright" }).addTo(map);
  }, [map]);
  return null;
}

function MapClickHandler({ onDeselect }: { onDeselect: () => void }) {
  useMapEvents({ click: () => onDeselect() });
  return null;
}

function FollowAircraft({ position }: { position: [number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (position) map.panTo(position, { animate: true, duration: 0.5 });
  }, [map, position]);
  return null;
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export function App() {
  const [aircraft, setAircraft] = useState<AircraftPosition[]>([]);
  const [strikes, setStrikes] = useState<StrikeEvent[]>([]);
  const [bases, setBases] = useState<MilitaryBase[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdate, setLastUpdate] = useState("--");
  const [globalView, setGlobalView] = useState(false);
  const [acVisible, setAcVisible] = useState<Record<MilCategory, boolean>>({
    tanker: true,
    awacs: true,
    transport: true,
    recon: true,
    other: true,
  });
  const [showStrikes, setShowStrikes] = useState(true);
  const [strikeDays, setStrikeDays] = useState(90);
  const [showBases, setShowBases] = useState(true);
  const [hints, setHints] = useState<string[]>([]);
  const [hintsVisible, setHintsVisible] = useState(true);

  const [selectedHex, setSelectedHex] = useState<string | null>(null);
  const [trail, setTrail] = useState<TrailPoint[]>([]);
  const [followPos, setFollowPos] = useState<[number, number] | null>(null);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [showTable, setShowTable] = useState(false);
  const [showNews, setShowNews] = useState(false);

  const acTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const trailTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // --- Data loaders ---

  const loadAircraft = useCallback(async () => {
    setLoading(true);
    try {
      const d = await fetchAircraft(globalView);
      setAircraft(d.aircraft);
      setLastUpdate(new Date().toLocaleTimeString());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [globalView]);

  const loadStrikes = useCallback(async () => {
    try {
      const d = await fetchStrikes({ days: strikeDays });
      setStrikes(d.events);
      if (d.hint) setHints((prev) => (prev.includes(d.hint!) ? prev : [...prev, d.hint!]));
    } catch (e) {
      console.error(e);
    }
  }, [strikeDays]);

  const loadBases = useCallback(async () => {
    try {
      const d = await fetchBases(globalView);
      setBases(d.bases);
    } catch (e) {
      console.error(e);
    }
  }, [globalView]);

  const loadNews = useCallback(async () => {
    try {
      const d = await fetchNews(50);
      setNews(d.items);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadTrail = useCallback(async () => {
    if (!selectedHex) return;
    try {
      const d = await fetchTrail(selectedHex);
      setTrail(d.points);
    } catch (e) {
      console.error(e);
    }
  }, [selectedHex]);

  // --- Lifecycle ---

  useEffect(() => {
    loadAircraft();
    loadStrikes();
    loadBases();
    loadNews();
    acTimer.current = setInterval(loadAircraft, AC_REFRESH);
    const newsTimer = setInterval(loadNews, 900_000);
    return () => {
      if (acTimer.current) clearInterval(acTimer.current);
      clearInterval(newsTimer);
    };
  }, [loadAircraft, loadStrikes, loadBases, loadNews]);

  useEffect(() => {
    if (selectedHex) {
      loadTrail();
      trailTimer.current = setInterval(loadTrail, TRAIL_REFRESH);
    } else {
      setTrail([]);
    }
    return () => {
      if (trailTimer.current) clearInterval(trailTimer.current);
    };
  }, [selectedHex, loadTrail]);

  useEffect(() => {
    if (!selectedHex) {
      setFollowPos(null);
      return;
    }
    const ac = aircraft.find((a) => a.hex === selectedHex);
    if (ac?.lat != null && ac?.lon != null) setFollowPos([ac.lat, ac.lon]);
  }, [aircraft, selectedHex]);

  // --- Handlers ---

  const selectAircraft = useCallback((hex: string | null) => {
    setSelectedHex(hex);
  }, []);

  const deselectAll = useCallback(() => {
    setSelectedHex(null);
  }, []);

  const acCounts = useMemo(() => {
    const c: Record<MilCategory, number> = { tanker: 0, awacs: 0, transport: 0, recon: 0, other: 0 };
    for (const ac of aircraft) c[classifyAircraft(ac)]++;
    return c;
  }, [aircraft]);

  const trailPositions: [number, number][] = useMemo(
    () => trail.map((p) => [p.lat, p.lon] as [number, number]),
    [trail],
  );

  const selectedAc = selectedHex ? aircraft.find((a) => a.hex === selectedHex) : null;
  const selectedCat = selectedAc ? classifyAircraft(selectedAc) : null;
  const panelOpen = selectedAc != null || showTable || showNews;

  // Iran conflict aircraft — scored by involvement level, sorted descending
  const conflictAircraft = useMemo(() => {
    const CONFLICT_COUNTRIES = new Set(["US", "IL", "GB", "FR", "DE", "SA", "AE", "BH", "QA"]);
    return aircraft
      .filter((ac) => ac.country_code && CONFLICT_COUNTRIES.has(ac.country_code))
      .map((ac) => ({ ...ac, _sr: conflictScore(ac) }))
      .sort((a, b) => b._sr.score - a._sr.score);
  }, [aircraft]);

  const toggleCat = useCallback((cat: MilCategory) => {
    setAcVisible((p) => ({ ...p, [cat]: !p[cat] }));
  }, []);

  // Compute trail dot positions (every ~5th point for visual dots along the path)
  const trailDots: { pos: [number, number]; opacity: number }[] = useMemo(() => {
    if (trail.length < 3) return [];
    const step = Math.max(1, Math.floor(trail.length / 30));
    const dots: { pos: [number, number]; opacity: number }[] = [];
    for (let i = 0; i < trail.length; i += step) {
      dots.push({
        pos: [trail[i].lat, trail[i].lon],
        opacity: 0.3 + (0.7 * i) / trail.length,
      });
    }
    return dots;
  }, [trail]);

  return (
    <div style={{ position: "relative", height: "100%", width: "100%" }}>
      {/* Map */}
      <MapContainer
        center={globalView ? GLOBAL_CENTER : ME_CENTER}
        zoom={globalView ? GLOBAL_ZOOM : ME_ZOOM}
        zoomControl={false}
        style={{ height: "100%", width: "100%" }}
      >
        <TileLayer
          attribution='&copy; <a href="https://carto.com/">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        />
        {/* Iran border highlight */}
        <GeoJSON
          data={iranGeoJson as GeoJSON.FeatureCollection}
          style={{
            fillColor: "#dc2626",
            fillOpacity: 0.08,
            color: "#dc2626",
            weight: 1.5,
            opacity: 0.35,
            dashArray: "6 3",
          }}
        />
        {/* Israel border highlight */}
        <GeoJSON
          data={israelGeoJson as GeoJSON.FeatureCollection}
          style={{
            fillColor: "#3b82f6",
            fillOpacity: 0.06,
            color: "#3b82f6",
            weight: 1.5,
            opacity: 0.3,
            dashArray: "6 3",
          }}
        />
        <ZoomControl />
        <MapClickHandler onDeselect={deselectAll} />
        <FollowAircraft position={followPos} />

        {/* Trail polyline */}
        {trailPositions.length > 1 && selectedCat && (
          <Polyline
            positions={trailPositions}
            pathOptions={{
              color: CATEGORY_META[selectedCat].color,
              weight: 3,
              opacity: 0.75,
              dashArray: "8 4",
            }}
          />
        )}

        {/* Trail dots along path */}
        {selectedCat &&
          trailDots.map((dot, i) => (
            <CircleMarker
              key={`td-${i}`}
              center={dot.pos}
              radius={3}
              pathOptions={{
                color: CATEGORY_META[selectedCat].color,
                fillColor: CATEGORY_META[selectedCat].color,
                fillOpacity: dot.opacity,
                weight: 0,
              }}
            />
          ))}

        {/* Aircraft markers */}
        {aircraft.map((ac) => {
          const cat = classifyAircraft(ac);
          if (!acVisible[cat] || ac.lat == null || ac.lon == null) return null;
          const isSelected = ac.hex === selectedHex;
          const shape = getAircraftShape(ac);
          const label = ac.aircraft_type || ac.flight?.trim() || "";
          const flag = countryFlag(ac.country_code);
          const cName = countryName(ac.country_code);
          return (
            <Marker
              key={ac.hex || `ac-${ac.lat}-${ac.lon}`}
              position={[ac.lat, ac.lon]}
              icon={aircraftIcon(cat, shape, ac.track, label, flag, cName, isSelected)}
              eventHandlers={{
                click: (e) => {
                  e.originalEvent.stopPropagation();
                  selectAircraft(ac.hex);
                },
              }}
            >
              <Popup maxWidth={260}>
                <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>
                    {ac.flight?.trim() || "Unknown"}
                  </div>
                  <div style={{ color: "#8888a0" }}>{getAircraftLabel(ac)}</div>
                  <div style={{ marginTop: 4 }}>
                    <span
                      style={{
                        display: "inline-block",
                        width: 10,
                        height: 10,
                        borderRadius: "50%",
                        background: CATEGORY_META[cat].color,
                        marginRight: 6,
                        verticalAlign: "middle",
                      }}
                    />
                    {CATEGORY_META[cat].label}
                  </div>
                  <div style={{ marginTop: 6, fontSize: 10, color: "#8888a0" }}>
                    Click for details
                  </div>
                </div>
              </Popup>
            </Marker>
          );
        })}

        {/* Military base markers */}
        {showBases &&
          bases.map((b) => (
            <Marker
              key={`base-${b.id}`}
              position={[b.lat, b.lon]}
              icon={baseIcon(b.base_type, b.name)}
            >
              <Popup maxWidth={280}>
                <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>
                    {b.name || "Unknown Base"}
                  </div>
                  <div style={{ textTransform: "capitalize" }}>
                    {b.base_type.replace("_", " ")}
                  </div>
                  {b.operator && <div>Operator: {b.operator}</div>}
                  <div style={{ color: "#8888a0", fontSize: 11 }}>
                    {b.lat.toFixed(4)}, {b.lon.toFixed(4)}
                  </div>
                </div>
              </Popup>
            </Marker>
          ))}

        {/* Strike markers */}
        {showStrikes &&
          strikes.map((ev) => {
            if (ev.latitude == null || ev.longitude == null) return null;
            return (
              <Marker
                key={ev.event_id || `s-${ev.latitude}-${ev.longitude}-${ev.event_date}`}
                position={[ev.latitude, ev.longitude]}
                icon={strikeIcon(ev.fatalities)}
              >
                <Popup maxWidth={360}>
                  <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                    <div style={{ fontWeight: 700, fontSize: 14 }}>
                      {ev.sub_event_type || ev.event_type}
                    </div>
                    <div>
                      {ev.event_date} &middot; {ev.country}
                    </div>
                    <div>
                      {ev.location}
                      {ev.admin1 ? `, ${ev.admin1}` : ""}
                    </div>
                    {ev.actor1 && <div>Actor: {ev.actor1}</div>}
                    {ev.actor2 && <div>Target: {ev.actor2}</div>}
                    {ev.fatalities != null && ev.fatalities > 0 && (
                      <div style={{ color: "#ef4444", fontWeight: 600 }}>
                        Fatalities: {ev.fatalities}
                      </div>
                    )}
                    {ev.notes && (
                      <div
                        style={{
                          marginTop: 6,
                          color: "#8888a0",
                          maxHeight: 80,
                          overflowY: "auto",
                          fontSize: 11,
                        }}
                      >
                        {ev.notes}
                      </div>
                    )}
                  </div>
                </Popup>
              </Marker>
            );
          })}
      </MapContainer>

      {/* ---- Left side panel ---- */}
      <div className={`side-panel ${panelOpen ? "open" : ""}`}>
        {selectedAc && (
          <SidePanel
            aircraft={selectedAc}
            trail={trail}
            onClose={deselectAll}
          />
        )}
      </div>

      {/* ---- Bottom panel: Conflict table or News feed ---- */}
      {(showTable || showNews) && !selectedAc && (
        <div
          className="side-panel open"
          style={{ width: 620, maxWidth: "55vw" }}
        >
          <div className="sp-header">
            <span style={{ fontWeight: 700, fontSize: 15, letterSpacing: 1 }}>
              {showTable ? "IRAN CONFLICT — AIRCRAFT" : "MILITARY NEWS FEED"}
            </span>
            <button
              className="sp-close"
              onClick={() => { setShowTable(false); setShowNews(false); }}
            >
              ✕
            </button>
          </div>
          <div className="sp-body" style={{ padding: "0 8px 12px" }}>
            {showTable && (
              <>
                <div style={{ fontSize: 10, color: "#8888a0", marginBottom: 8, lineHeight: 1.4 }}>
                  Coalition aircraft ranked by how likely they are involved in the Iran conflict ({conflictAircraft.length} tracked).
                  Main factor: distance to Iran. Multiplied by aircraft role.
                </div>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, tableLayout: "fixed" }}>
                    <colgroup>
                      <col style={{ width: 52 }} />
                      <col style={{ width: 30 }} />
                      <col style={{ width: 72 }} />
                      <col style={{ width: 48 }} />
                      <col style={{ width: 90 }} />
                      <col />
                      <col style={{ width: 56 }} />
                      <col style={{ width: 48 }} />
                    </colgroup>
                    <thead>
                      <tr style={{ borderBottom: "1px solid #2a2a3a", color: "#8888a0", textAlign: "left" }}>
                        <th style={thStyle}>Score</th>
                        <th style={thStyle}></th>
                        <th style={thStyle}>Callsign</th>
                        <th style={thStyle}>Type</th>
                        <th style={thStyle}>Role</th>
                        <th style={thStyle}>Score explanation</th>
                        <th style={thStyle}>Alt (ft)</th>
                        <th style={thStyle}>Spd (kt)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {conflictAircraft.map((ac) => {
                        const cat = classifyAircraft(ac);
                        const flag = countryFlag(ac.country_code);
                        const cName = countryName(ac.country_code);
                        const sr = (ac as typeof ac & { _sr: ScoreResult })._sr;
                        const sCol = scoreColor(sr.score);
                        return (
                          <tr
                            key={ac.hex}
                            onClick={() => selectAircraft(ac.hex)}
                            style={{
                              borderBottom: "1px solid #1a1a2a",
                              cursor: "pointer",
                              background: ac.hex === selectedHex ? "rgba(59,130,246,.15)" : "transparent",
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,.05)")}
                            onMouseLeave={(e) => (e.currentTarget.style.background = ac.hex === selectedHex ? "rgba(59,130,246,.15)" : "transparent")}
                          >
                            <td style={tdStyle}>
                              <span style={{
                                display: "inline-flex", alignItems: "center", gap: 4,
                                fontWeight: 700, color: sCol, fontSize: 13, fontVariantNumeric: "tabular-nums",
                              }}>
                                <span style={{
                                  width: 8, height: 8, borderRadius: "50%", background: sCol, flexShrink: 0,
                                }} />
                                {sr.score}
                              </span>
                              <div style={{ fontSize: 9, color: sCol, opacity: 0.85, marginTop: 1, fontWeight: 600 }}>
                                {scoreLabel(sr.score)}
                              </div>
                            </td>
                            <td style={{ ...tdStyle, fontSize: 18 }}>{flag}</td>
                            <td style={{ ...tdStyle, fontWeight: 600, color: CATEGORY_META[cat].color }}>
                              {ac.flight?.trim() || ac.hex || "?"}
                            </td>
                            <td style={tdStyle}>{ac.aircraft_type || "—"}</td>
                            <td style={tdStyle}>
                              <span style={{ color: CATEGORY_META[cat].color, fontSize: 10 }}>
                                {CATEGORY_META[cat].label}
                              </span>
                              <div style={{ fontSize: 9, color: "#666", marginTop: 1 }}>
                                {CATEGORY_META[cat].desc}
                              </div>
                            </td>
                            <td style={{ ...tdStyle, fontSize: 10, color: "#b0b0c0", lineHeight: 1.5, whiteSpace: "normal" }}>
                              {sr.reasons.map((r, i) => (
                                <span key={i} style={{
                                  display: "inline-block",
                                  background: i === 0 ? `${sCol}18` : "#1a1a2a",
                                  border: `1px solid ${i === 0 ? `${sCol}40` : "#2a2a3a"}`,
                                  borderRadius: 3,
                                  padding: "1px 5px",
                                  margin: "1px 2px 1px 0",
                                  fontSize: 9,
                                  color: i === 0 ? sCol : "#999",
                                }}>
                                  {r}
                                </span>
                              ))}
                            </td>
                            <td style={{ ...tdStyle, fontVariantNumeric: "tabular-nums" }}>
                              {typeof ac.alt_baro === "number" ? Math.round(ac.alt_baro).toLocaleString() : ac.alt_baro || "—"}
                            </td>
                            <td style={{ ...tdStyle, fontVariantNumeric: "tabular-nums" }}>
                              {ac.ground_speed ? Math.round(ac.ground_speed) : "—"}
                            </td>
                          </tr>
                        );
                      })}
                      {conflictAircraft.length === 0 && (
                        <tr>
                          <td colSpan={8} style={{ ...tdStyle, textAlign: "center", color: "#666", padding: "16px 8px" }}>
                            No coalition military aircraft currently tracked
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            {showNews && (
              <>
                <div style={{ fontSize: 10, color: "#8888a0", marginBottom: 8, lineHeight: 1.4 }}>
                  Latest military and Iran conflict news from RSS feeds ({news.length} items).
                </div>
                {news.length === 0 && (
                  <div style={{ color: "#666", fontSize: 12, padding: 16, textAlign: "center" }}>
                    Loading news...
                  </div>
                )}
                {news.map((item, i) => (
                  <div
                    key={i}
                    style={{
                      borderBottom: "1px solid #1a1a2a",
                      padding: "8px 0",
                    }}
                  >
                    <a
                      href={item.link || "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        color: "#e4e4ef",
                        textDecoration: "none",
                        fontWeight: 600,
                        fontSize: 12,
                        lineHeight: 1.4,
                        display: "block",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.color = "#3b82f6")}
                      onMouseLeave={(e) => (e.currentTarget.style.color = "#e4e4ef")}
                    >
                      {item.title}
                    </a>
                    <div style={{ fontSize: 10, color: "#8888a0", marginTop: 2 }}>
                      <span style={{ color: "#f59e0b" }}>{item.source}</span>
                      {item.published && (
                        <span> · {item.published.replace(/\+0000|GMT/g, "").trim().slice(0, 22)}</span>
                      )}
                    </div>
                    {item.summary && (
                      <div style={{ fontSize: 10, color: "#666", marginTop: 3, lineHeight: 1.4 }}>
                        {item.summary.slice(0, 150)}...
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}

      {/* ---- Top status bar ---- */}
      <div
        style={{
          position: "absolute",
          top: 12,
          left: panelOpen ? 392 : 12,
          zIndex: 1000,
          display: "flex",
          alignItems: "center",
          gap: 8,
          transition: "left 0.3s cubic-bezier(0.4,0,0.2,1)",
        }}
      >
        <div style={panelStyle}>
          <span style={{ fontWeight: 700, letterSpacing: 1 }}>MILTRACK</span>
          <D />
          <span>
            <b style={mono}>{aircraft.length}</b> aircraft
          </span>
          <D />
          <span>
            <b style={{ ...mono, color: "#ef4444" }}>{strikes.length}</b> events
          </span>
          <D />
          <span>
            <b style={{ ...mono, color: "#f59e0b" }}>{bases.length}</b> bases
          </span>
          <D />
          <span
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: loading ? "#facc15" : "#22c55e",
              marginRight: 4,
              verticalAlign: "middle",
            }}
          />
          <span style={{ color: "#8888a0" }}>{lastUpdate}</span>
          <D />
          <button onClick={() => setGlobalView(!globalView)} style={toggleBtnStyle}>
            {globalView ? "GLOBAL" : "MIDDLE EAST"}
          </button>
          <D />
          <button
            onClick={() => { setShowTable(!showTable); setShowNews(false); }}
            style={{
              ...toggleBtnStyle,
              background: showTable ? "#dc262633" : "#1a1a2a",
              borderColor: showTable ? "#dc2626" : "#3a3a5a",
              color: showTable ? "#fca5a5" : "#e4e4ef",
            }}
          >
            CONFLICT TABLE ({conflictAircraft.length})
          </button>
          <D />
          <button
            onClick={() => { setShowNews(!showNews); setShowTable(false); }}
            style={{
              ...toggleBtnStyle,
              background: showNews ? "#f59e0b22" : "#1a1a2a",
              borderColor: showNews ? "#f59e0b" : "#3a3a5a",
              color: showNews ? "#fcd34d" : "#e4e4ef",
            }}
          >
            NEWS {news.length > 0 ? `(${news.length})` : ""}
          </button>
        </div>
      </div>

      {/* ---- Setup hints (dismissible) ---- */}
      {hints.length > 0 && hintsVisible && (
        <div style={{ position: "absolute", bottom: 12, left: 12, zIndex: 1000, maxWidth: 420 }}>
          <div
            style={{
              ...panelStyle,
              flexDirection: "column",
              alignItems: "flex-start",
              gap: 6,
              padding: "10px 14px",
              borderColor: "#f59e0b33",
              position: "relative",
            }}
          >
            <button
              onClick={() => setHintsVisible(false)}
              style={{
                position: "absolute",
                top: 6,
                right: 8,
                background: "none",
                border: "none",
                color: "#8888a0",
                cursor: "pointer",
                fontSize: 14,
              }}
            >
              ✕
            </button>
            <div
              style={{
                fontSize: 10,
                fontWeight: 600,
                textTransform: "uppercase",
                color: "#f59e0b",
                letterSpacing: 1,
              }}
            >
              Optional Setup
            </div>
            <div style={{ fontSize: 11, color: "#ccc", lineHeight: 1.4 }}>
              Aircraft tracking works with no setup. For extra data layers:
            </div>
            {hints.map((h, i) => (
              <div key={i} style={{ fontSize: 11, color: "#aaa", lineHeight: 1.4 }}>
                • {h}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---- Filter panel (right) ---- */}
      <div style={{ position: "absolute", top: 12, right: 12, zIndex: 1000, width: 250 }}>
        <div
          style={{
            ...panelStyle,
            flexDirection: "column",
            alignItems: "stretch",
            gap: 8,
            padding: "12px 14px",
          }}
        >
          <div style={{ ...sectionTitle, justifyContent: "space-between" }}>
            <span>Aircraft Layers</span>
          </div>
          {(Object.keys(CATEGORY_META) as MilCategory[]).map((cat) => (
            <Row key={cat}>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Dot color={CATEGORY_META[cat].color} />
                <span style={{ fontSize: 12 }}>{CATEGORY_META[cat].label}</span>
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Badge>{acCounts[cat]}</Badge>
                <input
                  type="checkbox"
                  checked={acVisible[cat]}
                  onChange={() => toggleCat(cat)}
                  style={{ accentColor: CATEGORY_META[cat].color }}
                />
              </span>
            </Row>
          ))}

          <div style={{ borderTop: "1px solid #2a2a3a", marginTop: 4, paddingTop: 8 }}>
            <div style={sectionTitle}>Conflict Events (GDELT)</div>
            <Row>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: "rgba(239,68,68,.7)",
                    border: "1.5px solid #dc2626",
                    display: "inline-block",
                  }}
                />
                <span style={{ fontSize: 12 }}>Strikes & Battles</span>
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Badge>{strikes.filter((e) => e.latitude != null).length}</Badge>
                <input
                  type="checkbox"
                  checked={showStrikes}
                  onChange={() => setShowStrikes(!showStrikes)}
                  style={{ accentColor: "#ef4444" }}
                />
              </span>
            </Row>
            <div
              style={{
                marginTop: 8,
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 12,
                color: "#8888a0",
              }}
            >
              <span>Look-back:</span>
              <select
                value={strikeDays}
                onChange={(e) => setStrikeDays(Number(e.target.value))}
                style={selectStyle}
              >
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
                <option value={180}>6 months</option>
              </select>
            </div>
            <div style={{ fontSize: 10, color: "#666", marginTop: 4, lineHeight: 1.4 }}>
              {strikes.length} total reports ({strikes.filter((e) => e.latitude != null).length} geocoded)
            </div>
          </div>

          <div style={{ borderTop: "1px solid #2a2a3a", marginTop: 4, paddingTop: 8 }}>
            <div style={{ ...sectionTitle, justifyContent: "space-between" }}>
              <span>Military Bases (OSM)</span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Badge>{bases.length}</Badge>
                <input
                  type="checkbox"
                  checked={showBases}
                  onChange={() => setShowBases(!showBases)}
                  style={{ accentColor: "#f59e0b" }}
                />
              </span>
            </div>
            {showBases && (
              <div style={{ marginTop: 4 }}>
                {Object.entries(BASE_TYPE_META).map(([type, meta]) => {
                  const count = bases.filter((b) => b.base_type === type).length;
                  if (count === 0) return null;
                  return (
                    <Row key={type}>
                      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <Dot color={meta.color} />
                        <span style={{ fontSize: 11 }}>{meta.label}</span>
                      </span>
                      <Badge>{count}</Badge>
                    </Row>
                  );
                })}
              </div>
            )}
          </div>

          <div
            style={{
              borderTop: "1px solid #2a2a3a",
              paddingTop: 6,
              fontSize: 10,
              color: "#8888a0",
              lineHeight: 1.5,
            }}
          >
            Aircraft: adsb.lol + OpenSky · 15s
            <br />
            Events: GDELT &nbsp;|&nbsp; Bases: OpenStreetMap
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const panelStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  background: "rgba(15,15,25,.92)",
  backdropFilter: "blur(8px)",
  border: "1px solid #2a2a3a",
  borderRadius: 8,
  padding: "6px 14px",
  fontSize: 12,
};

const mono: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

const sectionTitle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 1.5,
  color: "#8888a0",
  display: "flex",
  alignItems: "center",
};

const selectStyle: React.CSSProperties = {
  background: "#1a1a2a",
  border: "1px solid #2a2a3a",
  borderRadius: 4,
  color: "#e4e4ef",
  padding: "2px 6px",
  fontSize: 12,
};

const toggleBtnStyle: React.CSSProperties = {
  background: "#1a1a2a",
  border: "1px solid #3a3a5a",
  borderRadius: 4,
  color: "#e4e4ef",
  padding: "2px 10px",
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: 1,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const thStyle: React.CSSProperties = {
  padding: "6px 6px",
  fontSize: 10,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "5px 6px",
  fontSize: 11,
  whiteSpace: "nowrap",
};

function D() {
  return <span style={{ color: "#2a2a3a" }}>|</span>;
}

function Dot({ color }: { color: string }) {
  return (
    <span
      style={{ width: 10, height: 10, borderRadius: "50%", background: color, display: "inline-block" }}
    />
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        background: "#1a1a2a",
        border: "1px solid #2a2a3a",
        borderRadius: 4,
        padding: "1px 6px",
        fontSize: 10,
        fontVariantNumeric: "tabular-nums",
        minWidth: 20,
        textAlign: "center",
        display: "inline-block",
      }}
    >
      {children}
    </span>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        cursor: "pointer",
      }}
    >
      {children}
    </label>
  );
}
