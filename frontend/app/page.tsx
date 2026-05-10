'use client';

import { useState, useEffect } from 'react';
import dynamic from 'next/dynamic';
import styles from './page.module.css';
import Logo from './components/Logo';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const BEARER_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN || '';

const ROIChart = dynamic(() => import('./components/ROIChart'), { ssr: false });
const HotAreasMap = dynamic(() => import('./components/HotAreasMap'), { ssr: false });

// ── Types ──────────────────────────────────────────────────────────────────

interface AreaSuggestion {
  planning_area: string;
  flat_type: string | null;
  region: string;
  latest_psf: number;
  estimated_price: number;
  psf_cagr_pct: number;
  gross_yield_pct: number | null;
  avg_lease_years: number | null;
  mrt_dist_m: number | null;
  cbd_dist_km: number | null;
  school_dist_m: number | null;
  mall_dist_m: number | null;
  park_dist_m: number | null;
}

interface Citation {
  source: string;
  field: string;
  value: string;
  note?: string;
}

interface Dimension {
  score: number;
  weight: number;
  flags: string[];
  citations: Citation[];
  details?: Record<string, unknown>;
}

interface ScoreResult {
  overall_score: number;
  band: string;
  dimensions: Record<string, Dimension>;
  hard_stops: Array<{ dimension: string; flag: string }>;
  disclaimer: string;
  narrative?: {
    summary: string;
    dimension_narratives: Record<string, string>;
    key_risks: string[];
    next_steps: string[];
  };
  context_chunks_used: number;
  sufficient_context: boolean;
  elapsed_seconds: number;
  narrative_error?: string;
  suggested_areas?: AreaSuggestion[];
  max_affordable_price?: number;
  afford_constraint?: string;
}

// ── Presets ────────────────────────────────────────────────────────────────

const PRESETS = {
  'First-time Buyer': {
    label: 'First-time Buyer',
    description: 'Buying your first home (any citizenship)',
    icon: '🏠',
    values: {
      citizenship: 'SC', age: 32, cash: 120000, cpf_oa: 80000,
      monthly_income: 8000, monthly_obligations: 0,
      property_price: 550000, property_type: 'hdb', is_hdb: true,
      flat_type: '4 ROOM', num_existing_properties: 0,
      loan_tenure: 25, annual_sora: 0.037, bank_spread: 0.011,
      holding_years: 10, lease_remaining: 90, planning_area: '',
      purpose: 'live_in' as const, existing_hdb: false, district: '',
    },
  },
  'HDB Upgrader': {
    label: 'HDB / Condo Upgrader',
    description: 'Owns one property, buying a second',
    icon: '📈',
    values: {
      citizenship: 'SC', age: 40, cash: 400000, cpf_oa: 180000,
      monthly_income: 15000, monthly_obligations: 1500,
      property_price: 1800000, property_type: 'private', is_hdb: false,
      flat_type: '', num_existing_properties: 1,
      loan_tenure: 25, annual_sora: 0.037, bank_spread: 0.011,
      holding_years: 10, lease_remaining: 99, planning_area: '',
      purpose: 'invest' as const, existing_hdb: true, district: '',
    },
  },
  'Property Investor': {
    label: 'Property Investor',
    description: 'Buying for rental yield / capital gain',
    icon: '💼',
    values: {
      citizenship: 'SC', age: 45, cash: 600000, cpf_oa: 250000,
      monthly_income: 20000, monthly_obligations: 0,
      property_price: 2200000, property_type: 'private', is_hdb: false,
      flat_type: '', num_existing_properties: 1,
      loan_tenure: 20, annual_sora: 0.037, bank_spread: 0.011,
      holding_years: 8, lease_remaining: 99, planning_area: '',
      purpose: 'invest' as const, existing_hdb: false, district: '',
    },
  },
};

// ── Tooltips ────────────────────────────────────────────────────────────────

const TOOLTIPS: Record<string, string> = {
  cash: 'Your total liquid savings in SGD — cash in bank, excluding CPF and investments.',
  cpf_oa: 'CPF Ordinary Account balance. Can be used for down payment and monthly repayments on HDB and private property.',
  monthly_obligations: 'Total monthly repayments on existing loans — car loan, personal loan, existing mortgage, credit card minimum. Used to compute TDSR.',
  num_existing_properties: 'Number of residential properties you currently own. Affects ABSD rate and LTV limit.',
  property_price: 'The price you expect to pay. For HDB, use the asking price. For private, use the transacted or listed price.',
  planning_area: 'The URA planning zone e.g. Tampines, Queenstown, Bishan. Leave blank and click "Suggest Areas" to get recommendations.',
  lease_remaining: 'For HDB: years left on the 99-year lease. Newer flats have more years remaining. Affects CPF withdrawal limits.',
  loan_tenure: 'How many years to repay the loan. Longer tenure = lower monthly payment but more total interest paid.',
  annual_sora: 'Singapore Overnight Rate Average — the base rate banks use. Currently around 3.7%. Updates monthly.',
  bank_spread: "Your bank's markup above SORA. Typically 0.8–1.2%. Total mortgage rate = SORA + spread.",
  holding_years: 'How many years you plan to keep the property before selling. Affects breakeven calculation.',
};

// ── Constants ──────────────────────────────────────────────────────────────

const BAND_COLORS: Record<string, string> = {
  'Strong Buy': '#16a34a',
  'Conditional Buy': '#2563eb',
  'Marginal': '#d97706',
  'Do Not Buy': '#dc2626',
  // live-in bands
  'Very Ready': '#16a34a',
  'Ready with Caveats': '#2563eb',
  'Marginal Fit': '#d97706',
  'Not Ready': '#dc2626',
};

const DIM_LABELS: Record<string, string> = {
  // invest mode
  financial_readiness: 'Financial Readiness',
  market_timing: 'Market Timing',
  area_value: 'Area Value',
  opportunity_cost: 'Opportunity Cost',
  life_stage_fit: 'Life-Stage Fit',
  // live-in mode
  financial_sustainability: 'Financial Sustainability',
  affordability_comfort: 'Affordability Comfort',
  lifestyle_fit: 'Lifestyle Fit',
  fair_price: 'Fair Price Check',
};

// ── Sub-components ─────────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number }) {
  const color = score >= 80 ? '#16a34a' : score >= 60 ? '#2563eb' : score >= 40 ? '#d97706' : '#dc2626';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 6, background: '#e2e8f0', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ width: `${score}%`, height: '100%', background: color, borderRadius: 4, transition: 'width 0.6s ease' }} />
      </div>
      <span style={{ fontWeight: 700, color, minWidth: 32, fontSize: 13 }}>{score}</span>
    </div>
  );
}

