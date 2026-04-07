import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";

interface Props {
  username: string;
  onUsernameChange: (v: string) => void;
}

export default function Layout({ username, onUsernameChange }: Props) {
  return (
    <div className="flex min-h-screen">
      <Sidebar username={username} onUsernameChange={onUsernameChange} />
      <main className="flex-1 p-6 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
