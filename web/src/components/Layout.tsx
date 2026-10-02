import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { Badge } from "./ui";

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded px-2.5 py-1.5 text-sm ${isActive ? "bg-slate-800 text-white" : "text-slate-300 hover:text-white"}`;

export function Layout() {
  const { me, logout } = useAuth();
  return (
    <div className="min-h-screen">
      <nav className="bg-slate-900">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-2.5">
          <span className="font-semibold text-white">GPU Incident Assistant</span>
          <div className="flex gap-1">
            <NavLink to="/" end className={link}>Incidents</NavLink>
            <NavLink to="/audit" className={link}>Approvals audit</NavLink>
            <NavLink to="/feedback" className={link}>Feedback</NavLink>
          </div>
          {me && (
            <div className="ml-auto flex items-center gap-3 text-sm text-slate-300">
              <span>{me.display_name}</span>
              <Badge tone={me.can_approve ? "green" : "slate"}>{me.role}</Badge>
              <button className="text-slate-400 hover:text-white" onClick={logout}>Log out</button>
            </div>
          )}
        </div>
      </nav>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
