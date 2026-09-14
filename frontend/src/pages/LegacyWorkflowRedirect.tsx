import { Navigate, useParams } from "react-router-dom";
import { legacyWorkflowTarget } from "../app/routes";

export default function LegacyWorkflowRedirect() {
  const { id = "" } = useParams();
  return <Navigate replace to={legacyWorkflowTarget(id)} />;
}
