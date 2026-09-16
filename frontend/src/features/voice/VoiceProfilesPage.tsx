import { useEffect, useRef, useState } from "react";
import { operatorApi, VoiceLanguage, VoicePreviewInput, VoiceProfiles } from "../../api/operator";
import { Alert, LoadingState, PageHeader, TableWrap } from "../../components/ui";

const initialText: Record<VoiceLanguage, string> = {
  Portuguese: "Este é um teste curto de narração para comparar o timbre, o ritmo e a naturalidade da voz.",
  English: "This is a short narration test to compare the voice, pacing, and overall naturalness.",
};
type Preview = VoicePreviewInput & { url: string };

export default function VoiceProfilesPage() {
  const [catalog, setCatalog] = useState<VoiceProfiles | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [language, setLanguage] = useState<VoiceLanguage>("Portuguese");
  const [text, setText] = useState(initialText.Portuguese);
  const [profile, setProfile] = useState("");
  const [busy, setBusy] = useState(false);
  const [previews, setPreviews] = useState<Preview[]>([]);
  const urls = useRef<string[]>([]);
  const pending = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    operatorApi.getVoiceProfiles(controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setCatalog(data);
      setProfile(data.defaults["pt-BR"]);
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Profiles indisponíveis");
    });
    return () => {
      controller.abort();
      pending.current?.abort();
      urls.current.forEach((url) => URL.revokeObjectURL(url));
      urls.current = [];
    };
  }, []);

  function changeLanguage(next: VoiceLanguage) {
    setLanguage(next);
    setText(initialText[next]);
    setProfile(catalog?.defaults[next === "English" ? "en-US" : "pt-BR"] || "");
  }

  async function generate(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !catalog?.profiles.some((item) => item.name === profile) || !text.trim()) return;
    const input = { voice_profile: profile, language, text };
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    try {
      const wav = await operatorApi.previewVoice(input, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(wav);
      urls.current.push(url);
      setPreviews((current) => [...current, { ...input, url }]);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Falha ao gerar preview");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const loaded = catalog?.profiles.some((item) => item.name === profile);
  return <div className="page workspace-stack">
    <PageHeader title="Voz" description="Escolha um profile e ouça previews curtos. A seleção é apenas para este teste." />
    {error && <Alert tone="danger">{error}</Alert>}
    {!catalog ? (!error && <LoadingState />) : <>
      <section className="card">
        <h2>Defaults de produção</h2>
        {(["pt-BR", "en-US"] as const).map((locale) => <p key={locale}>
          <strong>{locale === "pt-BR" ? "PT-BR" : "EN-US"} production:</strong> {catalog.defaults[locale]}
          {!catalog.profiles.some((item) => item.name === catalog.defaults[locale]) && " — referência ainda não disponível"}
        </p>)}
      </section>
      <section className="card">
        <h2>Profiles registrados</h2>
        <TableWrap><table><thead><tr><th>Nome</th><th>Clone mode</th><th>Referência</th><th>Versão</th><th>Identity</th></tr></thead>
          <tbody>{catalog.profiles.map((item) => <tr key={item.name}><td>{item.name}</td><td>{item.clone_mode}</td><td>{item.reference_id || "—"}</td><td>{item.version}</td><td>{item.identity.slice(0, 12)}</td></tr>)}</tbody>
        </table></TableWrap>
        {catalog.profiles.length === 0 && <p>Nenhum profile carregado.</p>}
      </section>
      <form className="card workspace-stack" onSubmit={generate}>
        <h2>Preview</h2>
        <div className="filters">
          <label>Idioma<select value={language} disabled={busy} onChange={(event) => changeLanguage(event.target.value as VoiceLanguage)}><option value="Portuguese">Português</option><option value="English">English</option></select></label>
          <label>Profile<select value={profile} disabled={busy} onChange={(event) => setProfile(event.target.value)}>
            {!loaded && <option value={profile}>{profile || "Selecione"} — referência ainda não disponível</option>}
            {catalog.profiles.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select></label>
        </div>
        <label>Texto<textarea rows={4} style={{ width: "100%" }} maxLength={800} required disabled={busy} value={text} onChange={(event) => setText(event.target.value)} /></label>
        <small>{text.length}/800 caracteres · narration_v2 · seed 303</small>
        <button type="submit" disabled={busy || !loaded || !text.trim()}>{busy ? "Gerando preview…" : "Gerar preview"}</button>
      </form>
    </>}
    {previews.length > 0 && <section className="workspace-stack"><h2>Previews desta sessão</h2>
      {previews.map((preview, index) => <article className="card" key={preview.url}>
        <h3>{index + 1}. {preview.voice_profile} · {preview.language}</h3>
        <p>{preview.text}</p>
        <audio controls preload="none" src={preview.url} aria-label={`Preview ${index + 1}: ${preview.voice_profile}`} />
      </article>)}
    </section>}
  </div>;
}
