import { useEffect, useState } from "react";
import { BASE_URL, request } from "../../api/client";
import { Alert, Card } from "../../components/ui";

type Package = {
  identity?: string; status: string; duration_seconds?: number; content_locale?: string;
  files?: Record<string, { url: string; sample_rate?: number; mastering_metrics?: {
    status: string; after?: { integrated_lufs: number; true_peak_dbtp: number };
  } }>;
};

export function ManualPackagePanel({ url, status }: { url: string; status: string | null }) {
  const [data, setData] = useState<Package | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    request<Package>(url).then(value => { if (active) { setData(value); setError(null); } })
      .catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [url, status]);
  const audio = data?.files?.narration;
  return <Card title="Pacote para edição manual" className="span-full">
    {error && <Alert tone="danger">{error}</Alert>}
    {data?.identity ? <>
      <p>{data.duration_seconds?.toFixed(2)} s · {data.content_locale} · {audio?.sample_rate} Hz</p>
      <p>Mastering: {audio?.mastering_metrics?.status} · {audio?.mastering_metrics?.after?.integrated_lufs} LUFS · {audio?.mastering_metrics?.after?.true_peak_dbtp?.toFixed(2)} dBTP</p>
      <p><a href={`${BASE_URL}${url}/download`}>Baixar pacote ZIP</a></p>
      <p>{[["narration", "narration.wav"], ["captions.srt", "captions.srt"], ["markers.csv", "markers.csv"]].map(([key, label]) =>
        data.files?.[key] && <a key={key} style={{ marginRight: 20 }} href={`${BASE_URL}${data.files[key].url}`}>Baixar {label}</a>
      )}</p>
      <details><summary>Identidade e manifest</summary><code>{data.identity}</code><p><a href={`${BASE_URL}${url}`}>Consultar manifest</a></p></details>
    </> : <p>O pacote ficará disponível após validar narração, transcrição e arquivos.</p>}
  </Card>;
}
