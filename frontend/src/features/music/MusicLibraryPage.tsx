import { useEffect, useMemo, useState } from "react";
import { MusicAsset, operatorApi } from "../../api/operator";
import { Alert, DefinitionList, EmptyState, LoadingState, PageHeader, StatusBadge, TableWrap } from "../../components/ui";

export function filterMusicAssets(assets: MusicAsset[], query: string, mood: string, style: string, energy: string, rights: string) {
  const needle = query.trim().toLowerCase();
  return assets.filter((asset) => (!needle || asset.asset_id.includes(needle) || asset.source_owner.toLowerCase().includes(needle))
    && (!mood || asset.moods.includes(mood))
    && (!style || asset.styles.includes(style))
    && (!energy || (energy === "low" ? asset.energy < 0.34 : energy === "medium" ? asset.energy >= 0.34 && asset.energy < 0.67 : asset.energy >= 0.67))
    && (!rights || (rights === "monetizable" ? asset.monetization_allowed : asset.license_type === rights)));
}

export function selectedMusicAsset(assets: MusicAsset[], assetId: string) {
  return assets.find((asset) => asset.asset_id === assetId) || null;
}

export default function MusicLibraryPage() {
  const [assets, setAssets] = useState<MusicAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState(""); const [mood, setMood] = useState(""); const [style, setStyle] = useState(""); const [energy, setEnergy] = useState(""); const [rights, setRights] = useState("");
  useEffect(() => { const controller = new AbortController(); operatorApi.getMusicCatalog(controller.signal).then((data) => setAssets(data.assets)).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Catálogo indisponível"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, []);
  const moods = useMemo(() => [...new Set(assets.flatMap((asset) => asset.moods))].sort(), [assets]);
  const styles = useMemo(() => [...new Set(assets.flatMap((asset) => asset.styles))].sort(), [assets]);
  const filtered = filterMusicAssets(assets, query, mood, style, energy, rights);
  return <div className="page"><PageHeader title="Music Library" description="Catálogo validado do Program Audio. Paths locais e source_reference não são expostos." />
    <div className="filters"><label>Buscar<input value={query} onChange={(event) => setQuery(event.target.value)} /></label><label>Mood<select value={mood} onChange={(event) => setMood(event.target.value)}><option value="">todos</option>{moods.map((value) => <option key={value}>{value}</option>)}</select></label><label>Style<select value={style} onChange={(event) => setStyle(event.target.value)}><option value="">todos</option>{styles.map((value) => <option key={value}>{value}</option>)}</select></label><label>Energy<select value={energy} onChange={(event) => setEnergy(event.target.value)}><option value="">todas</option><option value="low">baixa</option><option value="medium">média</option><option value="high">alta</option></select></label><label>Direitos<select value={rights} onChange={(event) => setRights(event.target.value)}><option value="">todos</option><option value="monetizable">monetização permitida</option><option value="owned">owned</option><option value="commissioned">commissioned</option><option value="cc0">cc0</option><option value="royalty_free">royalty free</option></select></label></div>
    {error && <Alert tone="danger">{error}</Alert>}{loading ? <LoadingState /> : filtered.length === 0 ? <EmptyState title="Nenhuma faixa encontrada" /> : <TableWrap><table><thead><tr><th>Faixa</th><th>Duração / formato</th><th>Mood / style</th><th>Energy / loop</th><th>Loudness</th><th>Direitos</th><th>Preview</th></tr></thead><tbody>{filtered.map((asset) => <tr key={asset.asset_id}><td><strong>{asset.asset_id}</strong><small className="block-meta">{asset.source_owner}</small></td><td>{asset.duration_seconds.toFixed(1)}s · {asset.format}<small className="block-meta">{asset.sample_rate} Hz · {asset.channels} ch</small></td><td>{asset.moods.join(", ")}<small className="block-meta">{asset.styles.join(", ")}</small></td><td>{asset.energy.toFixed(2)} · {asset.loopable ? "loopable" : "sem loop"}</td><td>{asset.integrated_lufs.toFixed(1)} LUFS<small className="block-meta">{asset.true_peak_dbtp.toFixed(1)} dBTP</small></td><td><StatusBadge value={asset.monetization_allowed ? "monetization allowed" : "blocked"} /><DefinitionList items={[["Licença", asset.license_type], ["Attribution", asset.attribution_required ? asset.attribution_text || "obrigatória" : "não"]]} /></td><td><audio controls preload="none" src={operatorApi.musicPreviewUrl(asset)} /></td></tr>)}</tbody></table></TableWrap>}
  </div>;
}
