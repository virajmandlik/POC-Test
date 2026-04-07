import { cn } from "@/lib/utils";
import { Check } from "lucide-react";

interface Props {
  steps: string[];
  current: number;
  completed: Set<number>;
}

export default function Stepper({ steps, current, completed }: Props) {
  return (
    <div className="flex items-center gap-0 my-3 mb-5 overflow-x-auto">
      {steps.map((label, i) => {
        const isDone = completed.has(i) && i !== current;
        const isActive = i === current;

        return (
          <div key={i} className="flex items-center">
            {i > 0 && (
              <div
                className={cn(
                  "w-8 h-0.5 flex-shrink-0",
                  completed.has(i - 1) ? "bg-primary" : "bg-gray-200",
                )}
              />
            )}
            <div
              className={cn(
                "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-all",
                isDone && "text-primary",
                isActive && "text-primary bg-primary/5 font-semibold",
                !isDone && !isActive && "text-gray-400",
              )}
            >
              <span
                className={cn(
                  "w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 border-2 transition-all",
                  isDone && "bg-primary border-primary text-white",
                  isActive && "bg-primary border-primary text-white shadow-[0_0_0_3px_rgba(46,125,50,0.2)]",
                  !isDone && !isActive && "border-gray-300 text-gray-400",
                )}
              >
                {isDone ? <Check className="w-3.5 h-3.5" /> : i + 1}
              </span>
              {label}
            </div>
          </div>
        );
      })}
    </div>
  );
}
