import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker, GeoJSON, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  fetchAircraft,
  fetchStrikes,
  fetchBases,
  fetchNews,
  fetchIntel,
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
  type IntelArticle,
  type MilCategory,
  fetchSitrep,
  type SitrepResponse,
  fetchDeathToll,
  type DeathTollResponse,
  type DeathTollPreset,
  fetchFlightAwareRoute,
  type FlightAwareResponse,
} from "./tracker-api";
import iranGeoJson from "./iran.geo.json";
import israelGeoJson from "./israel.geo.json";

const GLOBAL_CENTER: [number, number] = [25.0, 30.0];
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


const DIRECTION_COLORS = {
  to_iran:   { fill: "#3b82f6", border: "#2563eb", label: "STRIKE ON IRAN" },     // blue
  from_iran: { fill: "#ef4444", border: "#dc2626", label: "IRANIAN ATTACK" },     // red
  internal:  { fill: "#f59e0b", border: "#d97706", label: "INTERNAL" },            // amber
  other:     { fill: "#8b5cf6", border: "#7c3aed", label: "OTHER" },              // purple
  unknown:   { fill: "#6b7280", border: "#4b5563", label: "" },                    // gray
} as const;

function strikeClusterIcon(events: StrikeEvent[], enriched: boolean): L.DivIcon {
  const top = events[0];
  const isEnriched = enriched && top.title != null;

  if (!isEnriched) {
    const s = 8;
    const count = events.length;
    return L.divIcon({
      className: "",
      iconSize: [s, s],
      iconAnchor: [s / 2, s / 2],
      popupAnchor: [0, -s / 2],
      html: `<div style="position:relative;width:${s}px;height:${s}px">
        <div style="width:${s}px;height:${s}px;border-radius:50%;background:#6b7280;opacity:0.5;border:1px solid #888"></div>
        ${count > 1 ? `<div style="position:absolute;top:-4px;right:-6px;background:#6b7280;color:#fff;font-size:7px;font-weight:700;border-radius:6px;min-width:12px;height:12px;display:flex;align-items:center;justify-content:center;padding:0 2px">${count}</div>` : ""}
      </div>`,
    });
  }

  const topSeverity = top.severity ?? 5;
  const topConf = top.confidence ?? 0.7;
  const topHours = top.hours_ago ?? 999;

  const s = Math.max(10, Math.min(26, 6 + topSeverity * 2));
  const freshness = Math.max(0.3, 1 - topHours / (90 * 24));
  const dotOpacity = Math.max(0.3, Math.min(0.95, topConf * freshness));

  const topDir = top.attack_direction ?? "unknown";
  const topPalette = DIRECTION_COLORS[topDir] || DIRECTION_COLORS.unknown;
  const glow = topSeverity >= 7 ? `box-shadow:0 0 ${topSeverity}px ${topPalette.fill}80` : "";

  const uid = `sc-${Math.random().toString(36).slice(2, 8)}`;
  const moreCount = events.length - 5;

  const allRows = events.map((ev, i) => {
    const dir = ev.attack_direction ?? "unknown";
    const p = DIRECTION_COLORS[dir] || DIRECTION_COLORS.unknown;
    const title = ev.title || ev.event_type || "";
    const short = title.length > 35 ? title.slice(0, 33) + "…" : title;
    const time = formatHoursAgo(ev.hours_ago);
    const hidden = i >= 5 ? `class="${uid}-extra" style="display:none;align-items:center;gap:4px;padding:2px 0"` : `style="display:flex;align-items:center;gap:4px;padding:2px 0"`;
    return `<div ${hidden}>
      <span style="width:6px;height:6px;border-radius:50%;background:${p.fill};flex-shrink:0"></span>
      <span style="color:${p.fill};font-weight:600">${short}</span>
      ${time ? `<span style="color:#8888a0;margin-left:2px">· ${time}</span>` : ""}
    </div>`;
  });

  if (moreCount > 0) {
    allRows.push(
      `<div id="${uid}-toggle" style="color:#3b82f6;padding:2px 0 0 10px;cursor:pointer;pointer-events:auto" onclick="
        var extras=document.querySelectorAll('.${uid}-extra');
        var show=extras[0]&&extras[0].style.display==='none';
        extras.forEach(function(el){el.style.display=show?'flex':'none'});
        this.textContent=show?'show less':'+${moreCount} more';
        event.stopPropagation();
      ">+${moreCount} more</div>`
    );
  }

  const boxHtml = `<div style="position:absolute;top:${s + 3}px;left:50%;transform:translateX(-50%);
    background:rgba(10,10,18,.92);border:1px solid #2a2a3a;border-radius:5px;padding:4px 8px;
    white-space:nowrap;font-size:9px;line-height:1.4;pointer-events:none;
    backdrop-filter:blur(4px);min-width:120px">${allRows.join("")}</div>`;

  const boxHeight = Math.min(events.length, 5) * 16 + (moreCount > 0 ? 16 : 0) + 14;

  return L.divIcon({
    className: "",
    iconSize: [s, s + boxHeight],
    iconAnchor: [s / 2, s / 2],
    popupAnchor: [0, -s / 2],
    html: `<div style="position:relative;width:${s}px;height:${s}px">
      <div style="width:${s}px;height:${s}px;border-radius:50%;background:${topPalette.fill};opacity:${dotOpacity};border:1.5px solid ${topPalette.border};${glow}"></div>
      ${events.length > 1 ? `<div style="position:absolute;top:-4px;right:-6px;background:#e4e4ef;color:#0a0a0f;font-size:8px;font-weight:700;border-radius:6px;min-width:14px;height:14px;display:flex;align-items:center;justify-content:center;padding:0 3px">${events.length}</div>` : ""}
      ${boxHtml}</div>`,
  });
}

function formatHoursAgo(hours: number | null): string {
  if (hours == null) return "";
  if (hours < 1) return "< 1 hour ago";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "1 day ago";
  if (days < 7) return `${days} days ago`;
  const weeks = Math.floor(days / 7);
  if (weeks === 1) return "1 week ago";
  return `${weeks} weeks ago`;
}

