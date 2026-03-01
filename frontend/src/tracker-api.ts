// --- Types ---

export interface AircraftPosition {
  hex: string | null;
  flight: string | null;
  registration: string | null;
  aircraft_type: string | null;
  description: string | null;
  country_code: string | null;
  lat: number | null;
  lon: number | null;
  alt_baro: number | string | null;
  alt_geom: number | null;
  ground_speed: number | null;
  track: number | null;
  squawk: string | null;
  category: string | null;
  nav_heading: number | null;
  seen: number | null;
  rssi: number | null;
  emergency: string | null;
  db_flags: number | null;
}

export interface AircraftResponse {
  aircraft: AircraftPosition[];
  total: number;
  cached: boolean;
}

export interface StrikeEvent {
  event_id: string | null;
  event_date: string | null;
  event_type: string | null;
  sub_event_type: string | null;
  actor1: string | null;
  actor2: string | null;
  country: string | null;
  admin1: string | null;
  admin2: string | null;
  location: string | null;
  latitude: number | null;
  longitude: number | null;
  fatalities: number | null;
  notes: string | null;
  source: string | null;
}

export interface StrikesResponse {
  events: StrikeEvent[];
  total: number;
  cached: boolean;
  hint: string | null;
}

export interface AircraftInfo {
  type_code: string;
  name: string | null;
  description: string | null;
  image_url: string | null;
  wiki_url: string | null;
}

export interface TrailPoint {
  ts: number;
  lat: number;
  lon: number;
  alt: number | null;
}

export interface TrailResponse {
  hex: string;
  points: TrailPoint[];
  total: number;
}

export interface MilitaryBase {
  id: number;
  name: string | null;
  lat: number;
  lon: number;
  base_type: "airbase" | "naval_base" | "base";
  operator: string | null;
  country: string | null;
}

export interface BasesResponse {
  bases: MilitaryBase[];
  total: number;
  cached: boolean;
}

export interface NewsItem {
  title: string;
  link: string | null;
  published: string | null;
  source: string | null;
  summary: string | null;
}

export interface NewsResponse {
  items: NewsItem[];
  total: number;
  cached: boolean;
}

// --- Country code → flag emoji + name ---

const REGIONAL_A = 0x1F1E6;
export function countryFlag(cc: string | null): string {
  if (!cc || cc.length !== 2) return "";
  const a = cc.charCodeAt(0) - 65 + REGIONAL_A;
  const b = cc.charCodeAt(1) - 65 + REGIONAL_A;
  return String.fromCodePoint(a, b);
}

const COUNTRY_NAMES: Record<string, string> = {
  US: "United States", IL: "Israel", GB: "United Kingdom", FR: "France",
  DE: "Germany", IT: "Italy", ES: "Spain", NL: "Netherlands", BE: "Belgium",
  DK: "Denmark", NO: "Norway", SE: "Sweden", FI: "Finland", PL: "Poland",
  GR: "Greece", PT: "Portugal", CZ: "Czech Rep.", RO: "Romania", HU: "Hungary",
  BG: "Bulgaria", AT: "Austria", CH: "Switzerland", TR: "Turkey",
  CA: "Canada", AU: "Australia", NZ: "New Zealand", JP: "Japan", KR: "South Korea",
  IN: "India", PK: "Pakistan", CN: "China", TW: "Taiwan", SG: "Singapore",
  MY: "Malaysia", ID: "Indonesia", TH: "Thailand", PH: "Philippines", VN: "Vietnam",
  SA: "Saudi Arabia", AE: "UAE", QA: "Qatar", BH: "Bahrain", KW: "Kuwait",
  OM: "Oman", JO: "Jordan", LB: "Lebanon", IQ: "Iraq", IR: "Iran", SY: "Syria",
  EG: "Egypt", LY: "Libya", BR: "Brazil", AR: "Argentina", RS: "Serbia",
  BD: "Bangladesh", LK: "Sri Lanka", AF: "Afghanistan", KP: "North Korea", NP: "Nepal",
};

export function countryName(cc: string | null): string {
  if (!cc) return "";
  return COUNTRY_NAMES[cc] || cc;
}

// --- Fetch ---

const _infoCache = new Map<string, AircraftInfo>();

export async function fetchAircraftInfo(typeCode: string): Promise<AircraftInfo | null> {
  if (!typeCode) return null;
  const key = typeCode.toUpperCase();
  if (_infoCache.has(key)) return _infoCache.get(key)!;
  try {
    const res = await fetch(`/api/aircraft/info/${encodeURIComponent(key)}`);
    if (!res.ok) return null;
    const info: AircraftInfo = await res.json();
    _infoCache.set(key, info);
    return info;
  } catch {
    return null;
  }
}

