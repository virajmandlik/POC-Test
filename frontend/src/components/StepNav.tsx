interface Props {
  current: number;
  total: number;
  onBack: () => void;
  onNext: () => void;
  nextLabel?: string;
  backLabel?: string;
  nextDisabled?: boolean;
}

export default function StepNav({
  current,
  total,
  onBack,
  onNext,
  nextLabel = "Next →",
  backLabel = "← Back",
  nextDisabled = false,
}: Props) {
  return (
    <div className="flex justify-between items-center mt-6 pt-4 border-t border-border">
      <div>
        {current > 0 && (
          <button className="btn-secondary" onClick={onBack}>
            {backLabel}
          </button>
        )}
      </div>
      <div>
        {current < total - 1 && (
          <button className="btn-primary" onClick={onNext} disabled={nextDisabled}>
            {nextLabel}
          </button>
        )}
      </div>
    </div>
  );
}
