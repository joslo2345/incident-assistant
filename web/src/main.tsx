import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { Layout } from "./components/Layout";
import "./index.css";
import { Audit } from "./pages/Audit";
import { Feedback } from "./pages/Feedback";
import { IncidentDetail } from "./pages/IncidentDetail";
import { Incidents } from "./pages/Incidents";
import { Login } from "./pages/Login";

function App() {
  const { me, loading } = useAuth();
  if (loading) return null;
  if (!me) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Incidents />} />
        <Route path="incidents/:id" element={<IncidentDetail />} />
        <Route path="audit" element={<Audit />} />
        <Route path="feedback" element={<Feedback />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