export async function fetchAircraft(globalView = false): Promise<AircraftResponse> {
  const url = globalView ? "/api/aircraft?global_view=true" : "/api/aircraft";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Aircraft fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchTrail(hex: string): Promise<TrailResponse> {
  const res = await fetch(`/api/aircraft/trail/${encodeURIComponent(hex)}`);
  if (!res.ok) throw new Error(`Trail fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchStrikes(params?: { days?: number }): Promise<StrikesResponse> {
  const sp = new URLSearchParams();
  if (params?.days != null) sp.set("days", String(params.days));
  const qs = sp.toString();
  const res = await fetch(qs ? `/api/strikes?${qs}` : "/api/strikes");
  if (!res.ok) throw new Error(`Strikes fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchBases(globalView = false): Promise<BasesResponse> {
  const url = globalView ? "/api/bases?global_view=true" : "/api/bases";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Bases fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchNews(limit = 50): Promise<NewsResponse> {
  const res = await fetch(`/api/news?limit=${limit}`);
  if (!res.ok) throw new Error(`News fetch failed: ${res.status}`);
  return res.json();
}

// --- Aircraft classification ---

export type MilCategory = "tanker" | "awacs" | "transport" | "recon" | "other";

const TANKER_TYPES = new Set(["K35R", "KC46", "KC10", "K35T", "KC30", "MRTT", "K35E", "KC2T"]);
const AWACS_TYPES = new Set(["E3TF", "E3CF", "E737", "E767", "E2C", "E2D", "E6B"]);
const TRANSPORT_TYPES = new Set(["C17", "C5M", "C5", "C130", "C30J", "C160", "A400", "C2"]);
const RECON_TYPES = new Set(["RC135", "R135", "EP3", "P8", "RQ4", "MQ9", "MQ4C", "U2"]);

export function classifyAircraft(ac: AircraftPosition): MilCategory {
  const t = ac.aircraft_type?.toUpperCase().replace(/-/g, "") || "";
  if (TANKER_TYPES.has(t) || t.startsWith("KC") || t.startsWith("K35")) return "tanker";
  if (AWACS_TYPES.has(t) || t.startsWith("E3") || t.startsWith("E6")) return "awacs";
  if (TRANSPORT_TYPES.has(t) || t.startsWith("C17") || t.startsWith("C5") || t.startsWith("C130")) return "transport";
  if (RECON_TYPES.has(t) || t.startsWith("RC") || t.startsWith("RQ") || t.startsWith("MQ") || t.startsWith("P8") || t.startsWith("EP")) return "recon";
  const desc = (ac.description || "").toLowerCase();
  if (desc.includes("tanker") || desc.includes("refuel")) return "tanker";
  if (desc.includes("awacs") || desc.includes("sentry") || desc.includes("surveillance")) return "awacs";
  if (desc.includes("transport") || desc.includes("cargo") || desc.includes("globemaster") || desc.includes("galaxy")) return "transport";
  if (desc.includes("recon") || desc.includes("poseidon") || desc.includes("rivet") || desc.includes("hawk")) return "recon";
  return "other";
}

export const CATEGORY_META: Record<MilCategory, { color: string; label: string; desc: string }> = {
  tanker: { color: "#f59e0b", label: "Aerial Refueler", desc: "Refuels other aircraft mid-flight" },
  awacs: { color: "#3b82f6", label: "Radar / Command", desc: "Airborne radar or command post" },
  transport: { color: "#10b981", label: "Cargo / Transport", desc: "Moves troops, equipment, or supplies" },
  recon: { color: "#a855f7", label: "Surveillance", desc: "Gathers intelligence or patrols" },
  other: { color: "#6b7280", label: "Military", desc: "Other military aircraft" },
};

export function getAircraftLabel(ac: AircraftPosition): string {
  return [ac.flight?.trim(), ac.registration, ac.aircraft_type].filter(Boolean).join(" · ") || "Unknown";
}

// --- Conflict involvement score (0–100) ---
// Proximity is the dominant factor. Role acts as a multiplier.

const IRAN_CENTER = { lat: 32.4, lon: 53.7 };

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

const ROLE_MULTIPLIER: Record<MilCategory, number> = {
  tanker: 1.6,
  awacs: 1.6,
  recon: 1.4,
  transport: 1.0,
  other: 0.8,
};

const ROLE_LABELS: Record<MilCategory, string> = {
  tanker: "refueler",
  awacs: "radar/command",
  recon: "surveillance",
  transport: "cargo",
  other: "military",
};

export interface ScoreResult {
  score: number;
  reasons: string[];
}

export function conflictScore(ac: AircraftPosition): ScoreResult {
  const cat = classifyAircraft(ac);
  const reasons: string[] = [];
  let raw = 0;

  // 1. Proximity to Iran (0–45) — dominant factor
  let proxPts = 0;
  let distKm = 99999;
  if (ac.lat != null && ac.lon != null) {
    distKm = haversineKm(ac.lat, ac.lon, IRAN_CENTER.lat, IRAN_CENTER.lon);
    if (distKm < 300) { proxPts = 45; reasons.push(`${Math.round(distKm)}km from Iran`); }
    else if (distKm < 600) { proxPts = 38; reasons.push(`${Math.round(distKm)}km from Iran`); }
    else if (distKm < 1000) { proxPts = 30; reasons.push(`${Math.round(distKm)}km from Iran`); }
    else if (distKm < 2000) { proxPts = 18; reasons.push(`~${Math.round(distKm / 100) * 100}km away`); }
    else if (distKm < 4000) { proxPts = 8; reasons.push("far from conflict zone"); }
    else { proxPts = 2; reasons.push("very far from conflict"); }
  }
  raw += proxPts;

  // 2. Country relevance (0–10)
  if (ac.country_code === "IL") { raw += 10; reasons.push("Israeli"); }
  else if (ac.country_code === "US") { raw += 5; reasons.push("US"); }
  else if (ac.country_code === "SA" || ac.country_code === "AE") { raw += 6; reasons.push("Gulf state"); }
  else if (ac.country_code === "GB" || ac.country_code === "FR") { raw += 3; reasons.push("NATO ally"); }

  // 3. Mission-like behavior (0–10)
  const alt = typeof ac.alt_baro === "number" ? ac.alt_baro : null;
  if (ac.ground_speed != null && ac.ground_speed >= 180 && ac.ground_speed <= 380 && alt != null && alt >= 15000) {
    raw += 10;
    reasons.push("mission-like flight pattern");
  } else if (ac.ground_speed != null && ac.ground_speed > 100) {
    raw += 3;
  }

  // 4. Known aircraft type bonus (0–5)
  if (ac.aircraft_type) { raw += 5; reasons.push(ROLE_LABELS[cat]); }

  // Apply role multiplier
  const multiplied = Math.round(raw * ROLE_MULTIPLIER[cat]);
  if (ROLE_MULTIPLIER[cat] > 1.0) {
    reasons.push(`${ROLE_LABELS[cat]} ×${ROLE_MULTIPLIER[cat]}`);
  }

  const final = Math.min(multiplied, 100);
  return { score: final, reasons };
}

export function scoreColor(score: number): string {
  if (score >= 70) return "#ef4444";
  if (score >= 45) return "#f59e0b";
  if (score >= 25) return "#3b82f6";
  return "#6b7280";
}

export function scoreLabel(score: number): string {
  if (score >= 70) return "CRITICAL";
  if (score >= 45) return "HIGH";
  if (score >= 25) return "MODERATE";
  return "LOW";
}

// Silhouette shape type — determines which outline SVG to use
export type AircraftShape =
  | "tanker-jet"
  | "awacs"
  | "transport-prop"
  | "transport-jet"
  | "patrol-jet"
  | "recon-jet"
  | "uav"
  | "bizjet"
  | "heli"
  | "generic-jet";

const SHAPE_MAP: Record<string, AircraftShape> = {
  K35R: "tanker-jet", K35T: "tanker-jet", K35E: "tanker-jet",
  KC46: "tanker-jet", KC10: "tanker-jet", KC30: "tanker-jet",
  MRTT: "tanker-jet", KC2T: "tanker-jet",
  E3TF: "awacs", E3CF: "awacs", E737: "awacs", E767: "awacs",
  E2C: "awacs", E2D: "awacs", E6B: "awacs",
  C130: "transport-prop", C30J: "transport-prop", C160: "transport-prop",
  A400: "transport-prop", C2: "transport-prop",
  C17: "transport-jet", C5: "transport-jet", C5M: "transport-jet",
  P8: "patrol-jet", EP3: "patrol-jet", P3: "patrol-jet",
  RC135: "recon-jet", R135: "recon-jet",
  RQ4: "uav", MQ9: "uav", MQ4C: "uav", U2: "uav",
  GLF5: "bizjet", GLF6: "bizjet", GLEX: "bizjet", CL60: "bizjet",
  LJ35: "bizjet", C56X: "bizjet",
  H47: "heli", H60: "heli", V22: "heli",
};

export function getAircraftShape(ac: AircraftPosition): AircraftShape {
  const t = ac.aircraft_type?.toUpperCase().replace(/-/g, "") || "";
  if (SHAPE_MAP[t]) return SHAPE_MAP[t];
  if (t.startsWith("KC") || t.startsWith("K35")) return "tanker-jet";
  if (t.startsWith("E3") || t.startsWith("E6")) return "awacs";
  if (t.startsWith("C130") || t.startsWith("C30")) return "transport-prop";
  if (t.startsWith("C17") || t.startsWith("C5")) return "transport-jet";
  if (t.startsWith("RC") || t.startsWith("R135")) return "recon-jet";
  if (t.startsWith("RQ") || t.startsWith("MQ")) return "uav";
  if (t.startsWith("H47") || t.startsWith("H60") || t.startsWith("V22")) return "heli";
  if (t.startsWith("P8") || t.startsWith("EP")) return "patrol-jet";
  const desc = (ac.description || "").toLowerCase();
  if (desc.includes("helicopter") || desc.includes("rotor")) return "heli";
  if (desc.includes("tanker") || desc.includes("refuel")) return "tanker-jet";
  if (desc.includes("transport") || desc.includes("cargo")) return "transport-jet";
  return "generic-jet";
}
