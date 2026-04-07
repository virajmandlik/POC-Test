import { NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";
import { useHealth } from "@/hooks/useHealth";
import { useJobList } from "@/hooks/useJobs";
import {
  BarChart3,
  FileText,
  Leaf,
  Smartphone,
  Zap,
  ClipboardList,
  Settings,
} from "lucide-react";

const NAV = [
  { to: "/", label: "Dashboard", icon: BarChart3 },
  { to: "/land-records", label: "Land Records", icon: FileText },
  { to: "/photo-verification", label: "Photo Verification", icon: Leaf },
  { to: "/field-app", label: "Field App", icon: Smartphone },
  { to: "/jobs", label: "Jobs", icon: Zap },
  { to: "/audit", label: "Audit Logs", icon: ClipboardList },
  { to: "/settings", label: "Settings", icon: Settings },
];

interface Props {
  username: string;
  onUsernameChange: (v: string) => void;
}

export default function Sidebar({ username, onUsernameChange }: Props) {
  const { data: health } = useHealth();
  const { data: jobData } = useJobList({ limit: 1 });

  const apiOk = health?.status === "ok";
  const counts = jobData?.counts;

  return (
    <aside className="w-64 min-h-screen bg-gradient-to-b from-bg-sidebar to-[#2E4A2E] border-r border-white/5 flex flex-col flex-shrink-0">
      {/* Brand */}
      <div className="px-5 pt-5 pb-2 text-center">
        <SidebarIcon />
        <p className="text-white font-bold text-lg mt-1">🌾 Digilekha</p>
        <p className="text-green-300 text-[0.7rem] pb-2 border-b border-white/10 mb-1">
          Digital Land Records &bull; Offline First
        </p>
        <div
          className={cn(
            "text-[0.7rem] px-2 py-1 rounded-lg text-center mt-1 mb-3",
            apiOk
              ? "bg-green-500/20 text-green-300"
              : "bg-red-500/20 text-red-300",
          )}
        >
          {apiOk ? "● API Connected" : "○ API Offline"}
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 space-y-0.5">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all text-green-200",
                isActive
                  ? "bg-green-400/20 border-l-[3px] border-green-400 text-white font-semibold"
                  : "hover:bg-white/[0.08]",
              )
            }
          >
            <Icon className="w-4 h-4 flex-shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Username */}
      <div className="px-4 py-3 border-t border-white/10">
        <label className="text-green-300 text-xs font-medium">👤 Username</label>
        <input
          type="text"
          value={username}
          onChange={(e) => onUsernameChange(e.target.value)}
          className="w-full mt-1 px-2.5 py-1.5 bg-white/10 border border-white/15 rounded-lg text-sm text-green-100 placeholder-green-300/50 focus:outline-none focus:ring-1 focus:ring-green-400"
        />
      </div>

      {/* Quick stats */}
      <div className="px-4 pb-4 text-green-300 text-xs space-y-0.5">
        {counts?.running ? (
          <p>🔵 <strong>{counts.running}</strong> running</p>
        ) : null}
        {counts?.failed ? (
          <p>🔴 <strong>{counts.failed}</strong> failed</p>
        ) : null}
        {!counts?.running && !counts?.failed && (
          <p className="opacity-60">No active jobs</p>
        )}
      </div>
    </aside>
  );
}

function SidebarIcon() {
  return (
    <svg viewBox="0 0 120 100" className="w-28 h-20 mx-auto">
      <ellipse cx="60" cy="88" rx="52" ry="8" fill="#2E7D32" opacity="0.25" />
      <line x1="22" y1="88" x2="22" y2="58" stroke="#66BB6A" strokeWidth="2.5" />
      <ellipse cx="22" cy="54" rx="5" ry="7" fill="#43A047" />
      <line x1="14" y1="88" x2="14" y2="64" stroke="#66BB6A" strokeWidth="2" />
      <ellipse cx="14" cy="61" rx="4" ry="5.5" fill="#388E3C" />
      <line x1="98" y1="88" x2="98" y2="58" stroke="#66BB6A" strokeWidth="2.5" />
      <ellipse cx="98" cy="54" rx="5" ry="7" fill="#43A047" />
      <line x1="106" y1="88" x2="106" y2="64" stroke="#66BB6A" strokeWidth="2" />
      <ellipse cx="106" cy="61" rx="4" ry="5.5" fill="#388E3C" />
      <circle cx="60" cy="30" r="10" fill="#A5D6A7" />
      <line x1="60" y1="40" x2="60" y2="68" stroke="#C8E6C9" strokeWidth="3" strokeLinecap="round" />
      <line x1="60" y1="50" x2="44" y2="60" stroke="#C8E6C9" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="60" y1="50" x2="76" y2="60" stroke="#C8E6C9" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="60" y1="68" x2="50" y2="86" stroke="#C8E6C9" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="60" y1="68" x2="70" y2="86" stroke="#C8E6C9" strokeWidth="2.5" strokeLinecap="round" />
      <ellipse cx="60" cy="22" rx="14" ry="4" fill="#F9A825" />
      <rect x="50" y="12" width="20" height="10" rx="4" fill="#F9A825" />
    </svg>
  );
}
