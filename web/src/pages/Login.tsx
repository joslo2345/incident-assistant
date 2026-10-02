import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { Button, ErrorNote } from "../components/ui";

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100">
      <form onSubmit={submit} className="w-80 space-y-4 rounded-lg bg-white p-6 shadow">
        <div>
          <h1 className="text-lg font-semibold">GPU Incident Assistant</h1>
          <p className="text-sm text-slate-500">Sign in to review incidents and approvals.</p>
        </div>
        <label className="block text-sm">
          <span className="text-slate-600">Username</span>
          <input className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5" value={username}
                 onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label className="block text-sm">
          <span className="text-slate-600">Password</span>
          <input type="password" className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5" value={password}
                 onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        <ErrorNote error={error} />
        <Button type="submit" tone="primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</Button>
      </form>
    </div>
  );
}
