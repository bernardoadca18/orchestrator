import { BrowserRouter as Router, Routes, Route, Link } from "react-router-dom";
import Home from "./pages/Home";
import WorkflowDetail from "./pages/WorkflowDetail";
import Dashboard from "./pages/Dashboard";

export default function App() {
  return (
    <Router>
      <nav style={{ fontFamily: "sans-serif", background: "#333", color: "#fff", padding: "1rem" }}>
        <Link to="/" style={{ color: "#fff", marginRight: "1rem", textDecoration: "none" }}>
          <strong>Orchestrator</strong>
        </Link>
        <Link to="/dashboard" style={{ color: "#fff", textDecoration: "none" }}>
          Dashboard
        </Link>
      </nav>

      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/workflow/:id" element={<WorkflowDetail />} />
        <Route path="/dashboard" element={<Dashboard />} />
      </Routes>
    </Router>
  );
}