function Tooltip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span style={{ position: 'relative', display: 'inline-flex', marginLeft: 4, verticalAlign: 'middle' }}>
      <button
        type="button"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onClick={() => setShow(s => !s)}
        style={{
          width: 14, height: 14, borderRadius: '50%', border: '1.5px solid #94a3b8',
          background: 'white', color: '#64748b', fontSize: 9, fontWeight: 700,
          cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
          justifyContent: 'center', padding: 0, lineHeight: 1,
        }}
      >?</button>
      {show && (
        <span style={{
          position: 'absolute', bottom: '120%', left: '50%', transform: 'translateX(-50%)',
          background: '#1e293b', color: 'white', fontSize: 11, lineHeight: 1.5,
          padding: '6px 10px', borderRadius: 7, whiteSpace: 'normal', width: 220,
          zIndex: 999, boxShadow: '0 4px 12px rgba(0,0,0,0.2)', pointerEvents: 'none',
        }}>
          {text}
        </span>
      )}
    </span>
  );
}

function Field({ label, children, tooltip }: { label: string; children: React.ReactNode; tooltip?: string }) {
  return (
    <div className={styles.fieldGroup}>
      <label className={styles.fieldLabel}>
        {label}
        {tooltip && <Tooltip text={tooltip} />}
      </label>
      {children}
    </div>
  );
}

function Badge({ icon, label, value, color = '#475569' }: { icon: string; label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 6, padding: '3px 8px', fontSize: 11, color }}>
      <span>{icon}</span>
      <span style={{ color: '#94a3b8', marginRight: 2 }}>{label}</span>
      <span style={{ fontWeight: 600 }}>{value}</span>
    </div>
  );
}

// ── Score Explained card ────────────────────────────────────────────────────

function ScoreExplained({ result }: { result: ScoreResult }) {
  const fin = (result.dimensions.financial_readiness ?? result.dimensions.financial_sustainability) as Dimension | undefined;
  const details = fin?.details as Record<string, number> | undefined;

  const chips: { label: string; value: string; color: string }[] = [];
  if (details?.monthly_repayment) chips.push({ label: 'Monthly EMI', value: `S$${Math.round(details.monthly_repayment).toLocaleString()}`, color: '#1e40af' });
  if (details?.absd != null) chips.push({ label: 'ABSD', value: details.absd === 0 ? 'S$0 — none' : `S$${Math.round(details.absd).toLocaleString()}`, color: details.absd > 0 ? '#dc2626' : '#16a34a' });
  if (details?.tdsr != null) chips.push({ label: 'TDSR', value: `${(details.tdsr * 100).toFixed(1)}%`, color: details.tdsr > 0.55 ? '#dc2626' : details.tdsr > 0.45 ? '#d97706' : '#16a34a' });
  if (details?.msr != null) chips.push({ label: 'MSR', value: `${(details.msr * 100).toFixed(1)}%`, color: details.msr > 0.30 ? '#dc2626' : '#16a34a' });
  if (details?.cash_headroom != null) chips.push({ label: 'Cash buffer after purchase', value: details.cash_headroom >= 0 ? `S$${Math.round(details.cash_headroom).toLocaleString()}` : `–S$${Math.round(Math.abs(details.cash_headroom)).toLocaleString()}`, color: details.cash_headroom >= 0 ? '#16a34a' : '#dc2626' });

  // Collect top flags (max 1 per dimension, skip empty)
  const topFlags: string[] = Object.values(result.dimensions)
    .map(d => d.flags[0])
    .filter(Boolean)
    .slice(0, 4);

  return (
    <div className={styles.card}>
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>
        Why {result.overall_score}/100?
      </div>

      {/* Key numbers */}
      {chips.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
          {chips.map(c => (
            <div key={c.label} style={{ background: '#f8fafc', border: '1.5px solid #e2e8f0', borderRadius: 8, padding: '6px 12px', fontSize: 12 }}>
              <span style={{ color: '#94a3b8' }}>{c.label}: </span>
              <span style={{ fontWeight: 700, color: c.color }}>{c.value}</span>
            </div>
          ))}
        </div>
      )}

      {/* Narrative if available, else top flags */}
      {result.narrative?.summary
        ? <p style={{ fontSize: 13, color: '#334155', lineHeight: 1.7 }}>{result.narrative.summary}</p>
        : topFlags.length > 0 && (
          <ul style={{ paddingLeft: 16, fontSize: 13, color: '#334155', lineHeight: 1.8, margin: 0 }}>
            {topFlags.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        )
      }
    </div>
  );
}

// ── Area at a Glance card ───────────────────────────────────────────────────

