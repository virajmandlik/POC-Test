interface Props {
  label: string;
  value: string | number;
}

export default function MetricCard({ label, value }: Props) {
  return (
    <div className="metric-card">
      <p className="text-[0.72rem] font-semibold uppercase tracking-wide text-text-muted whitespace-nowrap">
        {label}
      </p>
      <p className="text-2xl font-bold text-text mt-0.5">{value}</p>
    </div>
  );
}