function formatUpdatedAgo(date: Date | null): string {
  if (!date) return "";
  const mins = Math.floor((Date.now() - date.getTime()) / 60000);
  if (mins < 1) return "updated just now";
  if (mins < 60) return `updated ${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs === 1) return "updated 1h ago";
  return `updated ${hrs}h ago`;
}

function formatPublishedAgo(isoDate: string | null): string {
  if (!isoDate) return "";
  try {
    const ms = Date.now() - new Date(isoDate).getTime();
    if (ms < 0) return "";
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days === 1) return "1 day ago";
    return `${days} days ago`;
  } catch {
    return "";
  }
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
  playbackTs,
  onPlaybackTsChange,
  playbackPlaying,
  onPlaybackPlayingChange,
  playbackPosition,
  faRoute,
  faLoading,
  onLoadFlightAware,
}: {
  aircraft: AircraftPosition;
  trail: TrailPoint[];
  onClose: () => void;
  playbackTs: number | null;
  onPlaybackTsChange: (ts: number | null) => void;
  playbackPlaying: boolean;
  onPlaybackPlayingChange: (v: boolean) => void;
  playbackPosition: { lat: number; lon: number; alt: number | null } | null;
  faRoute: FlightAwareResponse | null;
  faLoading: boolean;
  onLoadFlightAware: () => void;
}) {
  const [info, setInfo] = useState<AircraftInfo | null>(null);
  const [infoLoading, setInfoLoading] = useState(false);
  const playRef = useRef<ReturnType<typeof requestAnimationFrame> | null>(null);
  const currentTsRef = useRef<number>(0);

  // Playback animation
  useEffect(() => {
    if (!playbackPlaying || trail.length < 2) return;
    const tStart = trail[0].ts;
    const tEnd = trail[trail.length - 1].ts;
    currentTsRef.current = playbackTs ?? tStart;
    let lastT = performance.now();
    const step = (now: number) => {
      const dt = (now - lastT) / 1000;
      lastT = now;
      currentTsRef.current += dt;
      if (currentTsRef.current >= tEnd) {
        onPlaybackTsChange(tEnd);
        onPlaybackPlayingChange(false);
        return;
      }
      onPlaybackTsChange(currentTsRef.current);
      playRef.current = requestAnimationFrame(step);
    };
    playRef.current = requestAnimationFrame(step);
    return () => {
      if (playRef.current) cancelAnimationFrame(playRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- playbackTs only for initial value
  }, [playbackPlaying, trail]);

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

  const currentAlt = typeof aircraft.alt_baro === "number" ? aircraft.alt_baro : 0;
  const maxAlt =
    trail.length > 0
      ? Math.max(...trail.map((p) => p.alt ?? 0), currentAlt)
      : currentAlt;

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
          <button
            type="button"
            disabled={faLoading}
            onClick={onLoadFlightAware}
            style={{
              marginTop: 8,
              width: "100%",
              padding: "8px 12px",
              background: faRoute?.positions?.length ? "#1e3a5f" : "#1a1a2a",
              border: `1px solid ${faRoute?.positions?.length ? "#3b82f6" : "#2a2a3a"}`,
              borderRadius: 6,
              color: faRoute?.positions?.length ? "#93c5fd" : "#e4e4ef",
              cursor: faLoading ? "wait" : "pointer",
              fontSize: 11,
              fontWeight: 600,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
            }}
          >
            {faLoading ? "Loading..." : faRoute?.positions?.length ? `✓ Full Route · ${faRoute.total} points` : "Full Route (FlightAware)"}
          </button>
          {faRoute && (
            <div style={{ fontSize: 10, marginTop: 4, lineHeight: 1.6 }}>
              {faRoute.positions.length > 0 ? (
                <>
                  <div style={{ color: "#9ca3af" }}>
                    {faRoute.origin && faRoute.destination ? `${faRoute.origin} → ${faRoute.destination}` : "Route loaded"}
                    {faRoute.route_distance ? ` · ${faRoute.route_distance}` : ""}
                  </div>
                  {faRoute.progress_percent != null && (
                    <div style={{ marginTop: 4 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#9ca3af", marginBottom: 2 }}>
                        <span>Progress</span>
                        <span>{faRoute.progress_percent}%</span>
                      </div>
                      <div style={{ height: 4, background: "#1a1a2a", borderRadius: 2, overflow: "hidden" }}>
                        <div style={{ height: "100%", width: `${faRoute.progress_percent}%`, background: "#3b82f6", borderRadius: 2 }} />
                      </div>
                    </div>
                  )}
                  {(faRoute.departure_time || faRoute.arrival_time || faRoute.estimated_arrival) && (
                    <div style={{ color: "#9ca3af", marginTop: 4, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 12px" }}>
                      {faRoute.departure_time && (
                        <>
                          <span style={{ color: "#6b7280" }}>Departed</span>
                          <span>{new Date(faRoute.departure_time).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}</span>
                        </>
                      )}
                      {(faRoute.arrival_time || faRoute.estimated_arrival) && (
                        <>
                          <span style={{ color: "#6b7280" }}>{faRoute.arrival_time ? "Arrived" : "ETA"}</span>
                          <span>{new Date((faRoute.arrival_time || faRoute.estimated_arrival)!).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}</span>
                        </>
                      )}
                      {faRoute.filed_ete != null && (
                        <>
                          <span style={{ color: "#6b7280" }}>Duration</span>
                          <span>{Math.floor(faRoute.filed_ete / 3600)}h {Math.floor((faRoute.filed_ete % 3600) / 60)}m</span>
                        </>
                      )}
                    </div>
                  )}
                  {(faRoute.filed_altitude || faRoute.filed_airspeed) && (
                    <div style={{ color: "#9ca3af", marginTop: 4, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 12px" }}>
                      {faRoute.filed_altitude != null && (
                        <>
                          <span style={{ color: "#6b7280" }}>Filed alt</span>
                          <span>FL{faRoute.filed_altitude / 100}</span>
                        </>
                      )}
                      {faRoute.filed_airspeed != null && (
                        <>
                          <span style={{ color: "#6b7280" }}>Filed speed</span>
                          <span>{faRoute.filed_airspeed} kts</span>
                        </>
                      )}
                    </div>
                  )}
                  {(faRoute.operator || faRoute.owner || faRoute.status) && (
                    <div style={{ color: "#6b7280", marginTop: 4 }}>
                      {faRoute.operator ? `Operator: ${faRoute.operator}` : ""}
                      {faRoute.owner ? `${faRoute.operator ? " · " : ""}Owner: ${faRoute.owner}` : ""}
                      {faRoute.status ? `${faRoute.operator || faRoute.owner ? " · " : ""}${faRoute.status}` : ""}
                    </div>
                  )}
                  {faRoute.filed_route && (
                    <div style={{ color: "#6b7280", marginTop: 4, wordBreak: "break-all" }}>
                      Route: {faRoute.filed_route}
                    </div>
                  )}
                </>
              ) : faRoute.blocked ? (
                <span style={{ color: "#ef4444" }}>Blocked — military aircraft are often hidden on FlightAware</span>
              ) : (
                <span style={{ color: "#f59e0b" }}>{faRoute.message || "No route data available"}</span>
              )}
            </div>
          )}
        </div>

        {/* Playback of flight */}
        {trail.length >= 2 && (
          <div className="side-panel-section">
            <div className="sp-label">Playback of flight</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <button
                type="button"
                onClick={() => {
                  if (playbackPlaying) {
                    onPlaybackPlayingChange(false);
                  } else {
                    const tEnd = trail[trail.length - 1].ts;
                    if ((playbackTs ?? 0) >= tEnd - 1) {
                      onPlaybackTsChange(trail[0].ts);
                    }
                    onPlaybackPlayingChange(true);
                  }
                }}
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 6,
                  border: "1px solid #2a2a3a",
                  background: playbackPlaying ? "#3b82f633" : "#1a1a2a",
                  color: catMeta.color,
                  cursor: "pointer",
                  fontSize: 14,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {playbackPlaying ? "⏸" : "▶"}
              </button>
              <div style={{ flex: 1, fontSize: 11, color: "#9ca3af" }}>
                {playbackPosition ? (
                  <>
                    Alt {playbackPosition.alt != null ? `${Math.round(playbackPosition.alt).toLocaleString()} ft` : "—"}
                    {playbackTs != null && (
                      <span style={{ marginLeft: 8, color: "#6b7280" }}>
                        {new Date(playbackTs * 1000).toISOString().slice(11, 19)} UTC
                      </span>
                    )}
                  </>
                ) : (
                  "Drag to scrub"
                )}
              </div>
            </div>
            <input
              type="range"
              min={trail[0].ts}
              max={trail[trail.length - 1].ts}
              step={1}
              value={playbackTs ?? trail[trail.length - 1].ts}
              onChange={(e) => onPlaybackTsChange(parseFloat(e.target.value))}
              style={{
                width: "100%",
                height: 6,
                accentColor: catMeta.color,
                cursor: "pointer",
              }}
            />
          </div>
        )}

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

function FitRouteBounds({ positions }: { positions: [number, number][] | null }) {
  const map = useMap();
  useEffect(() => {
    if (!positions || positions.length < 2) return;
    const bounds = L.latLngBounds(positions.map(([lat, lon]) => L.latLng(lat, lon)));
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 12, animate: true, duration: 0.8 });
  }, [map, positions]);
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
  const [acVisible, setAcVisible] = useState<Record<MilCategory, boolean>>({
    tanker: true,
    awacs: true,
    transport: true,
    recon: true,
    other: true,
  });
  const [countryFilter, setCountryFilter] = useState<Record<string, boolean>>({});
  const [countryFilterOpen, setCountryFilterOpen] = useState(false);
  const [showStrikes, setShowStrikes] = useState(true);
  const [showBases, setShowBases] = useState(false);
  const [hints, setHints] = useState<string[]>([]);
  const [hintsVisible, setHintsVisible] = useState(true);

  const [selectedHex, setSelectedHex] = useState<string | null>(null);
  const [trail, setTrail] = useState<TrailPoint[]>([]);
  const [faRoute, setFaRoute] = useState<FlightAwareResponse | null>(null);
  const [faLoading, setFaLoading] = useState(false);
  const [fitBoundsPositions, setFitBoundsPositions] = useState<[number, number][] | null>(null);
  const faCacheRef = useRef<Map<string, FlightAwareResponse>>(new Map());
  const [followPos, setFollowPos] = useState<[number, number] | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const [playbackTs, setPlaybackTs] = useState<number | null>(null);
  const [playbackPlaying, setPlaybackPlaying] = useState(false);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [intel, setIntel] = useState<IntelArticle[]>([]);
  const [intelAvailable, setIntelAvailable] = useState(false);
  const [intelUpdatedAt, setIntelUpdatedAt] = useState<Date | null>(null);
  const [strikesUpdatedAt, setStrikesUpdatedAt] = useState<Date | null>(null);
  const [strikesEnriched, setStrikesEnriched] = useState(false);
  const [, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 60_000);
    return () => clearInterval(id);
  }, []);
  const [showTable, setShowTable] = useState(false);
  const [showNews, setShowNews] = useState(false);
  const [showSitrep, setShowSitrep] = useState(false);
  const [sitrep, setSitrep] = useState<SitrepResponse | null>(null);
  const [sitrepUpdatedAt, setSitrepUpdatedAt] = useState<Date | null>(null);
  const [deathToll, setDeathToll] = useState<DeathTollResponse | null>(null);
  const [showDeathTollModal, setShowDeathTollModal] = useState(false);
  const [expandedDeathTollCountry, setExpandedDeathTollCountry] = useState<string | null>(null);
  const [deathTollPreset, setDeathTollPreset] = useState<DeathTollPreset>("30d");
  const [deathTollLoading, setDeathTollLoading] = useState(false);

  useEffect(() => {
    if (!showDeathTollModal) setExpandedDeathTollCountry(null);
  }, [showDeathTollModal]);

  const acTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const trailTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // --- Data loaders ---

  const loadAircraft = useCallback(async () => {
    setLoading(true);
    try {
      const d = await fetchAircraft(true);
      setAircraft(d.aircraft);
      setLastUpdate(new Date().toLocaleTimeString());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadStrikes = useCallback(async () => {
    try {
      const d = await fetchStrikes({ days: 90 });
      setStrikes(d.events);
      setStrikesEnriched(d.enriched);
      setStrikesUpdatedAt(new Date());
      if (d.hint) setHints((prev) => (prev.includes(d.hint!) ? prev : [...prev, d.hint!]));
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadBases = useCallback(async () => {
    try {
      const d = await fetchBases(true);
      setBases(d.bases);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadNews = useCallback(async () => {
    try {
      const d = await fetchNews(75);
      setNews(d.items);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const loadIntel = useCallback(async () => {
    try {
      const d = await fetchIntel(20);
      if (d.articles.length > 0) {
        setIntel(d.articles);
        setIntelAvailable(true);
        setIntelUpdatedAt(new Date());
      } else if (d.pipeline_status?.search?.includes("not set")) {
        setIntelAvailable(false);
      }
    } catch {
      // Intel pipeline not configured — that's fine
    }
  }, []);

  const loadSitrep = useCallback(async () => {
    try {
      const d = await fetchSitrep();
      if (d.threat_level !== "PENDING") {
        setSitrep(d);
        setSitrepUpdatedAt(new Date());
      }
    } catch {
      // SITREP not ready yet
    }
  }, []);

  const loadDeathToll = useCallback(async (preset?: DeathTollPreset) => {
    const p = preset ?? deathTollPreset;
    setDeathTollLoading(true);
    try {
      const d = await fetchDeathToll(p);
      if (d.by_country.length > 0) setDeathToll(d);
    } catch {
      // Death toll not available
    } finally {
      setDeathTollLoading(false);
    }
  }, [deathTollPreset]);

  const faRouteRef = useRef(faRoute);
  faRouteRef.current = faRoute;

  const loadTrail = useCallback(async () => {
    if (!selectedHex) return;
    if (faRouteRef.current && faRouteRef.current.positions.length > 0) return;
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
    loadIntel();
    loadSitrep();
    loadDeathToll();
    acTimer.current = setInterval(loadAircraft, AC_REFRESH);
    const newsTimer = setInterval(loadNews, 300_000);
    const intelTimer = setInterval(loadIntel, 1_800_000);  // 30 min
    const sitrepTimer = setInterval(loadSitrep, 300_000);  // poll every 5 min
    const deathTollTimer = setInterval(loadDeathToll, 300_000);  // 5 min
    return () => {
      if (acTimer.current) clearInterval(acTimer.current);
      clearInterval(newsTimer);
      clearInterval(intelTimer);
      clearInterval(sitrepTimer);
      clearInterval(deathTollTimer);
    };
  }, [loadAircraft, loadStrikes, loadBases, loadNews, loadIntel, loadSitrep, loadDeathToll]);

  useEffect(() => {
    setFaRoute(null);
    setFitBoundsPositions(null);
    setTrail([]);
    if (selectedHex) {
      loadTrail();
      trailTimer.current = setInterval(loadTrail, TRAIL_REFRESH);
    }
    return () => {
      if (trailTimer.current) clearInterval(trailTimer.current);
    };
    // only re-run when aircraft selection changes, not on loadTrail ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedHex]);

  // --- Handlers ---

  const selectAircraft = useCallback((hex: string | null) => {
    setSelectedHex(hex);
  }, []);

  const deselectAll = useCallback(() => {
    setSelectedHex(null);
  }, []);

  const searchMatches = useMemo(() => {
    if (!searchQuery.trim()) return aircraft;
    const q = searchQuery.trim().toLowerCase();
    return aircraft.filter((ac) => {
      const flight = (ac.flight || "").toLowerCase();
      const reg = (ac.registration || "").toLowerCase();
      const hex = (ac.hex || "").toLowerCase();
      const type = (ac.aircraft_type || "").toLowerCase();
      const desc = (ac.description || "").toLowerCase();
      return (
        flight.includes(q) || reg.includes(q) || hex.includes(q) ||
        type.includes(q) || desc.includes(q)
      );
    });
  }, [aircraft, searchQuery]);

  const filteredAircraft = useMemo(() => {
    const byCountry = aircraft.filter((ac) => !ac.country_code || (countryFilter[ac.country_code] !== false));
    if (!searchQuery.trim()) return byCountry;
    const q = searchQuery.trim().toLowerCase();
    return byCountry.filter((ac) => {
      const flight = (ac.flight || "").toLowerCase();
      const reg = (ac.registration || "").toLowerCase();
      const hex = (ac.hex || "").toLowerCase();
      const type = (ac.aircraft_type || "").toLowerCase();
      const desc = (ac.description || "").toLowerCase();
      return flight.includes(q) || reg.includes(q) || hex.includes(q) || type.includes(q) || desc.includes(q);
    });
  }, [aircraft, countryFilter, searchQuery]);

  const acCounts = useMemo(() => {
    const c: Record<MilCategory, number> = { tanker: 0, awacs: 0, transport: 0, recon: 0, other: 0 };
    for (const ac of filteredAircraft) c[classifyAircraft(ac)]++;
    return c;
  }, [filteredAircraft]);

  const toggleCountry = useCallback((cc: string) => {
    setCountryFilter((prev) => ({ ...prev, [cc]: !(prev[cc] ?? true) }));
  }, []);

  const trailPositions: [number, number][] = useMemo(
    () => trail.map((p) => [p.lat, p.lon] as [number, number]),
    [trail],
  );

  const liveSelectedAc = selectedHex ? aircraft.find((a) => a.hex === selectedHex) : null;
  const lastSelectedAcRef = useRef<AircraftPosition | null>(null);
  if (liveSelectedAc) lastSelectedAcRef.current = liveSelectedAc;
  const selectedAc = selectedHex ? (liveSelectedAc ?? lastSelectedAcRef.current) : null;
  if (!selectedHex) lastSelectedAcRef.current = null;
  const selectedCat = selectedAc ? classifyAircraft(selectedAc) : null;
  const panelOpen = selectedAc != null || showTable || showNews;

  // Playback: interpolate position at playbackTs from trail
  const playbackPosition = useMemo((): { lat: number; lon: number; alt: number | null } | null => {
    if (playbackTs == null || trail.length < 2) return null;
    const idx = trail.findIndex((p) => p.ts >= playbackTs);
    if (idx <= 0) return { lat: trail[0].lat, lon: trail[0].lon, alt: trail[0].alt ?? null };
    if (idx >= trail.length) {
      const last = trail[trail.length - 1];
      return { lat: last.lat, lon: last.lon, alt: last.alt ?? null };
    }
    const a = trail[idx - 1];
    const b = trail[idx];
    const t = (playbackTs - a.ts) / (b.ts - a.ts);
    return {
      lat: a.lat + t * (b.lat - a.lat),
      lon: a.lon + t * (b.lon - a.lon),
      alt: a.alt != null && b.alt != null ? a.alt + t * (b.alt - a.alt) : a.alt ?? b.alt ?? null,
    };
  }, [playbackTs, trail]);

  // Resolve display position for selected aircraft (playback or live)
  const selectedAcDisplayPos: [number, number] | null = playbackPosition
    ? [playbackPosition.lat, playbackPosition.lon]
    : (selectedAc?.lat != null && selectedAc?.lon != null ? [selectedAc.lat, selectedAc.lon] : null);

  // Sync followPos with selected aircraft (live or playback)
  useEffect(() => {
    if (!selectedHex) {
      setFollowPos(null);
      setPlaybackTs(null);
      setPlaybackPlaying(false);
      return;
    }
    if (playbackPosition) {
      if (fitBoundsPositions) setFitBoundsPositions(null);
      setFollowPos([playbackPosition.lat, playbackPosition.lon]);
    } else {
      const ac = aircraft.find((a) => a.hex === selectedHex);
      if (ac?.lat != null && ac?.lon != null) setFollowPos([ac.lat, ac.lon]);
    }
  }, [aircraft, selectedHex, playbackPosition, fitBoundsPositions]);

  useEffect(() => {
    if (!selectedHex) setPlaybackTs(null);
  }, [selectedHex]);

  // Middle East conflict aircraft — scored by involvement level, sorted descending
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

  // Group nearby strikes into clusters so overlapping dots stack neatly
  type StrikeCluster = { lat: number; lon: number; events: StrikeEvent[] };
  const strikeClusters: StrikeCluster[] = useMemo(() => {
    const PROXIMITY = 0.15; // ~15 km at mid-latitudes
    const geoStrikes = strikes.filter((e) => e.latitude != null && e.longitude != null);
    const clusters: StrikeCluster[] = [];

    for (const ev of geoStrikes) {
      const match = clusters.find(
        (c) => Math.abs(c.lat - ev.latitude!) < PROXIMITY && Math.abs(c.lon - ev.longitude!) < PROXIMITY,
      );
      if (match) {
        match.events.push(ev);
      } else {
        clusters.push({ lat: ev.latitude!, lon: ev.longitude!, events: [ev] });
      }
    }
    // Sort events inside each cluster by latest first (smallest hours_ago = most recent)
    for (const c of clusters) c.events.sort((a, b) => (a.hours_ago ?? 999) - (b.hours_ago ?? 999));
    return clusters;
  }, [strikes]);

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
        center={GLOBAL_CENTER}
        zoom={GLOBAL_ZOOM}
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
        <FollowAircraft position={fitBoundsPositions ? null : followPos} />
        <FitRouteBounds positions={fitBoundsPositions} />

        {/* Trail polyline — solid blue for FlightAware full route, dashed for local trail */}
        {trailPositions.length > 1 && (selectedCat || faRoute?.positions?.length) && (
          <Polyline
            positions={trailPositions}
            pathOptions={{
              color: faRoute?.positions?.length ? "#3b82f6" : selectedCat ? CATEGORY_META[selectedCat].color : "#3b82f6",
              weight: faRoute?.positions?.length ? 2.5 : 3,
              opacity: 0.75,
              dashArray: faRoute?.positions?.length ? undefined : "8 4",
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
        {filteredAircraft.map((ac) => {
          const cat = classifyAircraft(ac);
          const isSelected = ac.hex === selectedHex;
          const pos = isSelected && selectedAcDisplayPos
            ? selectedAcDisplayPos
            : (ac.lat != null && ac.lon != null ? [ac.lat, ac.lon] : null);
          if (!acVisible[cat] || !pos) return null;
          const shape = getAircraftShape(ac);
          const label = ac.aircraft_type || ac.flight?.trim() || "";
          const flag = countryFlag(ac.country_code);
          const cName = countryName(ac.country_code);
          return (
            <Marker
              key={ac.hex || `ac-${pos[0]}-${pos[1]}`}
              position={pos}
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

        {/* Strike cluster markers */}
        {showStrikes &&
          strikeClusters.map((cluster, ci) => (
            <Marker
              key={`sc-${ci}`}
              position={[cluster.lat, cluster.lon]}
              icon={strikeClusterIcon(cluster.events, strikesEnriched)}
              eventHandlers={{
                click: (e) => {
                  const marker = e.target;
                  marker.setZIndexOffset(10000);
                  marker.once("popupclose", () => marker.setZIndexOffset(0));
                },
              }}
            >
              <Popup maxWidth={420}>
                <div style={{ fontSize: 13, lineHeight: 1.6, maxHeight: 400, overflowY: "auto" }}>
                  {cluster.events.map((ev, ei) => {
                    const isEnriched = ev.title != null;
                    const dir = ev.attack_direction ?? "unknown";
                    const dirPalette = DIRECTION_COLORS[dir] || DIRECTION_COLORS.unknown;
                    const sevColor = dirPalette.fill;
                    const timeStr = formatHoursAgo(ev.hours_ago);
                    const isNew = ev.hours_ago != null && ev.hours_ago < 0.5;
                    return (
                      <div key={ev.event_id || ei} style={{
                        borderBottom: ei < cluster.events.length - 1 ? "1px solid #2a2a3a" : "none",
                        paddingBottom: 10, marginBottom: 10,
                      }}>
                        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 2, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                          {ev.title || ev.sub_event_type || ev.event_type}
                          {isNew && (
                            <span style={{
                              fontSize: 9, fontWeight: 700, color: "#22c55e",
                              background: "#22c55e22", border: "1px solid #22c55e60", borderRadius: 3,
                              padding: "1px 5px", letterSpacing: 0.5,
                            }}>
                              NEW
                            </span>
                          )}
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12 }}>
                          {dirPalette.label && (
                            <span style={{
                              background: `${dirPalette.fill}20`, border: `1px solid ${dirPalette.fill}60`,
                              borderRadius: 3, padding: "0 6px", fontSize: 10, fontWeight: 700,
                              color: dirPalette.fill, letterSpacing: 0.5,
                            }}>
                              {dirPalette.label}
                            </span>
                          )}
                          {ev.event_date && <span>{ev.event_date}</span>}
                          {ev.country && <span>&middot; {ev.country}</span>}
                          {timeStr && (
                            <span style={{
                              background: "#1a1a2a", border: "1px solid #2a2a3a", borderRadius: 3,
                              padding: "0 5px", fontSize: 10, color: "#8888a0",
                            }}>
                              {timeStr}
                            </span>
                          )}
                        </div>
                        {ev.location && (
                          <div style={{ color: "#b0b0c0", fontSize: 12 }}>
                            {ev.location}{ev.admin1 ? `, ${ev.admin1}` : ""}
                          </div>
                        )}
                        {isEnriched && (
                          <div style={{ display: "flex", gap: 8, marginTop: 6, alignItems: "center", flexWrap: "wrap" }}>
                            {ev.severity != null && (
                              <span style={{
                                background: `${sevColor}20`, border: `1px solid ${sevColor}60`, borderRadius: 4,
                                padding: "1px 8px", fontSize: 11, fontWeight: 700, color: sevColor,
                              }}>
                                Severity {ev.severity}/10
                              </span>
                            )}
                            {ev.event_type && (
                              <span style={{
                                background: "#1a1a2a", border: "1px solid #2a2a3a", borderRadius: 3,
                                padding: "1px 6px", fontSize: 10, color: "#b0b0c0", textTransform: "uppercase",
                              }}>
                                {ev.event_type.replace("_", " ")}
                              </span>
                            )}
                            {ev.confidence != null && (
                              <span style={{ fontSize: 10, color: "#8888a0" }}>
                                {Math.round(ev.confidence * 100)}% confidence
                              </span>
                            )}
                          </div>
                        )}
                        {ev.actor1 && <div style={{ marginTop: 4 }}>Actor: <b>{ev.actor1}</b></div>}
                        {ev.actor2 && <div>Target: <b>{ev.actor2}</b></div>}
                        {ev.fatalities != null && ev.fatalities > 0 && (
                          <div style={{ color: "#ef4444", fontWeight: 600, marginTop: 4 }}>
                            Estimated fatalities: {ev.fatalities}
                          </div>
                        )}
                        {ev.summary && (
                          <div style={{
                            marginTop: 6, color: "#ccc", fontSize: 12, lineHeight: 1.5,
                          }}>
                            {ev.summary}
                          </div>
                        )}
                        {!isEnriched && ev.notes && (
                          <div style={{
                            marginTop: 6, color: "#8888a0", maxHeight: 80,
                            overflowY: "auto", fontSize: 11,
                          }}>
                            {ev.notes}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </Popup>
            </Marker>
          ))}
      </MapContainer>

      {/* ---- Left side panel ---- */}
      <div className={`side-panel ${panelOpen ? "open" : ""}`}>
        {selectedAc && (
          <SidePanel
            aircraft={selectedAc}
            trail={trail}
            onClose={deselectAll}
            playbackTs={playbackTs}
            onPlaybackTsChange={setPlaybackTs}
            playbackPlaying={playbackPlaying}
            onPlaybackPlayingChange={setPlaybackPlaying}
            playbackPosition={playbackPosition}
            faRoute={faRoute}
            faLoading={faLoading}
            onLoadFlightAware={async () => {
              const ident = selectedAc.flight?.trim() || selectedAc.registration?.trim();
              if (!ident) return;
              const cacheKey = `${ident}:${selectedAc.registration || ""}`.toUpperCase();
              const cached = faCacheRef.current.get(cacheKey);
              if (cached) {
                setFaRoute(cached);
                setTrail(cached.positions);
                if (cached.positions.length > 0) {
                  setFitBoundsPositions(cached.positions.map((p) => [p.lat, p.lon] as [number, number]));
                }
                return;
              }
              setFaLoading(true);
              try {
                const d = await fetchFlightAwareRoute(ident, selectedAc.registration || undefined);
                if (d.positions.length > 0) {
                  faCacheRef.current.set(cacheKey, d);
                }
                setFaRoute(d);
                if (d.positions.length > 0) {
                  setTrail(d.positions);
                  setFitBoundsPositions(d.positions.map((p) => [p.lat, p.lon] as [number, number]));
                }
              } catch {
                setFaRoute({ ident, positions: [], total: 0, fa_flight_id: null, origin: null, destination: null, aircraft_type: null, route_distance: null, owner: null, operator: null, operator_icao: null, status: null, blocked: false, available: false, message: "FlightAware unavailable — try again later", departure_time: null, arrival_time: null, estimated_arrival: null, filed_ete: null, progress_percent: null, filed_altitude: null, filed_airspeed: null, filed_route: null, registration: null });
              } finally {
                setFaLoading(false);
              }
            }}
          />
        )}
      </div>

      {/* ---- Bottom panel: Conflict table, News feed, or SITREP ---- */}
      {(showTable || showNews || showSitrep) && !selectedAc && (
        <div
          className="side-panel open"
          style={{ width: showSitrep ? 680 : 780, maxWidth: "62vw" }}
        >
          <div className="side-panel-header">
            <span style={{ fontWeight: 700, fontSize: 16, letterSpacing: 1 }}>
              {showTable ? "MIDDLE EAST CONFLICT — AIRCRAFT" : showSitrep ? "AI SITUATION REPORT" : intelAvailable ? "AI INTELLIGENCE FEED" : "MILITARY NEWS FEED"}
            </span>
            <button
              className="sp-close"
              onClick={() => { setShowTable(false); setShowNews(false); setShowSitrep(false); }}
            >
              ✕
            </button>
          </div>
          <div className="side-panel-body" style={{ padding: "0 14px 16px" }}>
            {showTable && (
              <>
                <div style={{ fontSize: 12, color: "#8888a0", marginBottom: 6, lineHeight: 1.5 }}>
                  Coalition aircraft ranked by how likely they are involved in the Middle East conflict ({conflictAircraft.length} tracked).
                </div>
                <details style={{ marginBottom: 12, fontSize: 11, color: "#6b7280", background: "#0d0d15", border: "1px solid #1a1a2a", borderRadius: 6, padding: 0 }}>
                  <summary style={{ padding: "6px 10px", cursor: "pointer", color: "#8888a0", fontWeight: 600, letterSpacing: 0.5 }}>How the conflict score works</summary>
                  <div style={{ padding: "8px 10px 10px", lineHeight: 1.6, borderTop: "1px solid #1a1a2a" }}>
                    <div style={{ marginBottom: 5 }}>
                      Each aircraft gets a <b style={{ color: "#e4e4ef" }}>conflict score (0–100)</b> calculated from multiple factors:
                    </div>
                    <div style={{ marginBottom: 4 }}>
                      <b style={{ color: "#3b82f6" }}>1. Distance to Iran</b> — Base score from proximity: in conflict zone (80+), near (60–79), far (40–59), very far (20–39). This is the primary factor.
                    </div>
                    <div style={{ marginBottom: 4 }}>
                      <b style={{ color: "#3b82f6" }}>2. Aircraft role multiplier</b> — Surveillance ×1.4, Refueler ×1.6, Bomber ×1.8, Fighter ×1.5, Cargo ×1.0. Higher multipliers for aircraft types more directly involved in combat operations.
                    </div>
                    <div style={{ marginBottom: 4 }}>
                      <b style={{ color: "#3b82f6" }}>3. Flight pattern</b> — Bonus for mission-like behavior: holding patterns, refueling tracks, low altitude in conflict zones.
                    </div>
                    <div style={{ marginBottom: 4 }}>
                      <b style={{ color: "#3b82f6" }}>4. Country of origin</b> — Coalition nations (US, UK, Israel, France) score higher than non-coalition.
                    </div>
                    <div style={{ color: "#555", marginTop: 4, fontSize: 10 }}>
                      Score labels: <span style={{ color: "#ef4444" }}>CRITICAL (80+)</span> · <span style={{ color: "#f59e0b" }}>HIGH (60–79)</span> · <span style={{ color: "#3b82f6" }}>MODERATE (40–59)</span> · <span style={{ color: "#6b7280" }}>LOW (&lt;40)</span>
                    </div>
                  </div>
                </details>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                    <colgroup>
                      <col style={{ width: "7%" }} />
                      <col style={{ width: "4%" }} />
                      <col style={{ width: "12%" }} />
                      <col style={{ width: "7%" }} />
                      <col style={{ width: "14%" }} />
                      <col style={{ width: "36%" }} />
                      <col style={{ width: "10%" }} />
                      <col style={{ width: "10%" }} />
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
            {showNews && intelAvailable && (
              <>
                <div style={{ fontSize: 12, color: "#8888a0", marginBottom: 6, lineHeight: 1.5 }}>
                  {intel.length} articles, latest first. {intelUpdatedAt && <span style={{ color: "#6b7280" }}>• {formatUpdatedAgo(intelUpdatedAt)}</span>}
                </div>
                <details style={{ marginBottom: 12, fontSize: 11, color: "#6b7280", background: "#0d0d15", border: "1px solid #1a1a2a", borderRadius: 6, padding: "0" }}>
                  <summary style={{ padding: "6px 10px", cursor: "pointer", color: "#8888a0", fontWeight: 600, letterSpacing: 0.5 }}>How AI Intel works</summary>
                  <div style={{ padding: "8px 10px 10px", lineHeight: 1.6, borderTop: "1px solid #1a1a2a" }}>
                    <div style={{ marginBottom: 6 }}>
                      <b style={{ color: "#f59e0b" }}>1. Search</b> — Brave Search API finds the latest military news articles across the web (4 targeted queries every 2 hours).
                    </div>
                    <div style={{ marginBottom: 6 }}>
                      <b style={{ color: "#f59e0b" }}>2. Extract</b> — Jina Reader API extracts full article text from each URL, stripping ads and navigation.
                    </div>
                    <div style={{ marginBottom: 6 }}>
                      <b style={{ color: "#f59e0b" }}>3. Analyze</b> — A Databricks-hosted LLM (Claude) reads each article and returns: a relevance score (0–100), category (airstrike, deployment, naval, etc.), entity extraction (countries, weapons, actors), an intelligence summary, and how the article connects to observable aircraft activity.
                    </div>
                    <div style={{ marginBottom: 6 }}>
                      <b style={{ color: "#f59e0b" }}>4. Rank</b> — Articles are ordered by latest first, then relevance: <span style={{ color: "#ef4444" }}>CRITICAL (80+)</span> = active operations, <span style={{ color: "#f59e0b" }}>HIGH (50–79)</span> = force posture changes, <span style={{ color: "#3b82f6" }}>MODERATE (25–49)</span> = political/military implications.
                    </div>
                    <div style={{ color: "#555", marginTop: 4, fontSize: 10 }}>
                      Unlike the <b style={{ color: "#f59e0b" }}>Live Feed</b> in the right panel (raw RSS headlines in real-time), AI Intel deeply analyzes each article for operational significance.
                    </div>
                  </div>
                </details>
                {intel.map((art, i) => {
                  const sColor = art.relevance_score >= 80 ? "#ef4444" : art.relevance_score >= 50 ? "#f59e0b" : "#3b82f6";
                  const sLabel = art.relevance_score >= 80 ? "CRITICAL" : art.relevance_score >= 50 ? "HIGH" : "MODERATE";
                  const isNew = art.hours_ago != null && art.hours_ago < 0.5;
                  return (
                    <div key={i} style={{ borderBottom: "1px solid #1a1a2a", padding: "14px 0" }}>
                      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                        <div style={{
                          minWidth: 46, textAlign: "center", padding: "4px 0",
                          borderRadius: 6, border: `1px solid ${sColor}40`, background: `${sColor}15`,
                        }}>
                          <div style={{ fontSize: 18, fontWeight: 700, color: sColor, fontVariantNumeric: "tabular-nums" }}>
                            {art.relevance_score}
                          </div>
                          <div style={{ fontSize: 9, fontWeight: 600, color: sColor, opacity: 0.8 }}>{sLabel}</div>
                        </div>
                        <div style={{ flex: 1 }}>
                          <a
                            href={art.url || "#"} target="_blank" rel="noopener noreferrer"
                            style={{ color: "#e4e4ef", textDecoration: "none", fontWeight: 600, fontSize: 15, lineHeight: 1.4, display: "block" }}
                            onMouseEnter={(e) => (e.currentTarget.style.color = "#3b82f6")}
                            onMouseLeave={(e) => (e.currentTarget.style.color = "#e4e4ef")}
                          >
                            {art.title}
                            {isNew && (
                              <span style={{
                                marginLeft: 8, fontSize: 9, fontWeight: 700, color: "#22c55e",
                                background: "#22c55e22", border: "1px solid #22c55e60", borderRadius: 3,
                                padding: "1px 5px", verticalAlign: "middle", letterSpacing: 0.5,
                              }}>
                                NEW
                              </span>
                            )}
                          </a>
                          <div style={{ fontSize: 12, color: "#8888a0", marginTop: 4, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                            <span style={{ color: "#f59e0b", fontWeight: 500 }}>{art.source_domain}</span>
                            {art.published && <span>· {art.published}</span>}
                            {art.category && (
                              <span style={{
                                background: "#1a1a2a", border: "1px solid #2a2a3a", borderRadius: 3,
                                padding: "1px 7px", fontSize: 10, color: "#b0b0c0", textTransform: "uppercase", fontWeight: 600,
                              }}>
                                {art.category.replace("_", " ")}
                              </span>
                            )}
                          </div>
                          {art.summary && (
                            <div style={{ fontSize: 13, color: "#ccc", marginTop: 6, lineHeight: 1.6 }}>
                              {art.summary}
                            </div>
                          )}
                          {art.map_connection && (
                            <div style={{
                              fontSize: 12, color: "#3b82f6", marginTop: 6, lineHeight: 1.5,
                              background: "#3b82f610", border: "1px solid #3b82f630", borderRadius: 5, padding: "6px 10px",
                            }}>
                              MAP: {art.map_connection}
                            </div>
                          )}
                          {art.entities && (
                            <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 6 }}>
                              {art.entities.countries?.map((c) => (
                                <span key={c} style={{ fontSize: 11, background: "#1a1a2a", border: "1px solid #2a2a3a", borderRadius: 4, padding: "1px 6px", color: "#999" }}>{c}</span>
                              ))}
                              {art.entities.weapons_platforms?.map((w) => (
                                <span key={w} style={{ fontSize: 11, background: "#f59e0b15", border: "1px solid #f59e0b30", borderRadius: 4, padding: "1px 6px", color: "#f59e0b" }}>{w}</span>
                              ))}
                              {art.entities.actors?.map((a) => (
                                <span key={a} style={{ fontSize: 11, background: "#a855f715", border: "1px solid #a855f730", borderRadius: 4, padding: "1px 6px", color: "#a855f7" }}>{a}</span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </>
            )}
            {showNews && !intelAvailable && (
              <>
                <div style={{ fontSize: 12, color: "#8888a0", marginBottom: 10, lineHeight: 1.5 }}>
                  Military &amp; conflict news from 14+ RSS feeds (incl. Fox, CNN, Guardian, Times) + GDELT DOC ({news.length} articles, ranked by relevance).
                </div>
                {news.length === 0 && (
                  <div style={{ color: "#666", fontSize: 14, padding: 16, textAlign: "center" }}>
                    Loading news...
                  </div>
                )}
                {news.map((item, i) => (
                  <div
                    key={i}
                    style={{
                      borderBottom: "1px solid #1a1a2a",
                      padding: "12px 0",
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
                        fontSize: 15,
                        lineHeight: 1.4,
                        display: "block",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.color = "#3b82f6")}
                      onMouseLeave={(e) => (e.currentTarget.style.color = "#e4e4ef")}
                    >
                      {item.title}
                    </a>
                    <div style={{ fontSize: 12, color: "#8888a0", marginTop: 4, display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ color: "#f59e0b", fontWeight: 500 }}>{item.source}</span>
                      {item.published && (
                        <span>· {item.published.replace(/\+0000|GMT|T|Z/g, " ").trim().slice(0, 16)}</span>
                      )}
                      {item.relevance >= 0.5 && (
                        <span style={{
                          background: item.relevance >= 0.7 ? "#dc262633" : "#f59e0b22",
                          border: `1px solid ${item.relevance >= 0.7 ? "#dc2626" : "#f59e0b"}`,
                          borderRadius: 4, padding: "1px 7px", fontSize: 10, fontWeight: 600,
                          color: item.relevance >= 0.7 ? "#fca5a5" : "#fcd34d",
                        }}>
                          {item.relevance >= 0.7 ? "HIGH" : "MED"}
                        </span>
                      )}
                    </div>
                    {item.summary && (
                      <div style={{ fontSize: 13, color: "#888", marginTop: 5, lineHeight: 1.5 }}>
                        {item.summary.slice(0, 200)}...
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}
            {showSitrep && (
              <>
                {!sitrep ? (
                  <div style={{ color: "#8888a0", fontSize: 14, padding: 24, textAlign: "center" }}>
                    Generating situation report... This runs every 2 hours.
                    <br />
                    <span style={{ fontSize: 12, color: "#555" }}>The first report is generated ~3 minutes after server start.</span>
                  </div>
                ) : (
                  <div style={{ lineHeight: 1.6 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                      <span style={{
                        padding: "4px 14px",
                        borderRadius: 6,
                        fontWeight: 800,
                        fontSize: 14,
                        letterSpacing: 1,
                        color: "#fff",
                        background:
                          sitrep.threat_level === "CRITICAL" ? "#dc2626" :
                          sitrep.threat_level === "HIGH" ? "#ea580c" :
                          sitrep.threat_level === "MODERATE" ? "#ca8a04" : "#16a34a",
                      }}>
                        {sitrep.threat_level}
                      </span>
                      <span style={{ fontSize: 12, color: "#6b7280", display: "inline-flex", alignItems: "center", gap: 8 }}>
                        {sitrep.generated_at}
                        {sitrep.generated_at_ts != null && (Date.now() / 1000 - sitrep.generated_at_ts) < 1800 && (
                          <span style={{
                            fontSize: 9, fontWeight: 700, color: "#22c55e",
                            background: "#22c55e22", border: "1px solid #22c55e60", borderRadius: 3,
                            padding: "1px 5px", letterSpacing: 0.5,
                          }}>
                            NEW
                          </span>
                        )}
                        {sitrepUpdatedAt && <> · {formatUpdatedAgo(sitrepUpdatedAt)}</>}
                      </span>
                    </div>

                    <details style={{ marginBottom: 14, fontSize: 11, color: "#6b7280", background: "#0d0d15", border: "1px solid #1a1a2a", borderRadius: 6, padding: 0 }}>
                      <summary style={{ padding: "6px 10px", cursor: "pointer", color: "#8888a0", fontWeight: 600, letterSpacing: 0.5 }}>How the SITREP works</summary>
                      <div style={{ padding: "8px 10px 10px", lineHeight: 1.6, borderTop: "1px solid #1a1a2a" }}>
                        <div style={{ marginBottom: 5 }}>
                          The SITREP is generated every 2 hours by a Databricks-hosted LLM that synthesizes <b style={{ color: "#c4b5fd" }}>all three data streams</b> into one intelligence briefing:
                        </div>
                        <div style={{ marginBottom: 4 }}>
                          <b style={{ color: "#3b82f6" }}>Aircraft</b> — All currently tracked military aircraft (type, position, country, altitude, speed) are sent to the LLM without pre-filtering, letting the AI assess which movements are operationally significant.
                        </div>
                        <div style={{ marginBottom: 4 }}>
                          <b style={{ color: "#ef4444" }}>Strikes</b> — The latest AI-verified conflict events from the last 24 hours, with severity and direction data.
                        </div>
                        <div style={{ marginBottom: 4 }}>
                          <b style={{ color: "#f59e0b" }}>News</b> — Top AI Intel articles with relevance scores and summaries.
                        </div>
                        <div style={{ color: "#555", marginTop: 4, fontSize: 10 }}>
                          The LLM cross-references these streams to identify patterns, connections between aircraft activity and strikes, and produces a threat level assessment with forward-looking analysis.
                        </div>
                      </div>
                    </details>

                    <div style={{ fontSize: 15, color: "#e4e4ef", fontWeight: 600, marginBottom: 16, lineHeight: 1.6 }}>
                      {sitrep.executive_summary}
                    </div>

                    {([
                      { label: "AIRCRAFT SITUATION", text: sitrep.aircraft_situation, color: "#3b82f6" },
                      { label: "CONFLICT SITUATION", text: sitrep.conflict_situation, color: "#ef4444" },
                      { label: "KEY DEVELOPMENTS", text: sitrep.key_developments, color: "#f59e0b" },
                      { label: "ASSESSMENT", text: sitrep.assessment, color: "#8b5cf6" },
                      ...(sitrep.connections ? [{ label: "CONNECTIONS", text: sitrep.connections, color: "#06b6d4" }] : []),
                    ] as { label: string; text: string; color: string }[]).map((section) => (
                      <div key={section.label} style={{ marginBottom: 14 }}>
                        <div style={{
                          fontSize: 11,
                          fontWeight: 700,
                          letterSpacing: 1.5,
                          color: section.color,
                          marginBottom: 4,
                          textTransform: "uppercase" as const,
                        }}>
                          {section.label}
                        </div>
                        <div style={{ fontSize: 13, color: "#c4c4d4", whiteSpace: "pre-wrap" }}>
                          {section.text}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
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
          flexWrap: "wrap",
          maxWidth: "calc(100vw - 48px)",
          transition: "left 0.3s cubic-bezier(0.4,0,0.2,1)",
        }}
      >
        <div style={{ ...panelStyle, flexWrap: "wrap", rowGap: 8, alignItems: "center" }}>
          <span style={{ fontWeight: 700, letterSpacing: 1 }}>MILTRACK</span>
          <D />
          <div style={{ position: "relative" }}>
            <input
              type="text"
              placeholder="Find flights, hex, registration..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setSearchFocused(true)}
              onBlur={() => setTimeout(() => setSearchFocused(false), 150)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && searchMatches.length === 1) {
                  selectAircraft(searchMatches[0].hex);
                  setSearchQuery("");
                  setSearchFocused(false);
                }
              }}
              style={{
                width: 200,
                padding: "6px 10px",
                background: "#0d0d15",
                border: "1px solid #2a2a3a",
                borderRadius: 6,
                color: "#e4e4ef",
                fontSize: 12,
                outline: "none",
              }}
            />
            {searchFocused && searchQuery.trim() && searchMatches.length > 0 && (
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  left: 0,
                  marginTop: 4,
                  minWidth: 260,
                  maxHeight: 240,
                  overflowY: "auto",
                  background: "#0d0d15",
                  border: "1px solid #2a2a3a",
                  borderRadius: 8,
                  boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
                  zIndex: 2000,
                }}
              >
                {searchMatches.slice(0, 10).map((ac) => (
                  <button
                    key={ac.hex}
                    type="button"
                    onClick={() => {
                      selectAircraft(ac.hex);
                      setSearchQuery("");
                      setSearchFocused(false);
                    }}
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      textAlign: "left",
                      background: "none",
                      border: "none",
                      borderBottom: "1px solid #1a1a2a",
                      color: "#e4e4ef",
                      cursor: "pointer",
                      fontSize: 12,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <span>{ac.flight?.trim() || "—"} · {ac.registration || ac.hex || "—"}</span>
                    <span style={{ color: "#6b7280", fontSize: 11 }}>{ac.aircraft_type || ""}</span>
                  </button>
                ))}
                {searchMatches.length > 10 && (
                  <div style={{ padding: "6px 12px", fontSize: 10, color: "#6b7280" }}>
                    +{searchMatches.length - 10} more — refine search
                  </div>
                )}
              </div>
            )}
          </div>
          <D />
          <span>
            <b style={mono}>{filteredAircraft.length}</b> aircraft
          </span>
          <D />
          <span>
            <b style={{ ...mono, color: "#ef4444" }}>{strikes.length}</b> events
          </span>
          <D />
          <span>
            <b style={{ ...mono, color: "#f59e0b" }}>{bases.length}</b> bases
          </span>
          {deathToll && deathToll.by_country.length > 0 && (() => {
            const total = deathToll.by_country.reduce((s, r) => s + (r.ucdp_best ?? r.gdelt_total ?? 0), 0);
            return (
              <>
                <D />
                <button
                  type="button"
                  onClick={() => setShowDeathTollModal(true)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "4px 10px",
                    background: "#1a1a2a",
                    borderRadius: 6,
                    border: "1px solid #2a2a3a",
                    cursor: "pointer",
                    color: "inherit",
                    font: "inherit",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "#252535";
                    e.currentTarget.style.borderColor = "#dc262640";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "#1a1a2a";
                    e.currentTarget.style.borderColor = "#2a2a3a";
                  }}
                >
                  <b style={{ ...mono, color: "#dc2626", fontSize: 12 }}>{total.toLocaleString()}</b>
                  <span style={{ color: "#9ca3af", fontSize: 11 }}>casualties</span>
                  <span style={{ color: "#6b7280", fontSize: 9 }}>({deathTollPreset === "30d" ? "30d" : deathTollPreset === "90d" ? "90d" : deathTollPreset === "ytd" ? "YTD" : deathTollPreset === "2024" ? "2024" : "all"})</span>
                  <span style={{ fontSize: 10, color: "#6b7280", marginLeft: 2 }}>▼</span>
                </button>
              </>
            );
          })()}
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
          <button
            onClick={() => { setShowTable(!showTable); setShowNews(false); setShowSitrep(false); }}
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
            onClick={() => { setShowNews(!showNews); setShowTable(false); setShowSitrep(false); }}
            style={{
              ...toggleBtnStyle,
              background: showNews ? "#f59e0b22" : "#1a1a2a",
              borderColor: showNews ? "#f59e0b" : "#3a3a5a",
              color: showNews ? "#fcd34d" : "#e4e4ef",
            }}
          >
            {intelAvailable ? "AI INTEL" : "NEWS"} {intelAvailable ? `(${intel.length})` : news.length > 0 ? `(${news.length})` : ""}
          </button>
          <D />
          <button
            onClick={() => { setShowSitrep(!showSitrep); setShowTable(false); setShowNews(false); }}
            style={{
              ...toggleBtnStyle,
              background: showSitrep ? "#8b5cf622" : "#1a1a2a",
              borderColor: showSitrep ? "#8b5cf6" : "#3a3a5a",
              color: showSitrep ? "#c4b5fd" : "#e4e4ef",
            }}
          >
            SITREP {sitrep ? `(${sitrep.threat_level})` : ""}
          </button>
        </div>
      </div>

      {/* ---- Death toll modal (click to open) ---- */}
      {showDeathTollModal && deathToll && deathToll.by_country.length > 0 && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 2000,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "rgba(0,0,0,0.6)",
            backdropFilter: "blur(4px)",
          }}
          onClick={() => setShowDeathTollModal(false)}
        >
          <div
            style={{
              background: "#0d0d15",
              border: "1px solid #2a2a3a",
              borderRadius: 12,
              padding: 24,
              maxWidth: 460,
              width: "90vw",
              maxHeight: "80vh",
              overflowY: "auto",
              boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: "#e4e4ef" }}>Conflict casualties</h3>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {expandedDeathTollCountry && (
                  <button type="button" onClick={() => setExpandedDeathTollCountry(null)} style={{ background: "none", border: "none", color: "#8888a0", cursor: "pointer", fontSize: 11 }}>Collapse</button>
                )}
                <button
                  type="button"
                  onClick={() => setShowDeathTollModal(false)}
                  style={{
                    background: "none",
                    border: "none",
                    color: "#8888a0",
                    cursor: "pointer",
                    fontSize: 20,
                    padding: 4,
                    lineHeight: 1,
                  }}
                >
                  ×
                </button>
              </div>
            </div>

            {/* Time period selector */}
            <div style={{ display: "flex", gap: 4, marginBottom: 12, flexWrap: "wrap" }}>
              {([
                ["30d", "30 days"],
                ["90d", "90 days"],
                ["ytd", "YTD"],
                ["2024", "2024"],
                ["all", "Since Oct 7 '23"],
              ] as [DeathTollPreset, string][]).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => {
                    setDeathTollPreset(key);
                    loadDeathToll(key);
                  }}
                  style={{
                    padding: "4px 10px",
                    fontSize: 11,
                    fontWeight: deathTollPreset === key ? 600 : 400,
                    borderRadius: 6,
                    border: `1px solid ${deathTollPreset === key ? "#3b82f6" : "#2a2a3a"}`,
                    background: deathTollPreset === key ? "#1e3a5f" : "#1a1a2a",
                    color: deathTollPreset === key ? "#93c5fd" : "#9ca3af",
                    cursor: "pointer",
                    transition: "all 0.15s",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>

            <div style={{ fontSize: 11, color: "#6b7280", marginBottom: 14, lineHeight: 1.4 }}>
              {deathToll.ucdp_available ? <><a href="https://ucdp.uu.se/" target="_blank" rel="noopener noreferrer" style={{ color: "#3b82f6" }}>UCDP</a> (Uppsala Conflict Data Program) + <a href="https://www.gdeltproject.org/" target="_blank" rel="noopener noreferrer" style={{ color: "#3b82f6" }}>GDELT</a> (Global Database of Events, Language, and Tone)</> : <><a href="https://www.gdeltproject.org/" target="_blank" rel="noopener noreferrer" style={{ color: "#3b82f6" }}>GDELT</a> (Global Database of Events, Language, and Tone — AI-enriched)</>} · {deathToll.period}
              {deathTollLoading && <span style={{ marginLeft: 8, color: "#3b82f6" }}>Loading...</span>}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8, opacity: deathTollLoading ? 0.5 : 1, transition: "opacity 0.2s" }}>
              {deathToll.by_country.map((row) => {
                const isExpanded = expandedDeathTollCountry === row.country;
                const hasUcdp = row.ucdp_best != null && row.ucdp_best > 0;
                const hasGdelt = row.gdelt_total != null && row.gdelt_total > 0;
                return (
                  <div
                    key={row.country}
                    style={{
                      background: "#1a1a2a",
                      borderRadius: 8,
                      border: "1px solid #2a2a3a",
                      overflow: "hidden",
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => setExpandedDeathTollCountry(isExpanded ? null : row.country)}
                      style={{
                        width: "100%",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "10px 12px",
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        color: "inherit",
                        font: "inherit",
                        textAlign: "left",
                      }}
                    >
                      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        <span style={{ color: "#e4e4ef", fontWeight: 600, fontSize: 14 }}>{row.country}</span>
                        {row.conflict_context && (
                          <span style={{ fontSize: 10, color: "#6b7280", fontStyle: "italic" }}>{row.conflict_context}</span>
                        )}
                      </div>
                      <span style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                        {hasUcdp && (
                          <span style={{ color: "#22c55e", fontWeight: 700, fontSize: 15, fontVariantNumeric: "tabular-nums" }}>
                            {row.ucdp_best!.toLocaleString()}
                            {row.ucdp_low != null && row.ucdp_high != null && row.ucdp_low !== row.ucdp_high && (
                              <span style={{ fontSize: 11, color: "#6b7280", fontWeight: 400, marginLeft: 6 }}>
                                ({row.ucdp_low.toLocaleString()}–{row.ucdp_high.toLocaleString()})
                              </span>
                            )}
                          </span>
                        )}
                        {hasGdelt && (
                          <span style={{
                            background: "#0d0d15",
                            border: "1px solid #2a2a3a",
                            borderRadius: 4,
                            padding: "2px 8px",
                            fontSize: 11,
                            color: "#9ca3af",
                            fontVariantNumeric: "tabular-nums",
                          }}>
                            GDELT: {row.gdelt_total}
                          </span>
                        )}
                        <span style={{ fontSize: 10, color: "#6b7280" }}>{isExpanded ? "▲" : "▼"}</span>
                      </span>
                    </button>
                    {isExpanded && (
                      <div style={{ padding: "8px 12px 12px", borderTop: "1px solid #2a2a3a", background: "#0d0d15", fontSize: 11, color: "#9ca3af", lineHeight: 1.6 }}>
                        {hasUcdp && (
                          <div style={{ marginBottom: 8 }}>
                            <div style={{ fontWeight: 600, color: "#22c55e", marginBottom: 4 }}>UCDP (verified)</div>
                            <div style={{ marginBottom: 4 }}>
                              Uppsala Conflict Data Program — peer-reviewed, verified battle-related deaths. Data is cross-checked against multiple sources.
                            </div>
                            <a href="https://ucdp.uu.se/" target="_blank" rel="noopener noreferrer" style={{ color: "#3b82f6", marginRight: 12 }}>ucdp.uu.se</a>
                          </div>
                        )}
                        {hasGdelt && (
                          <div>
                            <div style={{ fontWeight: 600, color: "#f59e0b", marginBottom: 4 }}>GDELT (AI-enriched)</div>
                            <div style={{ marginBottom: 4 }}>
                              Fatalities inferred by LLM from GDELT conflict events. Unverified — estimates from news coding.
                            </div>
                            <a href="https://www.gdeltproject.org/" target="_blank" rel="noopener noreferrer" style={{ color: "#3b82f6" }}>gdeltproject.org</a>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div style={{ marginTop: 16, fontSize: 10, color: "#6b7280", lineHeight: 1.5 }}>
              Deaths attributed by location of the event, not nationality of victims. Click a country for source details.
            </div>
            <div style={{ marginTop: 12, padding: 10, background: "#0d0d15", borderRadius: 8, border: "1px solid #2a2a3a", fontSize: 10, color: "#9ca3af", lineHeight: 1.6 }}>
              <div style={{ fontWeight: 600, color: "#e4e4ef", marginBottom: 6 }}>Important context</div>
              These figures cover multiple overlapping conflicts in the Middle East region and should not be attributed to a single conflict. Use the time filter above to narrow the period.
              <div style={{ marginTop: 6, color: "#f59e0b" }}>
                GDELT numbers are AI-inferred and unverified — treat as rough indicators, not official counts. Always cross-reference with official sources.
              </div>
              {!deathToll.ucdp_available && (
                <div style={{ marginTop: 6 }}>
                  Request UCDP API access from <a href="mailto:mertcan.yilmaz@pcr.uu.se" style={{ color: "#3b82f6" }}>UCDP</a> for verified counts.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

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
      <div style={{ position: "absolute", top: 12, right: 12, zIndex: 1000, width: 320 }}>
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

          <details
            open={countryFilterOpen}
            onToggle={(e) => setCountryFilterOpen((e.target as HTMLDetailsElement).open)}
            style={{ marginTop: 8, borderTop: "1px solid #2a2a3a", paddingTop: 8 }}
          >
            <summary style={{ cursor: "pointer", fontSize: 10, fontWeight: 600, letterSpacing: 1.5, color: "#8888a0", textTransform: "uppercase" }}>
              Country filter
            </summary>
            <div style={{ marginTop: 6, maxHeight: 160, overflowY: "auto" }}>
              {(() => {
                const byCountry = new Map<string, number>();
                for (const ac of aircraft) {
                  if (ac.country_code) byCountry.set(ac.country_code, (byCountry.get(ac.country_code) ?? 0) + 1);
                }
                const countries = [...byCountry.entries()].sort((a, b) => b[1] - a[1]);
                if (countries.length === 0) return <span style={{ fontSize: 11, color: "#6b7280" }}>No country data</span>;
                return countries.map(([cc, count]) => (
                  <Row key={cc}>
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={{ fontSize: 14 }}>{countryFlag(cc)}</span>
                      <span style={{ fontSize: 11 }}>{countryName(cc) || cc}</span>
                    </span>
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <Badge>{count}</Badge>
                      <input
                        type="checkbox"
                        checked={countryFilter[cc] !== false}
                        onChange={() => toggleCountry(cc)}
                        style={{ accentColor: "#f59e0b" }}
                      />
                    </span>
                  </Row>
                ));
              })()}
            </div>
          </details>

          <div style={{ borderTop: "1px solid #2a2a3a", marginTop: 4, paddingTop: 8 }}>
            <div style={sectionTitle}>
              Conflict Events {strikesEnriched ? "(AI verified)" : "(raw — AI processing...)"}
              {strikesUpdatedAt && <span style={{ fontWeight: 400, fontSize: 10, color: "#6b7280", marginLeft: 8 }}>{formatUpdatedAgo(strikesUpdatedAt)}</span>}
            </div>
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
            <div style={{ fontSize: 10, color: "#666", marginTop: 8, lineHeight: 1.4 }}>
              {strikes.length} AI-reviewed incidents · last 90 days · Source: <a href="https://www.gdeltproject.org/" target="_blank" rel="noopener noreferrer" style={{ color: "#3b82f6" }}>GDELT</a> (Global Database of Events, Language, and Tone)
              <br />
              <span style={{ color: "#b8860b" }}>AI can make mistakes — cross-reference with official sources.</span>
            </div>
            <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 3 }}>
              {([
                ["to_iran", "#3b82f6", "Strikes on Iran"],
                ["from_iran", "#ef4444", "Iranian attacks"],
                ["internal", "#f59e0b", "Internal unrest"],
                ["other", "#8b5cf6", "Other regional"],
              ] as const).map(([, color, label]) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, display: "inline-block" }} />
                  <span style={{ fontSize: 10, color: "#b0b0c0" }}>{label}</span>
                </div>
              ))}
            </div>
            <details style={{ marginTop: 8, fontSize: 10, color: "#6b7280", background: "#0d0d15", border: "1px solid #1a1a2a", borderRadius: 5, padding: 0 }}>
              <summary style={{ padding: "5px 8px", cursor: "pointer", color: "#8888a0", fontWeight: 600, fontSize: 10, letterSpacing: 0.5 }}>How conflict events work</summary>
              <div style={{ padding: "6px 8px 8px", lineHeight: 1.6, borderTop: "1px solid #1a1a2a" }}>
                <div style={{ marginBottom: 4 }}>
                  <b style={{ color: "#ef4444" }}>1. Ingest</b> — Raw conflict events are pulled from the GDELT Project, which machine-codes news articles into structured event data every 15 minutes.
                </div>
                <div style={{ marginBottom: 4 }}>
                  <b style={{ color: "#ef4444" }}>2. Deduplicate</b> — GDELT often codes the same real-world incident multiple times from different articles. A Databricks-hosted LLM (Claude) groups duplicates that share the same date, location, and actors into single verified incidents.
                </div>
                <div style={{ marginBottom: 4 }}>
                  <b style={{ color: "#ef4444" }}>3. Enrich</b> — The LLM assigns each incident: a human-readable title, severity (1–10), confidence (0–1), attack direction (to/from Iran), and a summary. Events below 0.5 confidence are filtered out as noise.
                </div>
                <div style={{ marginBottom: 4 }}>
                  <b style={{ color: "#ef4444" }}>4. Visualize</b> — Dot size = severity, color = direction, opacity = confidence + recency. Nearby events cluster into grouped markers.
                </div>
              </div>
            </details>

            {news.length > 0 && (
              <div style={{ marginTop: 12, borderTop: "1px solid #2a2a3a", paddingTop: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1.5, color: "#f59e0b", marginBottom: 3, textTransform: "uppercase" as const }}>
                  Live Feed
                </div>
                <div style={{ fontSize: 10, color: "#6b7280", marginBottom: 8 }}>
                  Real-time RSS from 10+ sources · newest first · no AI processing (zero cost)
                </div>
                <div style={{ maxHeight: 480, overflowY: "auto", paddingRight: 4 }}>
                  {[...news]
                    .sort((a, b) => (b.published || "").localeCompare(a.published || ""))
                    .slice(0, 25)
                    .map((item, i) => {
                      const ago = formatPublishedAgo(item.published);
                      const domain = item.link ? new URL(item.link).hostname.replace("www.", "") : "";
                      const favicon = domain ? `https://www.google.com/s2/favicons?domain=${domain}&sz=20` : "";
                      return (
                        <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 10, paddingBottom: 10, borderBottom: "1px solid #1a1a2a" }}>
                          {favicon && <img src={favicon} alt="" width={18} height={18} style={{ flexShrink: 0, marginTop: 2, borderRadius: 3, opacity: 0.9 }} />}
                          <div style={{ minWidth: 0, flex: 1 }}>
                            {item.link ? (
                              <a
                                href={item.link}
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{ fontSize: 14, color: "#e4e4ef", lineHeight: 1.4, display: "block", textDecoration: "none", fontWeight: 500, wordBreak: "break-word" }}
                                onMouseEnter={(e) => (e.currentTarget.style.color = "#f59e0b")}
                                onMouseLeave={(e) => (e.currentTarget.style.color = "#e4e4ef")}
                              >
                                {item.title}
                              </a>
                            ) : (
                              <div style={{ fontSize: 14, color: "#e4e4ef", lineHeight: 1.4, fontWeight: 500, wordBreak: "break-word" }}>
                                {item.title}
                              </div>
                            )}
                            <div style={{ fontSize: 11, color: "#9ca3af", marginTop: 4, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                              {ago && <span style={{ color: "#f59e0b", fontWeight: 600 }}>{ago}</span>}
                              {ago && item.source ? <span style={{ color: "#6b7280" }}>·</span> : null}
                              {item.source && <span style={{ fontWeight: 500, color: "#b0b0c0" }}>{item.source}</span>}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            )}
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
            Events: GDELT → AI &nbsp;|&nbsp; Bases: OpenStreetMap
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
  overflow: "hidden",
  textOverflow: "ellipsis",
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
