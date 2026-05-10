'use client';

import { useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';

interface AreaGeo {
  planning_area: string;
  region: string;
  lat: number;
  lng: number;
  psf_cagr_pct: number;
  gross_yield_pct: number | null;
  latest_psf: number;
}

interface Project {
  project: string;
  street: string;
  lat: number;
  lng: number;
  median_psf_2024_26: number | null;
  median_psf_all: number;
  txn_count_2024_26: number;
  txn_count_all: number;
  latest_year: number;
  property_type: string;
}

type MapMode = 'cagr' | 'price';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const BEARER_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN || '';

function authHeaders(): Record<string, string> {
  return BEARER_TOKEN ? { Authorization: `Bearer ${BEARER_TOKEN}` } : {};
}

function getColor(value: number, min: number, max: number): string {
  const t = Math.max(0, Math.min(1, (value - min) / (max - min || 1)));
  const r = Math.round(220 - t * (220 - 22));
  const g = Math.round(38 + t * (163 - 38));
  const b = Math.round(38 - t * 38);
  return `rgb(${r},${g},${b})`;
}

function psfColor(psf: number): string {
  // Blue gradient: low PSF = light, high PSF = dark blue
  if (psf >= 3000) return '#1e3a8a';
  if (psf >= 2000) return '#1e40af';
  if (psf >= 1500) return '#2563eb';
  if (psf >= 1000) return '#3b82f6';
  return '#93c5fd';
}

// ── Inner map component (runs client-side only) ───────────────────────────

function LeafletMap({
  areas, projects, mode, selectedArea, onAreaClick, onBack,
}: {
  areas: AreaGeo[];
  projects: Project[];
  mode: MapMode;
  selectedArea: AreaGeo | null;
  onAreaClick: (area: AreaGeo) => void;
  onBack: () => void;
}) {
  const { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } = require('react-leaflet');

  const values = areas.map(a => mode === 'cagr' ? a.psf_cagr_pct : a.latest_psf);
  const min = Math.min(...values);
  const max = Math.max(...values);

  // FlyTo controller
  function MapController() {
    const map = useMap();
    useEffect(() => {
      if (selectedArea) {
        map.flyTo([selectedArea.lat, selectedArea.lng], 15, { duration: 1.2 });
      } else {
        map.flyTo([1.3521, 103.8198], 12, { duration: 1.0 });
      }
    }, [selectedArea, map]);
    return null;
  }

  const projectPsfValues = projects.map(p => p.median_psf_2024_26 ?? p.median_psf_all);
  const pMin = projectPsfValues.length ? Math.min(...projectPsfValues) : 0;
  const pMax = projectPsfValues.length ? Math.max(...projectPsfValues) : 0;

  return (
    <MapContainer
      center={[1.3521, 103.8198]}
      zoom={12}
      style={{ height: '480px', width: '100%', borderRadius: '12px' }}
      zoomControl={true}
    >
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        attribution='&copy; <a href="https://carto.com/">CARTO</a>'
      />
      <MapController />

      {/* Overview: area bubbles — hidden when zoomed in */}
      {!selectedArea && areas.map((area) => {
        const val = mode === 'cagr' ? area.psf_cagr_pct : area.latest_psf;
        const color = getColor(val, min, max);
        const radius = mode === 'cagr'
          ? Math.max(8, Math.min(28, Math.abs(area.psf_cagr_pct) * 3))
          : Math.max(8, Math.min(28, (area.latest_psf / (max || 1)) * 28));
        return (
          <CircleMarker
            key={area.planning_area}
            center={[area.lat, area.lng]}
            radius={radius}
            pathOptions={{ color: 'white', weight: 1.5, fillColor: color, fillOpacity: 0.85 }}
            eventHandlers={{ click: () => onAreaClick(area) }}
          >
            <Tooltip>
              <div style={{ fontFamily: 'Inter, sans-serif', minWidth: 150 }}>
                <div style={{ fontWeight: 700, fontSize: 13 }}>{area.planning_area}</div>
                <div style={{ fontSize: 11, color: '#6b7280' }}>{area.region} · Click to drill down</div>
                <div style={{ marginTop: 4, fontSize: 12 }}>
                  <span style={{ color: '#16a34a', fontWeight: 600 }}>+{area.psf_cagr_pct}% CAGR</span>
                  <span style={{ color: '#6b7280', marginLeft: 8 }}>
                    S${area.latest_psf.toLocaleString()}/sqft
                  </span>
                </div>
              </div>
            </Tooltip>
          </CircleMarker>
        );
      })}

      {/* Drill-down: individual project markers */}
      {selectedArea && projects.map((p) => {
        const psf = p.median_psf_2024_26 ?? p.median_psf_all;
        const color = getColor(psf, pMin, pMax);
        const radius = Math.max(5, Math.min(16, (p.txn_count_all / 10) + 5));
        return (
          <CircleMarker
            key={`${p.project}-${p.lat}-${p.lng}`}
            center={[p.lat, p.lng]}
            radius={radius}
            pathOptions={{ color: 'white', weight: 1.5, fillColor: color, fillOpacity: 0.9 }}
          >
            <Tooltip permanent={false}>
              <div style={{ fontFamily: 'Inter, sans-serif', minWidth: 180 }}>
                <div style={{ fontWeight: 700, fontSize: 13 }}>{p.project}</div>
                <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>{p.street}</div>
                {p.median_psf_2024_26 !== null && (
                  <div style={{ fontSize: 12 }}>
                    <span style={{ color: '#1e40af', fontWeight: 600 }}>
                      S${p.median_psf_2024_26.toLocaleString()}/sqft
                    </span>
                    <span style={{ color: '#6b7280', fontSize: 11, marginLeft: 4 }}>(2024–2026)</span>
                  </div>
                )}
                <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>
                  {p.txn_count_2024_26} recent txns · {p.txn_count_all} total
                </div>
                <div style={{ fontSize: 11, color: '#94a3b8' }}>{p.property_type}</div>
              </div>
            </Tooltip>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}

const LazyMap = dynamic(() => Promise.resolve(LeafletMap), { ssr: false });

// ── Main export ───────────────────────────────────────────────────────────

export default function HotAreasMap({ autoZoomArea }: { autoZoomArea?: string }) {
  const [areas, setAreas] = useState<AreaGeo[]>([]);
  const [mode, setMode] = useState<MapMode>('cagr');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedArea, setSelectedArea] = useState<AreaGeo | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectsNote, setProjectsNote] = useState('');

  useEffect(() => {
    fetch(`${API_URL}/areas-geo`, { headers: authHeaders() })
      .then(r => r.json())
      .then(d => { setAreas(d.areas || []); setLoading(false); })
      .catch(() => { setError('Could not load map data'); setLoading(false); });
  }, []);

  // Sub-area → parent planning area (mirrors backend _SUBAREA_TO_PLANNING_AREA)
  const SUBAREA_PARENT: Record<string, string> = {
    katong: 'Marine Parade', siglap: 'Marine Parade', 'tanjong katong': 'Marine Parade',
    'east coast': 'Marine Parade', 'tiong bahru': 'Bukit Merah',
    alexandra: 'Queenstown', dawson: 'Queenstown', commonwealth: 'Queenstown',
    'buona vista': 'Queenstown', 'one north': 'Queenstown', 'pasir panjang': 'Queenstown',
    dover: 'Clementi', 'west coast': 'Clementi',
    kovan: 'Serangoon', 'lorong chuan': 'Serangoon',
    dakota: 'Geylang', aljunied: 'Geylang', macpherson: 'Geylang',
    compassvale: 'Sengkang', anchorvale: 'Sengkang', fernvale: 'Sengkang',
    waterway: 'Punggol', northshore: 'Punggol',
    'boon lay': 'Jurong West', lakeside: 'Jurong East',
    bendemeer: 'Kallang', 'upper thomson': 'Bishan', newton: 'Novena',
    balestier: 'Toa Payoh', canberra: 'Sembawang',
    orchard: 'Orchard', scotts: 'Orchard', raffles: 'Downtown Core',
    bugis: 'Rochor', 'little india': 'Rochor', 'farrer park': 'Rochor',
  };

  // Auto-zoom to planning area when score result comes in
  useEffect(() => {
    if (!autoZoomArea || areas.length === 0) return;
    const q = autoZoomArea.toLowerCase();
    // Direct match first, then sub-area fallback
    const match = areas.find(a => a.planning_area.toLowerCase() === q)
      ?? areas.find(a => a.planning_area.toLowerCase() === (SUBAREA_PARENT[q] ?? '').toLowerCase());
    if (match && (!selectedArea || selectedArea.planning_area !== match.planning_area)) {
      handleAreaClick(match);
    }
  }, [autoZoomArea, areas]);

  const handleAreaClick = async (area: AreaGeo) => {
    setSelectedArea(area);
    setProjects([]);
    setProjectsNote('');
    setProjectsLoading(true);
    try {
      const params = new URLSearchParams({ planning_area: area.planning_area });
      const res = await fetch(`${API_URL}/projects-geo?${params}`, { headers: authHeaders() });
      const data = await res.json();
      setProjects(data.projects || []);
      setProjectsNote(data.note || '');
    } catch {
      setProjectsNote('Could not load project data');
    } finally {
      setProjectsLoading(false);
    }
  };

  const handleBack = () => {
    setSelectedArea(null);
    setProjects([]);
    setProjectsNote('');
  };

  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '6px 16px', borderRadius: 20, border: 'none', cursor: 'pointer',
    fontWeight: 600, fontSize: 13,
    background: active ? '#1e40af' : '#f1f5f9',
    color: active ? 'white' : '#374151',
    transition: 'all 0.15s',
  });

  return (
    <div>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {selectedArea && (
            <button
              onClick={handleBack}
              style={{ padding: '5px 12px', borderRadius: 8, border: '1.5px solid #e2e8f0', background: 'white', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: '#374151' }}
            >
              ← Overview
            </button>
          )}
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#0f172a' }}>
            {selectedArea ? `${selectedArea.planning_area} — Projects` : 'Hot Areas Map'}
          </h3>
        </div>
        {!selectedArea && (
          <div style={{ display: 'flex', gap: 6 }}>
            <button style={btnStyle(mode === 'cagr')} onClick={() => setMode('cagr')}>PSF Growth</button>
            <button style={btnStyle(mode === 'price')} onClick={() => setMode('price')}>Price Level</button>
          </div>
        )}
      </div>

      {/* Legend / status */}
      {!selectedArea && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 10, fontSize: 12, color: '#6b7280', flexWrap: 'wrap' }}>
          <span>Click any bubble to drill into individual projects</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#374151', display: 'inline-block', opacity: 0.4 }} />
            <span style={{ width: 18, height: 18, borderRadius: '50%', background: '#374151', display: 'inline-block', opacity: 0.8 }} />
            <span>Bubble size = {mode === 'cagr' ? 'price growth rate' : 'relative price level'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ width: 40, height: 8, borderRadius: 4, background: 'linear-gradient(to right, rgb(220,38,38), rgb(22,163,74))' }} />
            <span>Low → High</span>
          </div>
        </div>
      )}

      {selectedArea && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10, fontSize: 12, color: '#6b7280' }}>
          <span style={{ color: '#16a34a', fontWeight: 600 }}>+{selectedArea.psf_cagr_pct}% CAGR</span>
          <span>S${selectedArea.latest_psf.toLocaleString()}/sqft area avg</span>
          {projectsLoading && <span>Loading projects…</span>}
          {!projectsLoading && projects.length > 0 && <span>{projects.length} projects shown</span>}
          {projectsNote && <span style={{ fontStyle: 'italic', color: '#94a3b8' }}>{projectsNote}</span>}
        </div>
      )}

      {loading && <div style={{ height: 480, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f8fafc', borderRadius: 12, color: '#6b7280' }}>Loading map…</div>}
      {error && <div style={{ color: '#dc2626', padding: 16 }}>{error}</div>}
      {!loading && !error && areas.length > 0 && (
        <LazyMap
          areas={areas}
          projects={projects}
          mode={mode}
          selectedArea={selectedArea}
          onAreaClick={handleAreaClick}
          onBack={handleBack}
        />
      )}
    </div>
  );
}
