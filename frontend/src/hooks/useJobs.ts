import { useQuery } from "@tanstack/react-query";
import { listJobs, getJob } from "@/api/client";

export function useJobList(params: Record<string, string | number> = {}, refetchMs?: number) {
  return useQuery({
    queryKey: ["jobs", params],
    queryFn: () => listJobs(params),
    refetchInterval: refetchMs,
  });
}

export function useJob(jobId: string | null, poll = false) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => (jobId ? getJob(jobId) : null),
    enabled: !!jobId,
    refetchInterval: poll ? 1500 : undefined,
  });
}
