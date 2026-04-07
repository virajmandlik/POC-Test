interface Props {
  title: string;
  subtitle?: string;
}

export default function PageHeader({ title, subtitle }: Props) {
  return (
    <div className="page-header">
      <h1 className="text-2xl font-bold text-text">{title}</h1>
      {subtitle && <p className="text-text-muted text-sm mt-0.5">{subtitle}</p>}
    </div>
  );
}
