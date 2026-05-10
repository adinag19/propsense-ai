'use client';

import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts';

interface ROIChartProps {
  propertyPrice: number;
  annualSora: number;
  bankSpread: number;
  loanTenure: number;
  holdingYears: number;
  psf_cagr_pct?: number;   // from ROI data if available
  grossYieldPct?: number;   // from ROI data if available
  isHdb: boolean;
}

function computeEMI(principal: number, annualRate: number, months: number): number {
  const r = annualRate / 12;
  if (r === 0) return principal / months;
  return (principal * r * Math.pow(1 + r, months)) / (Math.pow(1 + r, months) - 1);
}

const SGD = (v: number) =>
  v >= 1_000_000
    ? `$${(v / 1_000_000).toFixed(2)}M`
    : `$${Math.round(v / 1000)}K`;

export default function ROIChart({
  propertyPrice, annualSora, bankSpread, loanTenure, holdingYears,
  psf_cagr_pct = 3.5, grossYieldPct = 3.2, isHdb,
}: ROIChartProps) {
  const annualRate = annualSora + bankSpread;
  const downPayment = propertyPrice * 0.25;
  const loanAmount = propertyPrice * 0.75;
  const monthlyEMI = computeEMI(loanAmount, annualRate, loanTenure * 12);
  const annualEMI = monthlyEMI * 12;
  const annualRent = isHdb ? 0 : propertyPrice * (grossYieldPct / 100);
  const cagrDecimal = psf_cagr_pct / 100;

  const data = Array.from({ length: holdingYears + 1 }, (_, yr) => {
    const propertyValue = propertyPrice * Math.pow(1 + cagrDecimal, yr);
    const cumulativeRent = annualRent * yr;
    const cumulativeEMI = annualEMI * Math.min(yr, loanTenure);
    const netROI = (propertyValue - propertyPrice) + cumulativeRent - cumulativeEMI;
    return {
      year: `Yr ${yr}`,
      'Property Value': Math.round(propertyValue),
      'Cumulative Rent': Math.round(cumulativeRent),
      'Cumulative EMI': Math.round(cumulativeEMI),
      'Net ROI': Math.round(netROI),
    };
  });

  const exitROI = data[holdingYears]['Net ROI'];
  const roiPct = ((exitROI / (downPayment)) * 100).toFixed(1);

  return (
    <div>
      <div style={{ display: 'flex', gap: 24, marginBottom: 16, flexWrap: 'wrap' }}>
        <Stat label="Est. Exit Value" value={SGD(data[holdingYears]['Property Value'])} color="#1e40af" />
        <Stat label="Cumulative Rent" value={isHdb ? 'N/A' : SGD(data[holdingYears]['Cumulative Rent'])} color="#16a34a" />
        <Stat label="Total EMI Paid" value={SGD(data[holdingYears]['Cumulative EMI'])} color="#dc2626" />
        <Stat label={`Net ROI on Equity`} value={`${roiPct}%`} color={exitROI >= 0 ? '#16a34a' : '#dc2626'} />
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis dataKey="year" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={SGD} tick={{ fontSize: 11 }} width={56} />
          <Tooltip formatter={(v: number) => SGD(v)} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="4 2" />
          <Line type="monotone" dataKey="Property Value" stroke="#1e40af" strokeWidth={2} dot={false} />
          {!isHdb && <Line type="monotone" dataKey="Cumulative Rent" stroke="#16a34a" strokeWidth={2} dot={false} />}
          <Line type="monotone" dataKey="Cumulative EMI" stroke="#dc2626" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="Net ROI" stroke="#7c3aed" strokeWidth={2.5} dot={false} strokeDasharray="6 2" />
        </LineChart>
      </ResponsiveContainer>
      <p style={{ fontSize: 11, color: '#9ca3af', marginTop: 8 }}>
        Assumes {psf_cagr_pct}% annual PSF growth{!isHdb && `, ${grossYieldPct}% gross yield`}, {((annualRate) * 100).toFixed(2)}% loan rate. For illustration only.
      </p>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}
