export default function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="36" height="36" rx="8" fill="#1e40af" />
      {/* Building silhouette */}
      <rect x="10" y="16" width="16" height="13" rx="1" fill="white" fillOpacity="0.15" />
      <rect x="10" y="16" width="16" height="13" rx="1" stroke="white" strokeWidth="1.5" />
      {/* Roof / arrow up */}
      <path d="M7 17L18 8L29 17" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {/* Door */}
      <rect x="15" y="22" width="6" height="7" rx="1" fill="white" fillOpacity="0.9" />
      {/* Chart bars (top-right of building) */}
      <rect x="12" y="19" width="2" height="4" rx="0.5" fill="#60a5fa" />
      <rect x="15.5" y="18" width="2" height="5" rx="0.5" fill="#93c5fd" />
      <rect x="22" y="19" width="2" height="4" rx="0.5" fill="#60a5fa" />
    </svg>
  );
}
