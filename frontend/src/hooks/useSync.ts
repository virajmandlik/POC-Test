import { useQuery } from "@tanstack/react-query";
import { getSyncStatus, getSyncOnline, getSyncRecent } from "@/api/client";

export function useSyncStatus() {
  return useQuery({
    queryKey: ["syncStatus"],
    queryFn: getSyncStatus,
    refetchInterval: 15_000,
  });
}

export function useSyncOnline() {
  return useQuery({
    queryKey: ["syncOnline"],
    queryFn: getSyncOnline,
    refetchInterval: 15_000,
  });
}

export function useSyncRecent(limit = 5) {
  return useQuery({
    queryKey: ["syncRecent", limit],
    queryFn: () => getSyncRecent(limit),
    refetchInterval: 30_000,
  });
}