function AreaAtAGlance({ result, planningArea, purpose }: {
  result: ScoreResult; planningArea: string; purpose: string;
}) {
  const match   = result.suggested_areas?.find(
    a => a.planning_area.toLowerCase() === planningArea.toLowerCase()
  );
  const cagr    = match?.psf_cagr_pct ?? null;
  const psf     = match?.latest_psf   ?? null;
  const lifeDim = result.dimensions?.lifestyle_fit;
  const mrtM    = (lifeDim?.details as any)?.mrt_dist_m ?? null;
  const mrtName = (lifeDim?.details as any)?.mrt_name ?? null;
  const schM    = (lifeDim?.details as any)?.school_dist_m ?? null;
  const schName = (lifeDim?.details as any)?.school_name ?? null;

  const hasData = cagr !== null || psf !== null || mrtM !== null || schM !== null;
  if (!planningArea || !hasData) return null;

  return (
    <div className={styles.card}>
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>{planningArea} at a Glance</div>
      {match?.region && <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 12 }}>{match.region} region</div>}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {mrtM !== null && (
          <div style={{ background: mrtM <= 400 ? '#f0fdf4' : '#f8fafc', border: `1.5px solid ${mrtM <= 400 ? '#86efac' : '#e2e8f0'}`, borderRadius: 8, padding: '6px 12px', fontSize: 12 }}>
            <span style={{ marginRight: 4 }}>🚇</span>
            <span style={{ fontWeight: 700 }}>{mrtM}m</span>
            {mrtName && <span style={{ color: '#94a3b8', marginLeft: 4, fontSize: 11 }}>{mrtName.replace(' MRT STATION','').replace(' LRT STATION','')}</span>}
          </div>
        )}
        {schM !== null && (
          <div style={{ background: '#f8fafc', border: '1.5px solid #e2e8f0', borderRadius: 8, padding: '6px 12px', fontSize: 12 }}>
            <span style={{ marginRight: 4 }}>🏫</span>
            <span style={{ fontWeight: 700 }}>{schM}m</span>
            {schName && <span style={{ color: '#94a3b8', marginLeft: 4, fontSize: 11 }}>{schName}</span>}
          </div>
        )}
        {psf !== null && (
          <div style={{ background: '#eff6ff', border: '1.5px solid #bfdbfe', borderRadius: 8, padding: '6px 12px', fontSize: 12 }}>
            <span style={{ color: '#94a3b8' }}>Median PSF: </span>
            <span style={{ fontWeight: 700, color: '#1e40af' }}>S${psf.toLocaleString()}/sqft</span>
          </div>
        )}
        {cagr !== null && purpose === 'invest' && (
          <div style={{ background: '#f0fdf4', border: '1.5px solid #86efac', borderRadius: 8, padding: '6px 12px', fontSize: 12 }}>
            <span style={{ color: '#94a3b8' }}>2-yr growth: </span>
            <span style={{ fontWeight: 700, color: '#16a34a' }}>+{cagr}%/yr</span>
          </div>
        )}
        {match?.avg_lease_years && (
          <div style={{ background: '#f8fafc', border: '1.5px solid #e2e8f0', borderRadius: 8, padding: '6px 12px', fontSize: 12 }}>
            <span style={{ color: '#94a3b8' }}>Avg lease: </span>
            <span style={{ fontWeight: 700 }}>{match.avg_lease_years} years</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Recent Transactions card ────────────────────────────────────────────────

interface TxnRow {
  year: number;
  median_price?: number;
  median_psf?: number;
  transactions?: number;
  min_price?: number;
  max_price?: number;
  psf_cagr_pct?: number;
  gross_yield_pct?: number;
}

interface TxnResult {
  transactions: TxnRow[];
  area: string;
  property_type: string;
  flat_type: string;
  source: string;
  note?: string;
}

function RecentTransactions({ planningArea, propertyType, flatType, authHeaders }: {
  planningArea: string; propertyType: string; flatType: string;
  authHeaders: () => Record<string, string>;
}) {
  const [data, setData] = useState<TxnResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!planningArea.trim()) return;
    setLoading(true);
    setData(null);
    const params = new URLSearchParams({
      planning_area: planningArea,
      property_type: propertyType === 'hdb' ? 'hdb' : 'private',
      ...(propertyType === 'hdb' && flatType ? { flat_type: flatType } : {}),
      years: '4',
    });
    fetch(`${API_URL}/transactions?${params}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => setData(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [planningArea, propertyType, flatType]);

  if (!planningArea.trim()) return null;
  if (loading) return <div className={styles.card} style={{ fontSize: 13, color: '#94a3b8' }}>Loading recent transactions…</div>;
  if (!data || data.transactions.length === 0) return null;

  const isHdb = data.property_type === 'HDB Resale';

  return (
    <div className={styles.card}>
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
        Recent Transactions — {data.area}
        {data.flat_type && data.flat_type !== 'all flat types' && ` · ${data.flat_type}`}
      </div>
      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6 }}>{data.source}</div>
      <div style={{ fontSize: 11, color: '#d97706', background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 6, padding: '4px 10px', marginBottom: 12 }}>
        Resale transactions only — new launches and BTO are not included in this data.
      </div>

      {data.note && !isHdb && (
        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 12, fontStyle: 'italic' }}>{data.note}</div>
      )}

      {/* Same table for all property types — HDB and private both have year-by-year data */}
      {data.transactions.length > 0 && data.transactions[0].median_price != null ? (
        <table className={styles.citTable}>
          <thead>
            <tr>
              <th>Year</th>
              <th>Median Price</th>
              <th>Median PSF</th>
              <th>Price Range</th>
              <th>Transactions</th>
            </tr>
          </thead>
          <tbody>
            {data.transactions.map(row => (
              <tr key={row.year}>
                <td style={{ fontWeight: 700 }}>
                  {row.year}
                  {row.year === 2026 && <span style={{ marginLeft: 6, background: '#dcfce7', color: '#16a34a', fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 8 }}>Latest</span>}
                  {(row as any).note && <span style={{ marginLeft: 6, fontSize: 10, color: '#94a3b8', fontStyle: 'italic' }}>{(row as any).note}</span>}
                </td>
                <td style={{ fontWeight: 600 }}>S${row.median_price?.toLocaleString()}</td>
                <td>S${row.median_psf?.toLocaleString()}/sqft</td>
                <td style={{ color: '#64748b', fontSize: 11 }}>
                  S${row.min_price?.toLocaleString()} – S${row.max_price?.toLocaleString()}
                </td>
                <td>{row.transactions?.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {data.transactions[0] && <>
            <div style={{ background: '#eff6ff', border: '1.5px solid #bfdbfe', borderRadius: 8, padding: '8px 14px', fontSize: 13 }}>
              <div style={{ color: '#94a3b8', fontSize: 11, marginBottom: 2 }}>Median PSF</div>
              <div style={{ fontWeight: 700, color: '#1e40af' }}>S${data.transactions[0].median_psf?.toLocaleString()}/sqft</div>
            </div>
          </>}
        </div>
      )}
    </div>
  );
}

function mrtRating(m: number | null): string {
  if (m === null) return '—';
  if (m <= 300) return `${m}m ★★★`;
  if (m <= 600) return `${m}m ★★`;
  return `${m}m ★`;
}

function RichAreaCard({ area: s, rank, active, onClick, purpose = 'invest' }: {
  area: AreaSuggestion; rank: number; active: boolean; onClick: () => void; purpose?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: active ? '#eff6ff' : 'white',
        border: `1.5px solid ${active ? '#2563eb' : '#e2e8f0'}`,
        borderRadius: 12,
        padding: '14px 16px',
        cursor: 'pointer',
        textAlign: 'left',
        width: '100%',
        fontFamily: 'inherit',
        transition: 'border-color 0.15s, background 0.15s, box-shadow 0.15s',
        boxShadow: active ? '0 0 0 3px rgba(37,99,235,0.15)' : 'none',
      }}
      onMouseEnter={e => { if (!active) (e.currentTarget as HTMLButtonElement).style.borderColor = '#93c5fd'; }}
      onMouseLeave={e => { if (!active) (e.currentTarget as HTMLButtonElement).style.borderColor = '#e2e8f0'; }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 15 }}>{rank}. {s.planning_area}</span>
          {s.flat_type && <span style={{ background: '#f1f5f9', color: '#475569', fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10, marginLeft: 8 }}>{s.flat_type}</span>}
          {s.region && <span style={{ color: '#94a3b8', fontSize: 12, marginLeft: 8 }}>{s.region}</span>}
        </div>
        {purpose === 'invest' && (
          <div style={{ textAlign: 'right' }}>
            <div style={{ color: '#16a34a', fontWeight: 700, fontSize: 14 }}>+{s.psf_cagr_pct}% CAGR</div>
            {s.gross_yield_pct !== null && <div style={{ color: '#2563eb', fontSize: 12 }}>{s.gross_yield_pct}% yield</div>}
          </div>
        )}
      </div>

      {/* Price row */}
      <div style={{ fontSize: 13, color: '#334155', marginBottom: 10 }}>
        <span style={{ fontWeight: 600 }}>~S${s.estimated_price.toLocaleString()}</span>
        <span style={{ color: '#94a3b8', marginLeft: 8 }}>S${s.latest_psf.toLocaleString()}/sqft</span>
        {s.avg_lease_years !== null && <span style={{ color: '#94a3b8', marginLeft: 8 }}>{s.avg_lease_years}yr lease remaining</span>}
      </div>

      {/* Region badge */}
      {s.region && (
        <div style={{ display: 'flex', gap: 6 }}>
          <Badge icon="📍" label="Region" value={s.region} />
        </div>
      )}
    </button>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function Home() {
  const [form, setForm] = useState({
    query: '',
    citizenship: 'SC',
    age: 35,
    cash: 200000,
    cpf_oa: 80000,
    monthly_income: 8000,
    monthly_obligations: 0,
    property_price: 1000000,
    property_type: 'private',
    is_hdb: false,
    flat_type: '4 ROOM',
    planning_area: '',
    loan_tenure: 25,
    annual_sora: 0.037,
    bank_spread: 0.011,
    num_existing_properties: 0,
    holding_years: 10,
    lease_remaining: 99,
    existing_hdb: false,
    district: '',
    purpose: 'live_in' as 'live_in' | 'invest',
    include_narrative: true,
  });

  const [result, setResult] = useState<ScoreResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [suggestions, setSuggestions] = useState<AreaSuggestion[] | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [maxAffordable, setMaxAffordable] = useState<number | null>(null);
  const [affordConstraint, setAffordConstraint] = useState('');
  const [activeTab, setActiveTab] = useState<'score' | 'map'>('score');
  const [parseLoading, setParseLoading] = useState(false);
  const [parsedFields, setParsedFields] = useState<Record<string, unknown> | null>(null);

  const NUM_FIELDS = new Set(['num_existing_properties', 'age', 'loan_tenure', 'holding_years', 'lease_remaining']);

  const handlePreset = (key: keyof typeof PRESETS) => {
    setForm(prev => ({ ...prev, ...PRESETS[key].values }));
    setResult(null);
    setSuggestions(null);
    setError('');
  };

  const authHeaders = (): Record<string, string> => {
    const h: Record<string, string> = {};
    if (BEARER_TOKEN) h['Authorization'] = `Bearer ${BEARER_TOKEN}`;
    return h;
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value, type } = e.target;
    const coerced = type === 'checkbox'
      ? (e.target as HTMLInputElement).checked
      : (type === 'number' || NUM_FIELDS.has(name)) ? Number(value) : value;
    setForm(prev => ({
      ...prev,
      [name]: coerced,
      ...(name === 'property_type' ? { is_hdb: value === 'hdb', flat_type: value === 'hdb' ? '4 ROOM' : '' } : {}),
    }));
  };

  const LOCATION_HINTS: Record<string, string> = {
    East: 'Tampines', West: 'Jurong East', North: 'Woodlands', Central: 'Novena',
  };

  const handleParse = async () => {
    if (!form.query.trim()) return;
    setParseLoading(true);
    setParsedFields(null);
    try {
      const res = await fetch(`${API_URL}/parse-query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ query: form.query }),
      });
      if (!res.ok) return;
      const data = await res.json();
      const ext = data.extracted as Record<string, unknown>;
      if (!ext || Object.keys(ext).length === 0) return;

      // Merge extracted fields into form
      setForm(prev => {
        const updates: Partial<typeof prev> = {};
        if (ext.citizenship) updates.citizenship = String(ext.citizenship);
        if (ext.cash) updates.cash = Number(ext.cash);
        if (ext.cpf_oa != null) updates.cpf_oa = Number(ext.cpf_oa);
        if (ext.monthly_income) updates.monthly_income = Number(ext.monthly_income);
        if (ext.age) updates.age = Number(ext.age);
        if (ext.loan_tenure) updates.loan_tenure = Number(ext.loan_tenure);
        if (ext.holding_years) updates.holding_years = Number(ext.holding_years);
        if (ext.num_existing_properties != null) updates.num_existing_properties = Number(ext.num_existing_properties);
        if (ext.property_price) updates.property_price = Number(ext.property_price);
        if (ext.purpose) updates.purpose = ext.purpose as 'live_in' | 'invest';
        if (ext.planning_area) updates.planning_area = String(ext.planning_area);
        else if (ext.location_hint && !prev.planning_area) {
          // Don't override existing planning area, but suggest one from hint
        }
        if (ext.property_type) {
          const pt = String(ext.property_type);
          updates.property_type = pt;
          updates.is_hdb = pt === 'hdb';
        }
        if (ext.flat_type) updates.flat_type = String(ext.flat_type);
        return { ...prev, ...updates };
      });
      setParsedFields(ext);
    } catch { /* silent */ }
    finally { setParseLoading(false); }
  };

  const handleSuggest = async () => {
    setSuggestLoading(true);
    setSuggestions(null);
    setMaxAffordable(null);
    setAffordConstraint('');
    setError('');
    try {
      const params = new URLSearchParams({
        monthly_income: String(form.monthly_income),
        cash: String(form.cash),
        cpf_oa: String(form.cpf_oa),
        annual_sora: String(form.annual_sora),
        bank_spread: String(form.bank_spread),
        loan_tenure: String(form.loan_tenure),
        citizenship: form.citizenship,
        property_type: form.property_type,
        ...(form.property_type === 'hdb' && form.flat_type ? { flat_type: form.flat_type } : {}),
        ...(form.query ? { query: form.query } : {}),
        top_n: '5',
      });
      const res = await fetch(`${API_URL}/suggest-areas?${params}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSuggestions(data.suggestions);
      setMaxAffordable(data.max_affordable_price ?? null);
      setAffordConstraint(data.constraint ?? '');
      setActiveTab('score');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSuggestLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setResult(null);
    setSuggestions(null);
    try {
      const res = await fetch(`${API_URL}/score`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ ...form, include_narrative: true }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const detail = body?.detail;
        const msg = typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((e: { msg?: string; loc?: (string | number)[] }) =>
                `${(e.loc ?? []).slice(1).join('.')}: ${e.msg ?? JSON.stringify(e)}`
              ).join(' | ')
            : `HTTP ${res.status} ${res.statusText}`;
        throw new Error(msg);
      }
      const data = await res.json();
      setResult(data);
      setActiveTab('score');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const scoreWithArea = async (area: AreaSuggestion) => {
    const updatedForm = {
      ...form,
      planning_area: area.planning_area,
      ...(area.flat_type ? { flat_type: area.flat_type, is_hdb: form.is_hdb } : {}),
      include_narrative: true,
    };
    setForm(prev => ({ ...prev, planning_area: area.planning_area }));
    setSuggestions(null);
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const res = await fetch(`${API_URL}/score`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(updatedForm),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const detail = body?.detail;
        const msg = typeof detail === 'string' ? detail
          : Array.isArray(detail)
            ? detail.map((e: { msg?: string; loc?: (string | number)[] }) =>
                `${(e.loc ?? []).slice(1).join('.')}: ${e.msg ?? JSON.stringify(e)}`
              ).join(' | ')
            : `HTTP ${res.status} ${res.statusText}`;
        throw new Error(msg);
      }
      setResult(await res.json());
      setActiveTab('score');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const bandColor = result ? (BAND_COLORS[result.band] || '#374151') : '#374151';

  // Extract CAGR + yield from area_value dimension citations for ROI chart
  const roiCagr = result?.dimensions?.area_value?.details?.psf_cagr_pct as number | undefined;
  const roiYield = result?.dimensions?.market_timing?.details?.gross_yield_pct as number | undefined;

  return (
    <div className={styles.root}>
      {/* ── Navbar ── */}
      <nav className={styles.navbar}>
        <div className={styles.navBrand}>
          <Logo size={36} />
          <div>
            <div className={styles.navWordmark}>PropSense<span>.AI</span></div>
            <div className={styles.navTagline}>Singapore Property Buy Readiness</div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={styles.navBadge}>RAG + Deterministic</span>
          <span className={styles.navBadge}>URA · HDB · Policy</span>
        </div>
      </nav>

      {/* ── Body ── */}
      <div className={styles.body}>
        <div className={styles.layout}>

          {/* ── Form panel ── */}
          <form onSubmit={handleSubmit} className={styles.formPanel}>
            <div className={styles.formTitle}>Buyer Profile & Property</div>

            {/* Quick-start presets */}
            <div className={styles.section}>
              <div className={styles.sectionTitle}>Quick Start — pick your profile</div>
              <div style={{ display: 'flex', gap: 8 }}>
                {(Object.keys(PRESETS) as (keyof typeof PRESETS)[]).map(key => {
                  const p = PRESETS[key];
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => handlePreset(key)}
                      style={{
                        flex: 1, padding: '8px 4px', borderRadius: 9,
                        border: '1.5px solid #e2e8f0', background: '#f8fafc',
                        cursor: 'pointer', fontFamily: 'inherit', textAlign: 'center',
                        transition: 'border-color 0.15s, background 0.15s',
                      }}
                      onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#93c5fd'; (e.currentTarget as HTMLButtonElement).style.background = '#eff6ff'; }}
                      onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#e2e8f0'; (e.currentTarget as HTMLButtonElement).style.background = '#f8fafc'; }}
                    >
                      <div style={{ fontSize: 18, marginBottom: 2 }}>{p.icon}</div>
                      <div style={{ fontSize: 11, fontWeight: 700, color: '#1e293b' }}>{p.label}</div>
                      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 1 }}>{p.description}</div>
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 6 }}>
                Defaults to Singapore Citizen — change Citizenship if you are PR or Foreigner.
              </div>
            </div>

            {/* Purpose toggle */}
            <div className={styles.section}>
              <div className={styles.sectionTitle}>Buying Purpose</div>
              <div style={{ display: 'flex', gap: 0, border: '1.5px solid #e2e8f0', borderRadius: 9, overflow: 'hidden' }}>
                {(['live_in', 'invest'] as const).map(p => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setForm(prev => ({ ...prev, purpose: p }))}
                    style={{
                      flex: 1,
                      padding: '9px 0',
                      fontSize: 13,
                      fontWeight: 600,
                      border: 'none',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      background: form.purpose === p ? (p === 'live_in' ? '#1e40af' : '#16a34a') : '#f8fafc',
                      color: form.purpose === p ? 'white' : '#64748b',
                      transition: 'background 0.15s, color 0.15s',
                    }}
                  >
                    {p === 'live_in' ? '🏠 Buy to Live In' : '📈 Buy to Invest'}
                  </button>
                ))}
              </div>
              <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6 }}>
                {form.purpose === 'live_in'
                  ? 'Scores affordability comfort, lifestyle fit (MRT/schools/parks), and fair price.'
                  : 'Scores market timing, area capital growth, rental yield, and opportunity cost.'}
              </div>
            </div>

            <div className={styles.section}>
              <div className={styles.sectionTitle}>Ask AI Advisor</div>
              <textarea
                name="query"
                value={form.query}
                onChange={e => { handleChange(e); setParsedFields(null); }}
                placeholder="Describe your situation — e.g. 'PR with $350k savings, looking for 4-room flat near MRT in the East'"
                rows={2}
                className={styles.textarea}
              />
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
                <div style={{ fontSize: 10, color: '#94a3b8' }}>
                  AI will read your description and fill the form below automatically.
                </div>
                <button
                  type="button"
                  onClick={handleParse}
                  disabled={parseLoading || !form.query.trim()}
                  style={{
                    padding: '6px 14px', background: '#7c3aed', color: 'white',
                    border: 'none', borderRadius: 7, fontSize: 12, fontWeight: 700,
                    cursor: 'pointer', fontFamily: 'inherit', opacity: parseLoading ? 0.6 : 1,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {parseLoading ? 'Reading…' : 'Fill Form with AI'}
                </button>
              </div>

              {/* Show what AI extracted */}
              {parsedFields && Object.keys(parsedFields).length > 0 && (
                <div style={{ marginTop: 10, background: '#f5f3ff', border: '1.5px solid #c4b5fd', borderRadius: 8, padding: '10px 12px', fontSize: 12 }}>
                  <div style={{ fontWeight: 700, color: '#5b21b6', marginBottom: 6 }}>
                    AI filled in: (review and adjust if needed)
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {Object.entries(parsedFields).map(([k, v]) => {
                      const labels: Record<string, string> = {
                        citizenship: 'Citizenship', cash: 'Cash', cpf_oa: 'CPF OA',
                        monthly_income: 'Income/mo', property_type: 'Property type',
                        flat_type: 'Flat type', planning_area: 'Planning area',
                        location_hint: 'Location hint', purpose: 'Purpose',
                        property_price: 'Price', num_bedrooms: 'Bedrooms',
                        age: 'Age', loan_tenure: 'Loan tenure', holding_years: 'Holding period',
                        num_existing_properties: 'Properties owned',
                      };
                      const display = typeof v === 'number' && v > 1000
                        ? `S$${Number(v).toLocaleString()}`
                        : String(v);
                      return (
                        <span key={k} style={{ background: '#ede9fe', color: '#5b21b6', padding: '2px 8px', borderRadius: 10, fontWeight: 600 }}>
                          {labels[k] || k}: {display}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            <div className={styles.section}>
              <div className={styles.sectionTitle}>Buyer Profile</div>
              <div className={styles.grid2}>
                <Field label="Citizenship">
                  <select name="citizenship" value={form.citizenship} onChange={handleChange} className={styles.input}>
                    <option value="SC">Singapore Citizen</option>
                    <option value="PR">Permanent Resident</option>
                    <option value="FR">Foreigner</option>
                  </select>
                </Field>
                <Field label="Age">
                  <input name="age" type="number" value={form.age} onChange={handleChange} className={styles.input} min={21} max={75} />
                </Field>
                <Field label="Cash Savings (SGD)" tooltip={TOOLTIPS.cash}>
                  <input name="cash" type="number" value={form.cash} onChange={handleChange} className={styles.input} min={0} step={10000} />
                </Field>
                <Field label="CPF OA Balance (SGD)" tooltip={TOOLTIPS.cpf_oa}>
                  <input name="cpf_oa" type="number" value={form.cpf_oa} onChange={handleChange} className={styles.input} min={0} step={5000} />
                </Field>
                <Field label="Monthly Income (SGD)">
                  <input name="monthly_income" type="number" value={form.monthly_income} onChange={handleChange} className={styles.input} min={0} step={500} />
                </Field>
                <Field label="Existing Loan Repayments/mo" tooltip={TOOLTIPS.monthly_obligations}>
                  <input name="monthly_obligations" type="number" value={form.monthly_obligations} onChange={handleChange} className={styles.input} min={0} step={100} />
                </Field>
                <Field label="Properties Owned" tooltip={TOOLTIPS.num_existing_properties}>
                  <select name="num_existing_properties" value={form.num_existing_properties} onChange={handleChange} className={styles.input}>
                    <option value={0}>0 — First property</option>
                    <option value={1}>1 — Second property</option>
                    <option value={2}>2+ — Third+</option>
                  </select>
                </Field>
              </div>
            </div>

            <div className={styles.section}>
              <div className={styles.sectionTitle}>Property Details</div>
              <div className={styles.grid2}>
                <Field label="Property Price (SGD)" tooltip={TOOLTIPS.property_price}>
                  <input name="property_price" type="number" value={form.property_price} onChange={handleChange} className={styles.input} min={100000} step={10000} />
                </Field>
                <Field label="Property Type">
                  <select name="property_type" value={form.property_type} onChange={handleChange} className={styles.input}>
                    <option value="private">Private Condo</option>
                    <option value="landed">Landed (Terrace / Semi-D / Bungalow)</option>
                    <option value="hdb">HDB Resale</option>
                  </select>
                </Field>
                {form.property_type === 'hdb' && (
                  <Field label="Flat Type">
                    <select name="flat_type" value={form.flat_type} onChange={handleChange} className={styles.input}>
                      <option value="3 ROOM">3-Room</option>
                      <option value="4 ROOM">4-Room</option>
                      <option value="5 ROOM">5-Room</option>
                      <option value="EXECUTIVE">Executive</option>
                    </select>
                  </Field>
                )}
                <Field label="Planning Area (optional)" tooltip={TOOLTIPS.planning_area}>
                  <input name="planning_area" type="text" value={form.planning_area} onChange={handleChange} className={styles.input} placeholder="e.g. Tampines" />
                </Field>
                <Field label="Remaining Lease (years)" tooltip={TOOLTIPS.lease_remaining}>
                  <input name="lease_remaining" type="number" value={form.lease_remaining} onChange={handleChange} className={styles.input} min={1} max={99} />
                </Field>
              </div>
            </div>

            <div className={styles.section}>
              <div className={styles.sectionTitle}>Loan & Planning</div>
              <div className={styles.grid2}>
                <Field label="Loan Tenure (years)" tooltip={TOOLTIPS.loan_tenure}>
                  <input name="loan_tenure" type="number" value={form.loan_tenure} onChange={handleChange} className={styles.input} min={5} max={35} />
                </Field>
                <Field label="SORA Rate" tooltip={TOOLTIPS.annual_sora}>
                  <input name="annual_sora" type="number" value={form.annual_sora} onChange={handleChange} className={styles.input} step={0.001} min={0} max={0.15} />
                </Field>
                <Field label="Bank Spread" tooltip={TOOLTIPS.bank_spread}>
                  <input name="bank_spread" type="number" value={form.bank_spread} onChange={handleChange} className={styles.input} step={0.001} min={0} max={0.05} />
                </Field>
                <Field label="Holding Period (years)" tooltip={TOOLTIPS.holding_years}>
                  <input name="holding_years" type="number" value={form.holding_years} onChange={handleChange} className={styles.input} min={1} max={30} />
                </Field>
              </div>
            </div>

            {/* Query vs planning area conflict warning */}
            {(() => {
              if (!form.planning_area || !form.query) return null;
              const q = form.query.toLowerCase();
              const EAST = ['east coast','east side','eastern',' east','bedok','tampines','pasir ris','marine parade','hougang','sengkang','punggol'];
              const WEST = ['west coast','west side',' west','jurong','clementi','bukit batok','bukit panjang','choa chu kang'];
              const NORTH = ['north ','northern','woodlands','yishun','sembawang'];
              const WEST_AREAS = ['jurong','clementi','bukit batok','bukit panjang','choa chu kang'];
              const EAST_AREAS = ['bedok','tampines','pasir ris','marine parade','hougang','sengkang','punggol'];
              const area = form.planning_area.toLowerCase();
              const isEastQuery = EAST.some(k => q.includes(k));
              const isWestQuery = WEST.some(k => q.includes(k));
              const isNorthQuery = NORTH.some(k => q.includes(k));
              const isWestArea = WEST_AREAS.some(k => area.includes(k));
              const isEastArea = EAST_AREAS.some(k => area.includes(k));
              const conflict = (isEastQuery && isWestArea) || (isWestQuery && isEastArea);
              if (!conflict) return null;
              return (
                <div style={{ background: '#fffbeb', border: '1.5px solid #fcd34d', borderRadius: 8, padding: '10px 12px', fontSize: 12, color: '#92400e', marginBottom: 8 }}>
                  <strong>Heads up:</strong> Your question mentions {isEastQuery ? 'East Coast' : isWestQuery ? 'West' : 'North'} but Planning Area is set to <strong>{form.planning_area}</strong> which is on the {isWestArea ? 'West' : 'East'} side. Clear the Planning Area field to get suggestions matching your question.
                  <button type="button" onClick={() => setForm(p => ({...p, planning_area: ''}))} style={{ marginLeft: 10, padding: '2px 8px', background: '#f59e0b', color: 'white', border: 'none', borderRadius: 5, fontSize: 11, fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}>Clear</button>
                </div>
              );
            })()}

            {form.property_type === 'landed' && (
              <div style={{ background: '#fef3c7', border: '1.5px solid #fcd34d', borderRadius: 8, padding: '10px 12px', fontSize: 12, color: '#92400e', marginBottom: 8 }}>
                <strong>Landed rules:</strong> Foreigners cannot buy landed property in Singapore. PRs require LDAU approval. Price benchmarks use private condo data — landed PSF is not directly comparable.
              </div>
            )}
            <div className={styles.btnRow}>
              <button type="submit" disabled={loading} className={styles.submitBtn}>
                {loading ? 'Analysing…' : 'Get Buy Readiness Score'}
              </button>
            </div>
            <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6, textAlign: 'center' }}>
              Leave Planning Area blank to get area suggestions based on your budget.
            </div>
          </form>

          {/* ── Results panel ── */}
          <div className={styles.resultsPanel}>
            {error && <div className={styles.errorBox}><strong>Error:</strong> {error}</div>}

            {loading && (
              <div className={styles.loadingBox}>
                <div className={styles.spinner} />
                <p>Analysing your property — checking market data, policy rules and financials…</p>
              </div>
            )}

            {/* Tab bar — always show once we have content */}
            {(result || suggestions) && !loading && (
              <div style={{ display: 'flex', gap: 4, background: '#f1f5f9', padding: 4, borderRadius: 10, width: 'fit-content' }}>
                <button
                  onClick={() => setActiveTab('score')}
                  style={{ padding: '7px 18px', borderRadius: 7, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600, fontFamily: 'inherit', background: activeTab === 'score' ? 'white' : 'transparent', color: activeTab === 'score' ? '#1e40af' : '#64748b', boxShadow: activeTab === 'score' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none', transition: 'all 0.15s' }}
                >
                  Score Analysis
                </button>
                <button
                  onClick={() => setActiveTab('map')}
                  style={{ padding: '7px 18px', borderRadius: 7, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600, fontFamily: 'inherit', background: activeTab === 'map' ? 'white' : 'transparent', color: activeTab === 'map' ? '#1e40af' : '#64748b', boxShadow: activeTab === 'map' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none', transition: 'all 0.15s' }}
                >
                  Hot Areas Map
                </button>
              </div>
            )}

            {/* ── Score tab ── */}
            {activeTab === 'score' && (
              <>
                {/* Area suggestions */}
                {suggestions && (
                  <div className={styles.card}>
                    <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Suggested Areas for Your Budget</div>
                    {maxAffordable !== null && (
                      <div style={{ background: '#eff6ff', border: '1.5px solid #bfdbfe', borderRadius: 8, padding: '8px 12px', marginBottom: 12, fontSize: 13 }}>
                        <span style={{ color: '#1e40af', fontWeight: 700 }}>Max affordable: S${maxAffordable.toLocaleString()}</span>
                        <span style={{ color: '#6b7280', marginLeft: 8 }}>· limited by {affordConstraint}</span>
                      </div>
                    )}
                    {suggestions.length === 0
                      ? <div style={{ color: '#dc2626', fontSize: 13 }}>No areas found within your budget. Consider increasing savings or income before buying.</div>
                      : <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 12 }}>Click an area to score it instantly.</div>
                    }
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {suggestions.map((s, i) => (
                        <RichAreaCard key={s.planning_area + (s.flat_type || '')} area={s} rank={i + 1}
                          purpose={form.purpose}
                          active={form.planning_area === s.planning_area}
                          onClick={() => scoreWithArea(s)} />
                      ))}
                    </div>
                  </div>
                )}

                {result && (
                  <>
                    {/* Score hero */}
                    <div className={styles.scoreCard} style={{ borderLeftColor: bandColor }}>
                      <div className={styles.scoreHeader}>
                        <div>
                          <div className={styles.scoreNumber} style={{ color: bandColor }}>
                            {result.overall_score}<span className={styles.scoreMax}>/100</span>
                          </div>
                          <div className={styles.scoreBand} style={{ background: bandColor }}>{result.band}</div>
                        </div>
                        <div className={styles.scoreMeta}>
                          <div>{result.elapsed_seconds}s</div>
                          {!result.sufficient_context && <div style={{ color: '#d97706' }}>⚠ Limited market data for this area</div>}
                        </div>
                      </div>
                      {result.narrative?.summary && <p className={styles.summary}>{result.narrative.summary}</p>}
                    </div>

                    {/* Auto area suggestions (when no planning_area given) */}
                    {result.suggested_areas && result.suggested_areas.length > 0 && (
                      <div className={styles.card}>
                        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Areas to Consider</div>
                        {result.max_affordable_price && (
                          <div style={{ background: '#eff6ff', border: '1.5px solid #bfdbfe', borderRadius: 8, padding: '8px 12px', marginBottom: 12, fontSize: 13 }}>
                            <span style={{ color: '#1e40af', fontWeight: 700 }}>Max affordable: S${result.max_affordable_price.toLocaleString()}</span>
                            <span style={{ color: '#6b7280', marginLeft: 8 }}>· limited by {result.afford_constraint}</span>
                          </div>
                        )}
                        <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 12 }}>Based on your profile — click any area to score it instantly.</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                          {result.suggested_areas.map((s, i) => (
                            <RichAreaCard key={s.planning_area + (s.flat_type || '')} area={s} rank={i + 1}
                              active={form.planning_area === s.planning_area}
                              onClick={() => scoreWithArea(s)} />
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Hard stops */}
                    {result.hard_stops.length > 0 && (
                      <div className={styles.hardStopsCard}>
                        <div style={{ fontWeight: 700, fontSize: 14, color: '#991b1b', marginBottom: 8 }}>⛔ Hard Stops</div>
                        {result.hard_stops.map((hs, i) => (
                          <div key={i} className={styles.hardStop}>
                            <span className={styles.stopTag}>{DIM_LABELS[hs.dimension] || hs.dimension}</span>
                            {hs.flag}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Score breakdown */}
                    <div className={styles.card}>
                      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 14 }}>Score Breakdown</div>
                      {Object.entries(result.dimensions).map(([key, dim]) => (
                        <div key={key} className={styles.dimRow}>
                          <div className={styles.dimLabel}>
                            <span>{DIM_LABELS[key] || key}</span>
                            <span style={{ fontSize: 11, color: '#94a3b8', fontWeight: 500 }}>weight {(dim.weight * 100).toFixed(0)}%</span>
                          </div>
                          <ScoreBar score={dim.score} />
                          {dim.flags.length > 0 && (
                            <ul className={styles.flags}>
                              {dim.flags.map((f, i) => <li key={i}>{f}</li>)}
                            </ul>
                          )}
                          {result.narrative?.dimension_narratives?.[key] && (
                            <p className={styles.dimNarrative}>{result.narrative.dimension_narratives[key]}</p>
                          )}
                        </div>
                      ))}
                    </div>

                    {/* Market reality check — warn when entered price is far above HDB median */}
                    {(() => {
                      const fairFlags = result.dimensions?.fair_price?.flags ?? [];
                      const bigPremium = fairFlags.some(f => f.includes('Large premium'));
                      // Only warn on large premiums for smaller flat types — 5-room/Executive can legitimately hit $1M+
                      const smallerFlat = ['3 ROOM', '4 ROOM'].includes(form.flat_type.toUpperCase());
                      if (!bigPremium || !form.is_hdb || !smallerFlat) return null;
                      // Extract the market median from the flag text
                      const medianMatch = fairFlags.find(f => f.includes('area median'))?.match(/S\$([\d,]+) area median/);
                      const marketMedian = medianMatch ? medianMatch[1] : null;
                      return (
                        <div style={{ background: '#fffbeb', border: '1.5px solid #fcd34d', borderRadius: 12, padding: '14px 18px', fontSize: 13 }}>
                          <div style={{ fontWeight: 700, color: '#92400e', marginBottom: 4 }}>
                            ⚠️ Entered price is above typical HDB market range
                          </div>
                          <div style={{ color: '#78350f', lineHeight: 1.6 }}>
                            {marketMedian
                              ? <>The typical resale price for a <strong>{form.flat_type}</strong> in <strong>{form.planning_area || 'this area'}</strong> is around <strong>S${marketMedian}</strong>. Your entered price of <strong>S${form.property_price.toLocaleString()}</strong> is well above this — it may still exist (e.g. high floor, recently renovated, facing) but is unusual for this flat type.</>
                              : <>The entered price appears to be significantly above the typical market range for this flat type and area.</>
                            }
                          </div>
                          {marketMedian && (
                            <button
                              type="button"
                              onClick={() => setForm(prev => ({ ...prev, property_price: parseInt(marketMedian.replace(/,/g, '')) }))}
                              style={{ marginTop: 10, padding: '6px 14px', background: '#f59e0b', color: 'white', border: 'none', borderRadius: 7, fontSize: 12, fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit' }}
                            >
                              Use market median S${marketMedian}
                            </button>
                          )}
                        </div>
                      );
                    })()}

                    {/* Score Explained */}
                    <ScoreExplained result={result} />

                    {/* Area at a Glance */}
                    <AreaAtAGlance result={result} planningArea={form.planning_area} purpose={form.purpose} />

                    {/* Recent Transactions */}
                    {form.planning_area && (
                      <RecentTransactions
                        planningArea={form.planning_area}
                        propertyType={form.property_type}
                        flatType={form.flat_type}
                        authHeaders={authHeaders}
                      />
                    )}

                    {/* ROI chart — invest mode only */}
                    {form.purpose === 'invest' && (
                      <div className={styles.card}>
                        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 16 }}>ROI Projection</div>
                        <ROIChart
                          propertyPrice={form.property_price}
                          annualSora={form.annual_sora}
                          bankSpread={form.bank_spread}
                          loanTenure={form.loan_tenure}
                          holdingYears={form.holding_years}
                          psf_cagr_pct={roiCagr ?? 3.5}
                          grossYieldPct={roiYield ?? 3.2}
                          isHdb={form.is_hdb}
                        />
                      </div>
                    )}

                    {/* Key risks & next steps */}
                    {result.narrative && (
                      <div className={styles.grid2Cards}>
                        {(result.narrative.key_risks?.length ?? 0) > 0 && (
                          <div className={styles.card}>
                            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12, color: '#dc2626' }}>Key Risks</div>
                            <ul className={styles.listUl}>
                              {result.narrative.key_risks.map((r, i) => <li key={i}>{r}</li>)}
                            </ul>
                          </div>
                        )}
                        {(result.narrative.next_steps?.length ?? 0) > 0 && (
                          <div className={styles.card}>
                            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12, color: '#16a34a' }}>Next Steps</div>
                            <ol className={styles.listOl}>
                              {result.narrative.next_steps.map((s, i) => <li key={i}>{s}</li>)}
                            </ol>
                          </div>
                        )}
                      </div>
                    )}

                    {result.narrative_error && (
                      <div className={styles.errorBox}>Narrative generation failed: {result.narrative_error}</div>
                    )}

                    <div className={styles.disclaimer}>{result.disclaimer}</div>
                  </>
                )}

                {!result && !loading && !error && !suggestions && (
                  <div className={styles.placeholder}>
                    <p style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Welcome to PropSense.AI</p>
                    <p>Fill in your profile and click <strong>Get Buy Readiness Score</strong> to analyse a property,<br />or use <strong>Suggest Areas</strong> to find the best planning areas for your budget.</p>
                  </div>
                )}
              </>
            )}

            {/* ── Map tab ── */}
            {activeTab === 'map' && (
              <div className={styles.card}>
                <HotAreasMap autoZoomArea={form.planning_area || undefined} />
              </div>
            )}

            {/* Map tab accessible even without a score result */}
            {!result && !loading && !suggestions && (
              <div className={styles.card}>
                <HotAreasMap autoZoomArea={form.planning_area || undefined} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
