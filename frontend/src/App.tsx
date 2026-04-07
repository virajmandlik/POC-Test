import { Routes, Route } from "react-router-dom";
import Layout from "@/components/Layout";
import Dashboard from "@/pages/Dashboard";
import LandRecords from "@/pages/LandRecords";
import PhotoVerification from "@/pages/PhotoVerification";
import FieldApp from "@/pages/FieldApp";
import Jobs from "@/pages/Jobs";
import AuditLogs from "@/pages/AuditLogs";
import Settings from "@/pages/Settings";
import { useUsername } from "@/hooks/useUsername";

export default function App() {
  const { username, setUsername } = useUsername();

  return (
    <Routes>
      <Route element={<Layout username={username} onUsernameChange={setUsername} />}>
        <Route index element={<Dashboard />} />
        <Route path="land-records" element={<LandRecords username={username} />} />
        <Route path="photo-verification" element={<PhotoVerification username={username} />} />
        <Route path="field-app" element={<FieldApp username={username} />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="audit" element={<AuditLogs />} />
        <Route path="settings" element={<Settings username={username} onUsernameChange={setUsername} />} />
      </Route>
    </Routes>
  );
}
