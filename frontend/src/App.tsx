import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import LegacyWorkflowRedirect from "./pages/LegacyWorkflowRedirect";
import HistoryPage from "./features/history/HistoryPage";
import VoiceProfilesPage from "./features/voice/VoiceProfilesPage";
import MusicLibraryPage from "./features/music/MusicLibraryPage";
import NarrationPage from "./features/narration/NarrationPage";
import PresentationPage from "./features/presentation/PresentationPage";
import ProductionsPage from "./features/productions/ProductionsPage";
import NewProductionPage from "./features/productions/NewProductionPage";
import ProgramAudioPage from "./features/program-audio/ProgramAudioPage";
import RenderPage from "./features/render/RenderPage";
import ReviewQueuePage from "./features/review/ReviewQueuePage";
import VisualsPage from "./features/visuals/VisualsPage";
import OverviewPage from "./features/workflow/OverviewPage";
import WorkflowWorkspace from "./features/workflow/WorkflowWorkspace";

export default function App() {
  return <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><AppShell><Routes>
    <Route path="/" element={<Navigate replace to="/dashboard" />} />
    <Route path="/dashboard" element={<Dashboard />} />
    <Route path="/productions" element={<ProductionsPage />} />
    <Route path="/productions/new" element={<NewProductionPage />} />
    <Route path="/productions/:workflowId" element={<WorkflowWorkspace />}>
      <Route index element={<Navigate replace to="overview" />} />
      <Route path="overview" element={<OverviewPage />} />
      <Route path="narration" element={<NarrationPage />} />
      <Route path="visuals" element={<VisualsPage />} />
      <Route path="presentation" element={<PresentationPage />} />
      <Route path="program-audio" element={<ProgramAudioPage />} />
      <Route path="render" element={<RenderPage />} />
      <Route path="history" element={<HistoryPage />} />
    </Route>
    <Route path="/review" element={<ReviewQueuePage />} />
    <Route path="/music" element={<MusicLibraryPage />} />
    <Route path="/voice" element={<VoiceProfilesPage />} />
    <Route path="/workflow/:id" element={<LegacyWorkflowRedirect />} />
    <Route path="*" element={<Navigate replace to="/dashboard" />} />
  </Routes></AppShell></BrowserRouter>;
}
