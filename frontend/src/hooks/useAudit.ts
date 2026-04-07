import { useQuery } from "@tanstack/react-query";
import { getAuditLogs } from "@/api/client";

export function useAuditLogs(params: Record<string, string | number> = {}) {
  return useQuery({
    queryKey: ["audit", params],
    queryFn: () => getAuditLogs(params),
  });
}
